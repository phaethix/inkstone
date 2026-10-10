"""core.comic.gc — reclaim unreferenced content-addressed objects (§7, phase 4).

``prune`` still owns deterministic image paths. This module owns ``cas/``.
A blob stays when a manifest names it as an input or an output, when a
non-ok manifest lists it in ``supersedes``, when the ledger stores its
content hash or a path inside ``cas/``, or when ``gate.json`` names it.
Anything else is reclaimable only after it is also older than the threshold.
"""

from __future__ import annotations

import json
import time
from datetime import timedelta
from pathlib import Path

from core.comic.cas import Manifest
from core.comic.ledger import ConsistencyLedger
from core.comic.prune import PruneCandidate, PrunePlan, PruneResult, apply_prune

_HEX = frozenset("0123456789abcdef")


class GcError(Exception):
    """A root file could not be read, so nothing may be reclaimed."""


def _bare(content_id: str) -> str | None:
    digest = content_id.removeprefix("sha256:")
    if len(digest) == 64 and all(char in _HEX for char in digest):
        return digest
    return None


def _ids_in(value: object) -> set[str]:
    found: set[str] = set()
    if isinstance(value, str):
        digest = _bare(value)
        if digest is not None and value.startswith("sha256:"):
            found.add(digest)
    elif isinstance(value, dict):
        for item in value.values():
            found.update(_ids_in(item))
    elif isinstance(value, list):
        for item in value:
            found.update(_ids_in(item))
    return found


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def live_content_ids(root: Path) -> set[str]:
    """Bare digests gc must keep. Unreadable roots raise instead of looking empty."""
    root = Path(root)
    live: set[str] = set()
    index = root / "index"
    if index.is_dir():
        for path in sorted(index.glob("*.json")):
            try:
                manifest = Manifest.model_validate_json(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                raise GcError(f"unreadable manifest {path.name}") from exc
            for item in manifest.outputs:
                digest = _bare(item.content)
                if digest is not None:
                    live.add(digest)
            for content_id in manifest.inputs:
                digest = _bare(content_id)
                if digest is not None:
                    live.add(digest)
            if manifest.outcome != "ok":
                for content_id in manifest.supersedes:
                    digest = _bare(content_id)
                    if digest is not None:
                        live.add(digest)

    ledger_path = root / "consistency.json"
    if ledger_path.is_file():
        try:
            ledger = ConsistencyLedger.model_validate_json(ledger_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise GcError("unreadable consistency.json") from exc
        cas_root = root / "cas"
        for entry in ledger.characters.values():
            if entry.reference.content_hash:
                digest = _bare(entry.reference.content_hash)
                if digest is not None:
                    live.add(digest)
            if not entry.reference.path:
                continue
            pointed = Path(entry.reference.path)
            if not pointed.is_absolute():
                pointed = root / pointed
            if pointed.suffix != ".bin" or not _is_within(pointed, cas_root):
                continue
            digest = _bare(pointed.stem)
            if digest is not None:
                live.add(digest)

    gate_path = root / "gate.json"
    if gate_path.is_file():
        try:
            payload = json.loads(gate_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise GcError("unreadable gate.json") from exc
        live.update(_ids_in(payload))
    return live


def plan_gc(root: Path, older_than: timedelta, *, now: float | None = None) -> PrunePlan:
    """Objects under ``cas/`` that nothing names and that are old enough."""
    root = Path(root)
    live = live_content_ids(root)
    cutoff = (now if now is not None else time.time()) - older_than.total_seconds()
    plan = PrunePlan()
    cas_root = root / "cas"
    if not cas_root.is_dir():
        return plan
    for path in sorted(cas_root.glob("*/*.bin")):
        try:
            stat = path.stat()
        except FileNotFoundError:
            continue
        if not _is_within(path, cas_root):
            continue
        if _bare(path.stem) is None or path.stem in live:
            continue
        if stat.st_mtime >= cutoff:
            continue
        plan.candidates.append(
            PruneCandidate(path=path, size_bytes=stat.st_size, mtime=stat.st_mtime)
        )
    return plan


def apply_gc(plan: PrunePlan) -> PruneResult:
    """Delete every planned object. A file that vanished first is still counted."""
    return apply_prune(plan)
