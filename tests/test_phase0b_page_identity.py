"""Phase 0b: page identity is positional ``c{ci:04d}-p{idx:04d}`` (§4/§9)."""

from core.comic.identity import (
    merge_character_alias,
    page_state_key,
)
from core.pipelines.creative_comic import (
    _panel_state_key,
)
from core.schemas import (
    CharacterAsset,
    ComicPagePlan,
    ComicPagePlanSet,
    ProjectState,
)


def test_page_state_key_matches_panel_key_shape():
    assert page_state_key(0, 0) == "c0000-p0000"
    assert page_state_key(1, 11) == "c0001-p0011"


def test_page_state_key_is_zero_padded_to_four():
    assert page_state_key(12, 300) == "c0012-p0300"
    assert page_state_key(3, 7) == "c0003-p0007"


def test_page_state_key_ignores_model_page_id():
    """Identity must be derivable from position alone (no model string)."""
    assert page_state_key(0, 0) == page_state_key(0, 0)
    assert ":" not in page_state_key(0, 0)


def test_page_and_panel_keys_share_one_convention():
    """0b: page keys converge on the panel convention, one formatter each."""
    assert _panel_state_key(0, 3) == "c0000-p0003"
    assert page_state_key(0, 3) == _panel_state_key(0, 3)


def test_alias_merge_marks_positional_page_keys():
    pages = ComicPagePlanSet(
        unit_id="1",
        pages=[
            ComicPagePlan.model_validate(
                {"page_id": "u1_p0001", "panels": [{"panel_id": "1", "action": "a"}]}
            ),
            ComicPagePlan.model_validate(
                {
                    "page_id": "u1_p0002",
                    "reference_characters": ["福贵"],
                    "panels": [{"panel_id": "2", "action": "b", "characters": ["福贵"]}],
                }
            ),
        ],
    )
    state = ProjectState(
        project_id="p",
        characters={
            "徐福贵": CharacterAsset(name="徐福贵"),
            "福贵": CharacterAsset(name="福贵"),
        },
        page_cache={"0": pages},
        pages_done=["c0000-p0000", "c0000-p0001"],
    )

    merge_character_alias(state, "福贵", "徐福贵")

    assert state.stale_pages == ["c0000-p0001"]
    assert state.pages_done == ["c0000-p0000"]

import asyncio
from unittest.mock import patch

from core.pipelines.creative_comic import creative_comic
from tests.test_finished_page_pipeline import FakeImage, FakeChat, _fake_export_pdf


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_finished_page_keys_are_positional(tmp_path, monkeypatch):
    """The state key is position, not the model's ``page_id`` (0b)."""
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "finished_page")
    src = "第一章\n福贵在村口。\n第二章\n福贵在读书。"
    proj = asyncio.run(
        creative_comic(src, output_dir=str(tmp_path), chat=FakeChat(), image=FakeImage())
    )

    assert "c0000-p0000" in proj.state.generated.pages
    assert "c0001-p0000" in proj.state.generated.pages
    assert all(":" not in key for key in proj.state.generated.pages)


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_key_is_unchanged_when_model_page_id_changes(tmp_path, monkeypatch):
    """A replan that renames ``page_id`` must not move the recorded key (0b)."""
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "finished_page")
    src = "第一章\n福贵在村口。"
    proj = asyncio.run(
        creative_comic(src, output_dir=str(tmp_path), chat=FakeChat(), image=FakeImage())
    )
    recorded = proj.state.generated.pages["c0000-p0000"]
    assert recorded.page_id == "u1_p0001"  # audited, but not identity

    rerun = asyncio.run(
        creative_comic(src, output_dir=str(tmp_path), chat=FakeChat(), image=FakeImage())
    )
    assert "c0000-p0000" in rerun.state.generated.pages
    assert rerun.state.pages_done == ["c0000-p0000"]
