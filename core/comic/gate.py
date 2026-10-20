"""core.comic.gate — sample gate and per-page decisions (§12, phase 5b).

A render batch larger than the sample is not issued until a person confirms
the sample, or passes an audited ``--yes``. The initial sample is 30 pages
and, when the book has at least two chunks, keeps a seat for a second chunk
so the boundary behaviour is actually in the sample.

Decisions are accept, redraw, or accept-and-flag. Accept and accept-and-flag
count as accepted. The remainder stays closed while any sample page is still
a redraw. Once every sample page is decided, the acceptance rate moves the
next sample size: multiply by 1.618 and round at or above 0.9, lock from 0.5
up to 0.9, halve below 0.5. A chapter length, when known, caps a grown sample
so it does not swallow the chapter.

The gate is a runtime input. It never enters an ``action_key``, ``h_env``, or
a fingerprint (§12 invariant 10). Repeating a pause at an unchanged boundary
appends no second stop record (§6).
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from core.comic.locks import exclusive_lock

logger = logging.getLogger(__name__)

SAMPLE_GATE_SIZE = 30
PHI = 1.618

_CHOICES = frozenset({"accept", "redraw", "accept_and_flag"})
_ACCEPTED = frozenset({"accept", "accept_and_flag"})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def chunk_index_of(key: str) -> int:
    """Chunk ordinal of a positional page or panel key (``c0000-p0000``)."""
    return int(key[1:5])


class GatePaused(Exception):
    """The remainder of the book is waiting on a sample decision.

    The page named by ``key`` was not rendered. A re-run pauses at the same
    key without issuing the call and without a second stop record.
    """

    def __init__(self, key: str) -> None:
        self.key = key
        super().__init__(f"awaiting_human at {key}")


class PageDecision(BaseModel):
    """One terminal decision on a sample page."""

    model_config = ConfigDict(extra="ignore")

    choice: str
    reason_tier: str | None = None


class ProjectGate(BaseModel):
    """One project's sample, persisted in the shared ``gate.json``."""

    model_config = ConfigDict(extra="ignore")

    sample_size: int
    sample_keys: list[str] = Field(default_factory=list)
    decisions: dict[str, PageDecision] = Field(default_factory=dict)
    released: bool = False
    yes: bool = False
    paused_key: str | None = None
    size_adjusted: bool = False


class GateFile(BaseModel):
    """Cross-project sample gates. Shared, like ``budget.json``."""

    model_config = ConfigDict(extra="ignore")

    projects: dict[str, ProjectGate] = Field(default_factory=dict)


def _spans(sample: list[str]) -> bool:
    return len({chunk_index_of(key) for key in sample}) >= 2


def _beyond_sample(sample: list[str], chunk_index: int, total_chunks: int, size: int) -> bool:
    """Whether ``chunk_index``'s next page must wait outside ``sample``.

    A sample of two or more pages keeps its last seat for a second chunk when
    the book has one. A one-page sample cannot span two chunks; it takes the
    first page only.
    """
    if size < 1:
        return True
    needs_second_chunk = total_chunks >= 2 and size >= 2 and not _spans(sample)
    if len(sample) >= size and not needs_second_chunk:
        return True
    if not needs_second_chunk or not sample:
        return False
    only_chunk = chunk_index_of(sample[0])
    same_chunk = chunk_index == only_chunk and not _spans(sample)
    return same_chunk and len(sample) >= size - 1


def select_sample(keys: list[str], size: int) -> list[str]:
    """The sample ``GateSession.allow`` would admit, walking ``keys`` in order."""
    if size < 1:
        return []
    seen: list[int] = []
    for key in keys:
        chunk = chunk_index_of(key)
        if chunk not in seen:
            seen.append(chunk)
    total = max(len(seen), 1)
    sample: list[str] = []
    for key in keys:
        if _beyond_sample(sample, chunk_index_of(key), total, size):
            continue
        sample.append(key)
    return sample


