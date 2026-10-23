"""core.pipelines.resume — what a resumed run may skip, and what it must redo.

Every function here answers one question about work already recorded, and none
of them calls a provider. They were inline in the pipeline body, where the same
three-way rule (stale wins, then done or skipped, else work) was written out four
times and never tested on its own.

The rule is deliberately asymmetric:

- **stale wins over done.** A page marked for redraw must be redone even though a
  projection record still says it finished. Anything else makes an alias merge a
  no-op.
- **skipped is not stale.** A content-policy rejection is a terminal answer about
  the source text, which did not change; retrying it only burns quota.
- **missing beats recorded.** A record with no file behind it is not done. That
  check lives with the file sweep in ``reconcile``, not here.
"""

from __future__ import annotations

from pathlib import Path

from core.comic.artifact_projection import drop_page_artifact
from core.comic.lettering_lang import scrub_lettering_panel
from core.schemas import ProjectState


def panel_needs_generation(state: ProjectState, state_key: str) -> bool:
    """True when a panel must be (re)generated this run."""
    if state_key in state.stale_panels:
        return True
    if state_key in state.panels_done or state_key in state.skipped:
        return False
    return True


def page_needs_generation(state: ProjectState, state_key: str) -> bool:
    """True when a finished page must be (re)generated this run."""
    if state_key in state.stale_pages:
        return True
    if state_key in state.pages_done or state_key in state.skipped_pages:
        return False
    return True


def drop_pages_outside_plan(state: ProjectState, *, chunk_index: int, page_count: int) -> None:
    """Remove obsolete positional page projections from one replanned chunk.

    Only the current chunk's ``cNNNN-pNNNN`` keys past its new page range are
    derived artifacts. Stale markers, tombstones, and other chunks' state stay
    untouched so a shorter plan cannot discard a human-visible decision.
    """
    prefix = f"c{chunk_index:04d}-p"
    known = set(state.generated.pages) | set(state.pages_done)
    for page_key in sorted(known):
        suffix = page_key.removeprefix(prefix)
        if (
            not page_key.startswith(prefix)
            or len(suffix) != 4
            or not suffix.isdecimal()
            or int(suffix) < page_count
        ):
            continue
        drop_page_artifact(state, page_key)


def scrub_chunk_lettering(state: ProjectState, key: str, chunk: str) -> bool:
    """Drop stored lettering that is not a contiguous span of this chunk."""
    changed = False
    pageset = state.page_cache.get(key)
    if pageset is not None:
        for page in pageset.pages:
            for panel in page.panels:
                changed = scrub_lettering_panel(panel, chunk) or changed
    cached = state.chunk_cache.get(key)
    board = cached.storyboard if cached is not None else None
    if board is not None:
        for panel in board.panels:
            changed = scrub_lettering_panel(panel, chunk) or changed
    return changed


def stamp_action_key(state: ProjectState, state_path: Path, target, key: str) -> bool:
    """Stamp a cached plan's missing action key and persist it immediately.

    A resume path changes only this stamp. Waiting for the end-of-run save loses
    it on a budget/continuity pause or a crash, and the next resume re-derives
    it. Persist at the mutation, as storyboard already does.
    """
    if target is None or target.action_key:
        return False
    target.action_key = key
    state.save(state_path)
    return True
