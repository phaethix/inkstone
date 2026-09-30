"""core.comic.prune — minimal reclaimer over generated image artifacts (§7, phase 0g).

The CAS and ``index/`` do not exist yet (Phases 2-3); today every artifact is
written at a deterministic path and overwritten in place, so the set of files a
resumable run still needs is exactly the set of paths the live state references.
This module deletes the complement, but only when it is also old enough:

    delete only when the reference count is zero AND the object is older than a
    threshold (content-addressed design §7; resolved item 3 in §13).

Both conditions are required. ``collect_live_refs`` is the seam Phase 4's ``gc``
extends with manifest ``outputs``, gate artifacts, and ``supersedes`` chains.
"""

from __future__ import annotations

import re
from datetime import timedelta
from pathlib import Path

from core.comic.ledger import ConsistencyLedger
from core.schemas import ProjectState

_OLDER_THAN_RE = re.compile(r"^(\d+)([dh]?)$")


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