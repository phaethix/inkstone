"""tests/test_estimate_progress.py — resume progress bar fraction."""

from core.pipelines.creative_comic import (
    _ordered_generated_panels,
    estimate_progress,
    panel_progress_counts,
)
from core.schemas import ChunkCache, GeneratedAssets, GeneratedPanel, Panel, ProjectState, Storyboard


def test_panel_progress_counts_matches_estimate():
    board = Storyboard(
        chapter_id="0",
        panels=[Panel(panel_id=str(i), action="a") for i in range(10)],
    )
    state = ProjectState(
        project_id="p",
        chunk_cache={"0": ChunkCache(storyboard=board)},
        panels_done=[f"c0000-p{i:04d}" for i in range(5)],
    )
    done, planned = panel_progress_counts(state, total_chunks=1)
    assert done == 5
    assert planned == 10
    pct = estimate_progress(state, total_chunks=1)
    # 5/10 panels * 0.9 = 0.45
    assert 0.40 <= pct <= 0.50


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


def test_ordered_generated_panels_ignores_non_digit_chunk_keys():
    board = Storyboard(
        chapter_id="0",
        panels=[Panel(panel_id="p0", action="a")],
    )
    state = ProjectState(
        project_id="p",
        chunk_cache={
            "legacy-meta": ChunkCache(),
            "1": ChunkCache(storyboard=board),
            "0": ChunkCache(storyboard=board),
        },
        generated=GeneratedAssets(
            panels={
                "c0000-p0000": GeneratedPanel(local="/tmp/p0.png"),
                "c0001-p0000": GeneratedPanel(local="/tmp/p1.png"),
            }
        ),
    )
    ordered = _ordered_generated_panels(state)
    assert [key for key, _ in ordered] == ["c0000-p0000", "c0001-p0000"]
