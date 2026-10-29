"""core.pipelines.progress — how far a resumed project has come.

Progress is derived, never stored: a panel counts as finished when the projection
records it, a page when its lettered file is recorded. Nothing here calls a
provider, and nothing enters a key, so the number can change without invalidating
work.

The fraction is deliberately capped below 1.0 until the run reports done. A bar
that reads 100% while pages are still being lettered is the kind of number that
makes a person stop watching.

Planning counts. ``extract``, ``bible``, ``portrait`` and ``page_plan`` can run
for many minutes before a single image exists; a bar pinned at 0.0 through all
of them is indistinguishable from a hang. Planning therefore earns a small
share of the bar (``_PLANNING_SHARE``) and rendering owns the rest, so more
work always moves the number forward and never backwards.
"""

from __future__ import annotations

from core.density import PANELS_PER_CHUNK_B
from core.schemas import ProjectState

# The tier-B planning budget, used only as a denominator when a project has
# no measured counts yet. Bound to density rather than restated as 8.
_DEFAULT_PANELS_PER_CHUNK = PANELS_PER_CHUNK_B

# Fraction of the *pre-layout* bar earned before any image is drawn. The
# pre-layout budget is 0.9 (the last 0.1 belongs to layout/export), so planning
# takes a slice of that 0.9 rather than sitting on top of it: a fully rendered
# project must still read 0.90, never higher, until layout actually runs.
_PLANNING_SHARE = 0.10
_RENDER_CEILING = 0.9

# Sub-weights inside ``_PLANNING_SHARE``. They sum to 1.0 and describe how far
# along the planning half of the run is: extraction first, then the visual
# bible, then canon portraits, then the per-chunk page/storyboard plan.
_PLANNING_WEIGHTS = {
    "extract": 0.15,
    "bible": 0.30,
    "portrait": 0.20,
    "plan": 0.35,
}


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


def _planning_fraction(state: ProjectState) -> float:
    """How far the planning half of the run has come, in ``[0, 1]``.

    Derived from the checkpoint only: characters extracted, the visual bible
    built, canon portraits drawn, and page/storyboard plans stored. Each step
    is capped at its weight so a partially finished stage still counts.
    """
    earned = 0.0
    if state.characters:
        earned += _PLANNING_WEIGHTS["extract"]
    if state.visual_bible is not None:
        earned += _PLANNING_WEIGHTS["bible"]

    # Canon list: the bible is authoritative once it exists; otherwise the
    # extracted character map stands in. A checkpoint can record drawn
    # portraits while both maps are empty (legacy or partially written state),
    # so fall back to the drawn keys themselves — artwork on disk is progress
    # even when its registry is missing.
    canon_names = set(state.visual_bible.characters) if state.visual_bible else set()
    if not canon_names:
        canon_names = set(state.characters)
    drawn_names = set(state.generated.portraits)
    if drawn_names:
        denominator = len(canon_names) or len(drawn_names)
        finished = len(drawn_names & canon_names) if canon_names else len(drawn_names)
        earned += _PLANNING_WEIGHTS["portrait"] * min(1.0, finished / denominator)

    if state.page_cache or state.beat_cache or state.chunk_cache:
        earned += _PLANNING_WEIGHTS["plan"]
    return min(1.0, earned)


def estimate_progress(state: ProjectState, total_chunks: int | None = None) -> float:
    """Estimate overall completion in ``[0, 1)`` from checkpointed planning + renders.

    Used so a resume does not reset the UI progress bar to near-zero. Planning
    earns ``_PLANNING_SHARE`` and rendering the remaining 0.9, so the bar moves
    from the first extracted character onward while still reserving the last
    10% for layout/export.
    """
    if state.render_mode == "finished_page":
        finished, planned = page_progress_counts(state, total_chunks)
    else:
        finished, planned = panel_progress_counts(state, total_chunks)
    rendered = 0.0 if planned <= 0 else min(1.0, finished / planned)
    # Planning and rendering split the pre-layout budget: planning earns
    # ``_PLANNING_SHARE`` of it up front, rendering earns the remainder. A
    # finished book is therefore exactly at ``_RENDER_CEILING`` before layout.
    planning = _PLANNING_SHARE * _planning_fraction(state)
    rendering = (_RENDER_CEILING - _PLANNING_SHARE) * rendered
    return min(_RENDER_CEILING, planning + rendering)
