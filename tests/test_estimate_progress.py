"""tests/test_estimate_progress.py — resume progress bar fraction."""

from core.pipelines.creative_comic import estimate_progress
from core.schemas import ChunkCache, Panel, ProjectState, Storyboard


def test_estimate_progress_scales_with_panels_done():
    board = Storyboard(
        chapter_id="0",
        panels=[Panel(panel_id=str(i), action="a") for i in range(10)],
    )
    state = ProjectState(
        project_id="p",
        chunk_cache={"0": ChunkCache(storyboard=board)},
        panels_done=[f"c0000-p{i:04d}" for i in range(5)],
    )
    pct = estimate_progress(state, total_chunks=1)
    # 5/10 panels * 0.9 = 0.45
    assert 0.40 <= pct <= 0.50


def test_estimate_progress_nonzero_on_partial_project():
    board = Storyboard(
        chapter_id="2",
        panels=[Panel(panel_id=str(i), action="a") for i in range(10)],
    )
    state = ProjectState(
        project_id="p",
        skipped_chunks=["0", "1"],
        chunk_cache={"2": ChunkCache(storyboard=board)},
        panels_done=[f"c0002-p{i:04d}" for i in range(10)],
        chunks_done=["2"],
    )
    pct = estimate_progress(state, total_chunks=5)
    assert pct > 0.15
