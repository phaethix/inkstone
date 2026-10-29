"""tests/test_progress_planning_weight.py — the bar must move during planning.

Bug: ``estimate_progress`` divided only by rendered panels/pages, so a project
whose planning stages (extract / bible / portrait / page_plan) had finished but
had not yet drawn a single image reported exactly 0.0 — indistinguishable from
a hang. ``comic_out/1f61fb46a59a`` sat at 0% for 18 minutes for this reason.
"""

from core.pipelines.progress import estimate_progress
from core.schemas import (
    ChunkCache,
    ComicPagePlan,
    ComicPagePlanSet,
    Panel,
    ProjectState,
    Storyboard,
)


def _planned_state(pages: int = 3, *, render_mode: str = "finished_page") -> ProjectState:
    """A state whose page plan exists but no page has been drawn yet."""
    pageset = ComicPagePlanSet(
        unit_id="0",
        pages=[ComicPagePlan(page_id=f"c0000-p{i:04d}", summary="s") for i in range(pages)],
    )
    return ProjectState(
        project_id="p",
        render_mode=render_mode,
        page_cache={"0": pageset},
        pages_done=[],
    )


def test_planning_only_project_reports_nonzero_progress():
    """A checkpoint with a finished page plan is real progress, not 0%."""
    pct = estimate_progress(_planned_state(), total_chunks=1)
    assert pct > 0.0


def test_planning_progress_stays_below_rendering_progress():
    """Planning weight must not inflate the bar to where drawing is irrelevant."""
    planning_only = estimate_progress(_planned_state(pages=4), total_chunks=1)
    finished = _planned_state(pages=4)
    finished.pages_done = ["c0000-p0000", "c0000-p0001", "c0000-p0002", "c0000-p0003"]
    after_render = estimate_progress(finished, total_chunks=1)
    assert planning_only < after_render


def test_progress_is_monotonic_from_planning_to_rendering():
    """Resuming with more work done must never move the bar backwards."""
    state = _planned_state(pages=4)
    steps = [estimate_progress(state, total_chunks=1)]
    for i in range(4):
        state.pages_done = [f"c0000-p{j:04d}" for j in range(i + 1)]
        steps.append(estimate_progress(state, total_chunks=1))
    assert steps == sorted(steps)


def test_empty_state_still_reports_zero():
    """Nothing planned and nothing drawn is genuinely 0%."""
    assert estimate_progress(ProjectState(project_id="p"), total_chunks=1) == 0.0


def test_progress_never_reaches_one_before_done():
    """The bar is reserved below 1.0 until the run reports done."""
    state = _planned_state(pages=2)
    state.pages_done = ["c0000-p0000", "c0000-p0001"]
    assert estimate_progress(state, total_chunks=1) < 1.0


def test_panel_compose_planning_only_also_nonzero():
    """The storyboard path has the same planning gap and needs the same floor."""
    board = Storyboard(
        chapter_id="0",
        panels=[Panel(panel_id=str(i), action="a") for i in range(4)],
    )
    state = ProjectState(
        project_id="p",
        render_mode="panel_compose",
        chunk_cache={"0": ChunkCache(storyboard=board)},
        panels_done=[],
    )
    assert estimate_progress(state, total_chunks=1) > 0.0
