"""core.pipelines.stage_runner — one execution unit per stage.

§9 states the per-stage runner contract as four properties. This module is the
single place they are encoded, so a stage whose body is already manifest-backed
can be executed through the runner instead of by an inline block in the pipeline:

1. The action key is **passed in**, never derived here. A runner with its own
   derivation would create the second key semantics §8 invariant 3 forbids.
2. A key hit issues no external call and returns the manifest's outputs.
3. The manifest is written under the ``manifest_stage`` the registry declares,
   never under a stage string the caller chose.
4. Nothing outside the stage's own outputs and its manifest is touched, so two
   runners cannot corrupt the projection by running concurrently.

Property 3 is why the public functions take a *spec name* (``"webtoon"``) rather
than a manifest stage (``"export"``): the mapping is read from
``stage_registry`` so it cannot drift from the catalog that ``verify`` uses.

The module is metadata plus CAS calls. It imports no pipeline code, so it can be
called from the pipeline, the CLI, and a future subprocess alike.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from core.comic.cas import read_manifest_entry, record_files, stored_files
from core.pipelines.stage_registry import stage_spec

# What a produce callback returns: the files the stage made, in manifest order.
Produced = Iterable[tuple[str, bytes]]


@dataclass(frozen=True)
class StageRun:
    """One stage's result, whether replayed from CAS or freshly produced.

    ``executed`` is the only field that implies external calls happened. A run
    that is ``replayed`` or ``tombstoned`` reached the provider zero times, which
    is what makes "resume costs nothing" checkable rather than asserted.
    """

    stage: str
    manifest_stage: str
    key: str
    files: tuple[tuple[str, bytes], ...] = ()
    replayed: bool = False
    executed: bool = False
    tombstoned: bool = False

    @property
    def settled(self) -> bool:
        """True when the stage needs no provider call and no further recording."""
        return self.replayed or self.tombstoned

    @property
    def external_calls(self) -> int:
        """How many provider calls this run made. A runner must keep this at 0 or 1."""
        return 1 if self.executed else 0


def manifest_stage_for(stage: str) -> str:
    """The manifest stage a runner writes under, resolved from the registry.

    An unknown stage must fail here rather than invent an artifact namespace.
    """
    return stage_spec(stage).manifest_stage


def replay(root: Path, stage: str, key: str) -> StageRun | None:
    """Return a settled run for ``key``, or ``None`` when the stage must produce.

    A non-ok manifest is settled as well: a tombstone stops the stage without a
    provider call, which is contract property 2 applied to the rejected side.
    """
    _require_key(key)
    manifest_stage = manifest_stage_for(stage)
    recorded = read_manifest_entry(root, key, stage=manifest_stage)
    if recorded is not None and recorded.outcome != "ok":
        return StageRun(stage, manifest_stage, key, tombstoned=True)
    files = stored_files(root, key, stage=manifest_stage)
    if files is None:
        return None
    return StageRun(stage, manifest_stage, key, files=tuple(files), replayed=True)


def record(
    root: Path,
    stage: str,
    key: str,
    files: Produced,
    *,
    stage_src: str,
    env: str,
    inputs: list[str],
    executed: bool = False,
) -> StageRun:
    """Store ``files`` under the registry's manifest stage and return the run.

    An empty result is not recorded. Writing an ``ok`` manifest with no outputs
    would claim a hit that ``stored_files`` can never satisfy, turning a failed
    stage into a permanent silent skip.
    """
    _require_key(key)
    manifest_stage = manifest_stage_for(stage)
    stored = tuple(files)
    if not stored:
        return StageRun(stage, manifest_stage, key, executed=executed)
    record_files(
        root,
        key=key,
        stage=manifest_stage,
        stage_src=stage_src,
        env=env,
        inputs=list(inputs),
        files=list(stored),
    )
    return StageRun(stage, manifest_stage, key, files=stored, executed=executed)


def run_stage(
    root: Path,
    stage: str,
    key: str,
    produce: Callable[[], Produced],
    *,
    stage_src: str,
    env: str,
    inputs: list[str],
) -> StageRun:
    """Replay ``key`` if it is settled, else run ``produce`` once and record it.

    ``produce`` is called at most once, and never when the key is settled. The
    key reaches the manifest unchanged, so the runner and the in-process path
    cannot disagree about whether a key hit.
    """
    settled = replay(root, stage, key)
    if settled is not None:
        return settled
    return record(
        root,
        stage,
        key,
        produce(),
        stage_src=stage_src,
        env=env,
        inputs=inputs,
        executed=True,
    )


def _require_key(key: str) -> None:
    if not key or not key.strip():
        raise ValueError(
            "a stage runner receives its action key; an empty key would make every run a hit"
        )
