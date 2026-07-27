"""tests/test_estimate_progress.py — resume progress bar fraction."""

from core.pipelines.creative_comic import (
    _ordered_generated_panels,
    estimate_progress,
    panel_progress_counts,
)
from core.schemas import (
    ChunkCache,
    GeneratedAssets,
    GeneratedPanel,
    Panel,
    ProjectState,
    Storyboard,
)


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


def test_ordered_generated_panels_uses_sparse_chunk_keys():
    """Chunk keys may skip indices after content-policy skips (0, 1, 5)."""
    board0 = Storyboard(chapter_id="0", panels=[Panel(panel_id="p0", action="a", dialogue="d0")])
    board1 = Storyboard(chapter_id="1", panels=[Panel(panel_id="p1", action="a", dialogue="d1")])
    board5 = Storyboard(chapter_id="5", panels=[Panel(panel_id="p5", action="a", dialogue="d5")])
    state = ProjectState(
        project_id="p",
        chunk_cache={
            "0": ChunkCache(storyboard=board0),
            "1": ChunkCache(storyboard=board1),
            "5": ChunkCache(storyboard=board5),
        },
        generated=GeneratedAssets(
            panels={
                "c0000-p0000": GeneratedPanel(local="/tmp/p0.png", chunk_index=0, panel_index=0),
                "c0001-p0000": GeneratedPanel(local="/tmp/p1.png", chunk_index=1, panel_index=0),
                "c0005-p0000": GeneratedPanel(local="/tmp/p5.png", chunk_index=5, panel_index=0),
            }
        ),
    )
    ordered = _ordered_generated_panels(state)
    assert [key for key, _ in ordered] == ["c0000-p0000", "c0001-p0000", "c0005-p0000"]
    assert [g.dialogue for _, g in ordered] == ["d0", "d1", "d5"]
    assert [g.chunk_index for _, g in ordered] == [0, 1, 5]


def test_chunk_complete_true_when_all_panels_skipped(tmp_path):
    from core.pipelines.creative_comic import _chunk_complete

    board = Storyboard(
        chapter_id="0",
        panels=[Panel(panel_id="p0", action="a"), Panel(panel_id="p1", action="a")],
    )
    state = ProjectState(
        project_id="p",
        skipped=["c0000-p0000", "c0000-p0001"],
    )
    assert _chunk_complete(state, board, tmp_path, 0) is True
