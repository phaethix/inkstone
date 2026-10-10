"""core.comic.prune — minimal reclaimer over generated image artifacts (§7, phase 0g).

Content-addressed objects are reclaimed by ``core.comic.gc``. This module
covers the deterministic paths a resumable run still names in ``state.json``
and the ledger, and deletes the complement only when it is also old enough:

    delete only when the reference count is zero AND the object is older than a
    threshold (content-addressed design §7; resolved item 3 in §13).

Both conditions are required. ``collect_live_refs`` is the seam Phase 4's ``gc``
extends with manifest ``outputs``, gate artifacts, and ``supersedes`` chains.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

from core.comic.ledger import ConsistencyLedger
from core.schemas import ProjectState

_OLDER_THAN_RE = re.compile(r"^(\d+)([dh]?)$")

# Only these globs are ever candidates; everything else (comic.pdf, webtoon.png,
# state.json, consistency.json, logs) is structurally out of scope.
_ASSET_GLOBS = (
    "panels/*.png",
    "assets/portraits/*.png",
    "pages/*.png",
    "pages/blank/*.png",
)


def parse_older_than(text: str) -> timedelta:
    """Parse ``<N>d`` / ``<N>h`` / bare ``<N>`` (days) into a positive timedelta."""
    match = _OLDER_THAN_RE.match((text or "").strip())
    if match is None:
        raise ValueError(f"invalid --older-than value: {text!r}")
    amount = int(match.group(1))
    unit = match.group(2)
    if amount <= 0:
        raise ValueError(f"--older-than must be positive: {text!r}")
    return timedelta(hours=amount) if unit == "h" else timedelta(days=amount)


def collect_live_refs(state: ProjectState, ledger: ConsistencyLedger) -> set[Path]:
    """Every artifact path the authoritative state still points at.

    This is the reference-count side of the retention rule. The ledger is
    included because it is authoritative product intent and one of ``gc``'s
    explicit root sets (design §8 invariant 6d); omitting it would let a single
    prune delete the only copy of a character's reference image.
    """
    refs: set[Path] = set()
    for panel in state.generated.panels.values():
        if panel.local:
            refs.add(Path(panel.local).resolve())
    for portrait in state.generated.portraits.values():
        if portrait:
            refs.add(Path(portrait).resolve())
    for page in state.generated.pages.values():
        if page.local:
            refs.add(Path(page.local).resolve())
        if page.blank_local:
            refs.add(Path(page.blank_local).resolve())
    for asset in state.characters.values():
        if asset.portrait_local:
            refs.add(Path(asset.portrait_local).resolve())
    for entry in ledger.characters.values():
        if entry.reference.path:
            refs.add(Path(entry.reference.path).resolve())
    return refs


@dataclass
class PruneCandidate:
    """One reclaimable file."""

    path: Path
    size_bytes: int
    mtime: float


@dataclass
class PrunePlan:
    """The outcome of planning: what would be deleted, and how much it weighs."""

    candidates: list[PruneCandidate] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.candidates)

    def __len__(self) -> int:
        return len(self.candidates)

    @property
    def total_bytes(self) -> int:
        return sum(candidate.size_bytes for candidate in self.candidates)


@dataclass
class PruneResult:
    """What an apply actually reclaimed."""

    deleted: int = 0
    reclaimed_bytes: int = 0


def _is_within(path: Path, root: Path) -> bool:
    """True when ``path`` resolves inside ``root`` (mirrors the pipeline helper)."""
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def plan_prune(output_dir: Path, older_than: timedelta, *, now: float | None = None) -> PrunePlan:
    """Compute the reclaimable set: unreferenced AND older than ``older_than``.

    An absent ``state.json`` is a hard error, never "everything is unreferenced":
    a missing state must not authorise a mass delete.
    """
    output_dir = Path(output_dir)
    state_path = output_dir / "state.json"
    if not state_path.is_file():
        raise FileNotFoundError(state_path)
    state = ProjectState.load(state_path)
    state.migrate_legacy_page_keys()
    ledger = ConsistencyLedger.load_or_rebuild(output_dir / "consistency.json", state)
    live = collect_live_refs(state, ledger)

    cutoff = (now if now is not None else time.time()) - older_than.total_seconds()
    plan = PrunePlan()
    for pattern in _ASSET_GLOBS:
        for path in sorted(output_dir.glob(pattern)):
            try:
                stat = path.stat()
            except FileNotFoundError:
                continue
            if not _is_within(path, output_dir):
                continue
            if path.resolve() in live:
                continue
            if stat.st_mtime >= cutoff:
                continue
            plan.candidates.append(
                PruneCandidate(path=path, size_bytes=stat.st_size, mtime=stat.st_mtime)
            )
    return plan


def apply_prune(plan: PrunePlan) -> PruneResult:
    """Delete every planned candidate; idempotent when a file vanished first."""
    result = PruneResult()
    for candidate in plan.candidates:
        try:
            candidate.path.unlink()
        except FileNotFoundError:
            pass
        result.deleted += 1
        result.reclaimed_bytes += candidate.size_bytes
    return result