def adjust_sample_size(
    current: int,
    acceptance_rate: float,
    chapter_pages: int | None = None,
) -> int:
    """Next sample size from the acceptance-rate rule.

    The prose sequence (30, 48, 78) illustrates the golden-ratio step. The
    value used is ``round(n * 1.618)``, which is 49 for 30. A known chapter
    length caps a grown sample so the sample stays a sample.
    """
    if acceptance_rate >= 0.9:
        # The growth branch needs the same lower bound as the shrink branch: a
        # zero or negative size makes every page "beyond the sample" and the
        # gate can never release.
        grown = max(1, round(current * PHI))
        if chapter_pages is not None and chapter_pages >= 1 and grown >= chapter_pages:
            return chapter_pages
        return grown
    if acceptance_rate >= 0.5:
        return current
    return max(1, current // 2)


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as temp:
            temp.write(text)
            temp.flush()
            os.fsync(temp.fileno())
        os.replace(temp_name, path)
    except Exception:
        Path(temp_name).unlink(missing_ok=True)
        raise


def _append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _normalize_choice(choice: str) -> str:
    return choice.strip().lower().replace("-", "_")


def _quarantine(path: Path) -> Path:
    """Move a corrupt shared gate aside so it is preserved, not overwritten."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    target = path.with_name(f"{path.name}.corrupt-{stamp}")
    try:
        path.replace(target)
    except OSError:
        return path
    return target


def _read_gate(path: Path, *, audit_path: Path | None = None) -> GateFile:
    """Read a gate file; a corrupt file is quarantined, not fatal."""
    if not path.is_file():
        return GateFile()
    try:
        return GateFile.model_validate_json(path.read_text(encoding="utf-8"))
    except (ValidationError, json.JSONDecodeError) as exc:
        quarantined = _quarantine(path)
        logger.warning("corrupt sample gate at %s (%s); rebuilding", path, exc)
        if audit_path is not None:
            _append_jsonl(
                audit_path,
                {
                    "kind": "recover",
                    "file": str(path),
                    "quarantined": str(quarantined),
                    "error": type(exc).__name__,
                    "ts": _now_iso(),
                },
            )
        return GateFile()


class GateSession:
    """Decides which pages may render, and records the human's answer."""

    def __init__(
        self,
        *,
        data_dir: Path | None,
        output_dir: Path,
        project_id: str,
        store: GateFile,
        project: ProjectGate,
        path: Path,
    ) -> None:
        self.data_dir = data_dir
        self.output_dir = output_dir
        self.project_id = project_id
        self._store = store
        self.project = project
        self._path = path
        # All writers for this project's data directory share one lock, even
        # when the gate record itself lives in the project directory.
        root = data_dir if data_dir is not None else output_dir
        self._lock_path = root / ".gate.lock"

    @staticmethod
    def _path_for(data_dir: Path | None, output_dir: Path) -> Path:
        # No data dir means the project directory, so a test never touches
        # ~/.inkstone. The CLI passes the shared data dir.
        root = data_dir if data_dir is not None else output_dir
        return root / "gate.json"

    @staticmethod
    def _candidate_paths(data_dir: Path | None, output_dir: Path) -> list[Path]:
        """Every place a project's gate may live, shared first.

        The pipeline writes a project-local gate when no data dir is supplied;
        the CLI and GC read the shared file. Searching both keeps the reader
        and the writer on the same record without changing test isolation.
        """
        paths: list[Path] = []
        if data_dir is not None:
            paths.append(data_dir / "gate.json")
        paths.append(output_dir / "gate.json")
        seen: set[Path] = set()
        out: list[Path] = []
        for path in paths:
            resolved = path.resolve()
            if resolved not in seen:
                seen.add(resolved)
                out.append(path)
        return out

    @classmethod
    def _read(cls, path: Path, *, audit_path: Path | None = None) -> GateFile:
        return _read_gate(path, audit_path=audit_path)

    @classmethod
    def _load_existing(
        cls,
        *,
        data_dir: Path | None,
        output_dir: Path,
        project_id: str,
    ) -> GateSession | None:
        for path in cls._candidate_paths(data_dir, output_dir):
            if not path.is_file():
                continue
            store = cls._read(path, audit_path=output_dir / "runs.jsonl")
            project = store.projects.get(project_id)
            if project is None:
                continue
            return cls(
                data_dir=data_dir,
                output_dir=output_dir,
                project_id=project_id,
                store=store,
                project=project,
                path=path,
            )
        return None

    @classmethod
    def load(
        cls,
        *,
        data_dir: str | Path | None,
        output_dir: str | Path,
        project_id: str,
    ) -> GateSession | None:
        """Return the stored gate, or ``None`` when this project has none."""
        out = Path(output_dir)
        data = Path(data_dir) if data_dir is not None else None
        return cls._load_existing(data_dir=data, output_dir=out, project_id=project_id)

    @classmethod
    def open(
        cls,
        *,
        data_dir: str | Path | None,
        output_dir: str | Path,
        project_id: str,
        yes: bool = False,
        sample_size: int = SAMPLE_GATE_SIZE,
    ) -> GateSession:
        """Load the project gate, creating it at ``sample_size`` on first use.

        An existing project keeps its stored size, including a size the
        acceptance-rate rule already moved. ``yes`` is sticky: the escape
        hatch is audited once and a later resume does not stop again.
        """
        if sample_size < 1:
            raise ValueError("sample_gate_size must be >= 1")
        out = Path(output_dir)
        data = Path(data_dir) if data_dir is not None else None
        path = cls._path_for(data, out)
        path.parent.mkdir(parents=True, exist_ok=True)
        lock_root = data if data is not None else out
        with exclusive_lock(lock_root / ".gate.lock"):
            session = cls._load_existing(data_dir=data, output_dir=out, project_id=project_id)
            if session is None:
                store = cls._read(path, audit_path=out / "runs.jsonl")
                project = ProjectGate(sample_size=sample_size)
                store.projects[project_id] = project
                session = cls(
                    data_dir=data,
                    output_dir=out,
                    project_id=project_id,
                    store=store,
                    project=project,
                    path=path,
                )
                # Persist the new gate immediately: a budget or continuity
                # pause before the first page must not lose the configured
                # sample size.
                session._save()
            if yes and not session.project.yes:
                session.project.yes = True
                session.project.paused_key = None
                session._append(
                    {
                        "kind": "gate",
                        "yes": True,
                        "ts": _now_iso(),
                    }
                )
                session._save()
            return session

    @property
    def awaiting(self) -> bool:
        """True when a page was held and neither ``--yes`` nor acceptance released it."""
        return (
            self.project.paused_key is not None
            and not self.project.released
            and not self.project.yes
        )

    def allow(self, key: str, chunk_index: int, *, total_chunks: int) -> bool:
        """Whether ``key`` may be rendered now.

        A held page is skipped by the caller. The pause itself is raised once
        the run has finished the sample, so a later chunk can still take the
        reserved seat.
        """
        with exclusive_lock(self._lock_path):
            self._refresh()
            if self.project.yes or self.project.released:
                return True
            decision = self.project.decisions.get(key)
            if decision is not None and decision.choice == "redraw":
                return True
            if key in self.project.sample_keys:
                return True
            beyond = _beyond_sample(
                self.project.sample_keys,
                chunk_index,
                total_chunks,
                self.project.sample_size,
            )
            if beyond:
                self._hold(key)
                return False
            self.project.sample_keys.append(key)
            self._save()
            return True

    def record(
        self,
        key: str,
        choice: str,
        *,
        reason_tier: str | None = None,
        state=None,
        chapter_pages: int | None = None,
    ) -> ProjectGate:
        """Store one decision. A redraw marks ``state.stale_pages`` when given.

        ``chapter_pages``, when known, caps a grown sample so it does not
        cover that whole chapter.
        """
        normalized = _normalize_choice(choice)
        if normalized not in _CHOICES:
            raise ValueError(f"unknown gate decision: {choice}")
        with exclusive_lock(self._lock_path):
            self._refresh()
            if key not in self.project.sample_keys:
                raise ValueError(f"{key} is not in the sample")
            self.project.decisions[key] = PageDecision(choice=normalized, reason_tier=reason_tier)
            if normalized == "redraw" and state is not None and key not in state.stale_pages:
                state.stale_pages.append(key)
            self._close_sample(chapter_pages)
            self._save()
            return self.project

    def _close_sample(self, chapter_pages: int | None) -> None:
        keys = self.project.sample_keys
        if not keys or any(key not in self.project.decisions for key in keys):
            return
        accepted = sum(1 for key in keys if self.project.decisions[key].choice in _ACCEPTED)
        rate = accepted / len(keys)
        redraws = [key for key in keys if self.project.decisions[key].choice == "redraw"]
        was_adjusted = self.project.size_adjusted
        if not was_adjusted:
            self.project.sample_size = adjust_sample_size(
                self.project.sample_size,
                rate,
                chapter_pages=chapter_pages,
            )
            self.project.size_adjusted = True
        if not redraws:
            self.project.released = True
            self.project.paused_key = None
        if not was_adjusted:
            self._append(
                {
                    "kind": "gate",
                    "acceptance_rate": rate,
                    "reason_tiers": self._reason_tiers(),
                    "sample_size": self.project.sample_size,
                    "ts": _now_iso(),
                }
            )

    def _reason_tiers(self) -> dict[str, int]:
        tiers: dict[str, int] = {}
        for key in self.project.sample_keys:
            decision = self.project.decisions.get(key)
            if decision is None or decision.choice != "redraw":
                continue
            tier = decision.reason_tier or "page"
            tiers[tier] = tiers.get(tier, 0) + 1
        return tiers

    def _refresh(self) -> None:
        """Re-read this project's gate under the lock; keep the newest record."""
        store = _read_gate(self._path, audit_path=self.output_dir / "runs.jsonl")
        latest = store.projects.get(self.project_id)
        if latest is not None:
            self.project = latest
        self._store = store

    def _hold(self, key: str) -> None:
        if self.project.paused_key is None:
            self._append(
                {
                    "kind": "stop",
                    "key": key,
                    "stage": "render",
                    "reason": "awaiting_human",
                    "ts": _now_iso(),
                }
            )
            self.project.paused_key = key
        self._save()

    def _append(self, record: dict) -> None:
        _append_jsonl(self.output_dir / "runs.jsonl", record)

    def _save(self) -> None:
        self._store.projects[self.project_id] = self.project
        _atomic_write(self._path, self._store.model_dump_json(indent=2))
