"""core.pipelines.progress — how far a resumed project has come.

Progress is derived, never stored: a panel counts as finished when the projection
records it, a page when its lettered file is recorded. Nothing here calls a
provider, and nothing enters a key, so the number can change without invalidating
work.

The fraction is deliberately capped below 1.0 until the run reports done. A bar
that reads 100% while pages are still being lettered is the kind of number that
makes a person stop watching.
"""

from __future__ import annotations

from core.density import PANELS_PER_CHUNK_B
from core.schemas import ProjectState

# The tier-B planning budget, used only as a denominator when a project has
# no measured counts yet. Bound to density rather than restated as 8.
_DEFAULT_PANELS_PER_CHUNK = PANELS_PER_CHUNK_B


def panel_progress_counts(state: ProjectState, total_chunks: int | None = None) -> tuple[int, int]:
    """Return ``(finished, planned)`` panel counts for progress display."""
    if total_chunks is None:
        keys = {int(k) for k in state.chunk_cache if str(k).isdigit()}
        keys.update(int(k) for k in state.skipped_chunks if str(k).isdigit())
        total_chunks = (max(keys) + 1) if keys else 1
    total_chunks = max(1, total_chunks)

    planned = 0
    accounted: set[str] = set()
    for key in state.skipped_chunks:
        accounted.add(str(key))
    panel_counts: list[int] = []
    for key, cache in state.chunk_cache.items():
        if cache.storyboard is not None:
            n = len(cache.storyboard.panels)
            planned += n
            panel_counts.append(n)
            accounted.add(str(key))

    avg = (
        sum(panel_counts) / len(panel_counts) if panel_counts else float(_DEFAULT_PANELS_PER_CHUNK)
    )
    for i in range(total_chunks):
        if str(i) not in accounted:
            planned += avg

    finished = len(state.panels_done) + len(state.skipped)
    planned_i = max(finished, int(round(planned)))
    return finished, planned_i


def page_progress_counts(state: ProjectState, total_chunks: int | None = None) -> tuple[int, int]:
    """Return ``(finished, planned)`` finished-page counts for progress display."""
    if total_chunks is None:
        keys = {int(k) for k in state.page_cache if str(k).isdigit()}
        keys.update(int(k) for k in state.skipped_chunks if str(k).isdigit())
        total_chunks = (max(keys) + 1) if keys else 1
    total_chunks = max(1, total_chunks)

    planned = 0
    accounted: set[str] = set()
    for key in state.skipped_chunks:
        accounted.add(str(key))
    page_counts: list[int] = []
    for key, pageset in state.page_cache.items():
        n = len(pageset.pages)
        planned += n
        page_counts.append(n)
        accounted.add(str(key))

    avg = sum(page_counts) / len(page_counts) if page_counts else float(_DEFAULT_PANELS_PER_CHUNK)
    for i in range(total_chunks):
        if str(i) not in accounted:
            planned += avg

    finished = len(state.pages_done) + len(state.skipped_pages)
    planned_i = max(finished, int(round(planned)))
    return finished, planned_i


def estimate_progress(state: ProjectState, total_chunks: int | None = None) -> float:
    """Estimate overall completion in ``[0, 1)`` from checkpointed panels/pages.

    Used so a resume does not reset the UI progress bar to near-zero. Reserves
    the last 10% for layout/export.
    """
    if state.render_mode == "finished_page":
        finished, planned = page_progress_counts(state, total_chunks)
    else:
        finished, planned = panel_progress_counts(state, total_chunks)
    if planned <= 0:
        return 0.0
    return min(0.95, 0.9 * finished / planned)
