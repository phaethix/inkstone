"""A finished page must not be conditioned on the previous page's pixels.

Page 4 of the dock sample copied page 3 almost exactly, extra reader included,
because the render branch passed the previous blank in as an image reference.
The panel-compose path still reads ``INKSTONE_PANEL_CONTINUITY``; this branch
must not.
"""

from pathlib import Path


def _render_branch() -> str:
    source = Path("core/pipelines/creative_comic.py").read_text(encoding="utf-8")
    start = source.index("async def _render_page_node")
    end = source.index("if _append_page_render_nodes", start)
    return source[start:end]


def test_finished_page_render_does_not_condition_on_the_previous_page():
    branch = _render_branch()
    call = branch[branch.index("refs = collect_finished_page_refs") :]
    call = call[: call.index(")") + 1]
    assert "prev_blank" not in call
    assert "panel_continuity=False" in call
    assert "image_config.panel_continuity" not in call
    policy = branch[branch.index("ref_policy = finished_page_ref_policy") :]
    policy = policy.split("\n", 1)[0]
    assert "panel_continuity=False" in policy
    assert "image_config.panel_continuity" not in policy
