"""tests/test_progress_ceiling.py — planning weight must come out of the budget.

Review finding: adding the planning share *on top of* the old rendering formula
pushed a fully rendered project from 0.90 to 0.95. Layout and export still have
not run at that point, so the bar claimed 5% of work that did not exist. The
planning share has to be carved out of the 0.9 rendering budget instead.
"""

from core.pipelines.progress import estimate_progress
from core.schemas import (
    CharacterAsset,
    ComicPagePlan,
    ComicPagePlanSet,
    ProjectState,
    VisualBible,
)


def _rendered_state(pages: int = 3) -> ProjectState:
    """Every page drawn, but layout/export not yet started."""
    pageset = ComicPagePlanSet(
        unit_id="0",
        pages=[ComicPagePlan(page_id=f"c0000-p{i:04d}", summary="s") for i in range(pages)],
    )
    return ProjectState(
        project_id="p",
        render_mode="finished_page",
        page_cache={"0": pageset},
        pages_done=[f"c0000-p{i:04d}" for i in range(pages)],
        characters={"R": CharacterAsset(name="R")},
        visual_bible=VisualBible(),
    )


def test_all_pages_rendered_stays_at_the_pre_layout_ceiling():
    """0.95 would claim layout/export progress that has not happened."""
    pct = estimate_progress(_rendered_state(), total_chunks=1)
    assert pct <= 0.90 + 1e-9


def test_all_pages_rendered_is_well_above_planning_only():
    """The pre-layout ceiling must still be far past the planning floor."""
    pct = estimate_progress(_rendered_state(), total_chunks=1)
    assert pct >= 0.80


def test_planning_share_is_not_added_on_top_of_rendering():
    """Planning complete + all pages rendered must not exceed the render ceiling.

    A project that has finished both halves is at the pre-layout ceiling: the
    planning share is part of the same 0.9, not extra.
    """
    state = _rendered_state(pages=2)
    pct = estimate_progress(state, total_chunks=1)
    assert pct <= 0.90 + 1e-9
