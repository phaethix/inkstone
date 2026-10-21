"""Density is a planning budget stored on the project."""

import asyncio
from unittest.mock import patch

import pytest

from core.cli import _build_parser
from core.density import PANELS_PER_CHUNK_A, PANELS_PER_CHUNK_B, PANELS_PER_CHUNK_C
from core.pipelines.creative_comic import _structure_fingerprint, creative_comic
from core.schemas import ProjectState, StoryElements
from core.screenwriter import plan_comic_pages, plan_storyboard
from tests.test_creative_comic import FakeChat, FakeImage, _fake_export_pdf


def test_tier_b_keeps_the_historical_structure_fingerprint():
    source = "第一章\n方鸿渐在甲板上。"
    historical = _structure_fingerprint(source)
    assert _structure_fingerprint(source, density="B", panels_per_chunk=PANELS_PER_CHUNK_B) == (
        historical
    )
    assert _structure_fingerprint(source, density="A", panels_per_chunk=PANELS_PER_CHUNK_A) != (
        historical
    )


def test_an_older_state_loads_the_default_tier():
    loaded = ProjectState.model_validate_json('{"project_id":"p1"}')
    assert loaded.density == "B"
    assert loaded.panels_per_chunk == PANELS_PER_CHUNK_B


class _BudgetChat:
    def __init__(self, payload):
        self.payload = payload
        self.user = ""

    async def chat_function_call(self, messages, tools, tool_choice, **kwargs):
        self.user = messages[1]["content"]
        return self.payload


def test_a_storyboard_keeps_only_the_budgeted_panels():
    chat = _BudgetChat(
        {
            "chapter_id": "ch",
            "panels": [{"panel_id": str(i), "action": "walks"} for i in range(5)],
        }
    )
    board = asyncio.run(
        plan_storyboard("甲板上。", StoryElements(), chat=chat, panel_budget=2, enforce_budget=True)
    )
    assert [panel.panel_id for panel in board.panels] == ["0", "1"]
    assert "about 2 panels" in chat.user


def test_a_page_plan_drops_panels_past_the_budget_and_their_boxes():
    chat = _BudgetChat(
        {
            "unit_id": "1",
            "pages": [
                {
                    "page_id": "p1",
                    "purpose": "arrive",
                    "layout_intent": "wide top",
                    "panels": [
                        {"panel_id": "a", "action": "walks"},
                        {"panel_id": "b", "action": "stops"},
                    ],
                    "lettering_boxes": [
                        {
                            "kind": "caption",
                            "panel_id": "a",
                            "x": 0.1,
                            "y": 0.1,
                            "w": 0.2,
                            "h": 0.1,
                        },
                        {
                            "kind": "caption",
                            "panel_id": "b",
                            "x": 0.1,
                            "y": 0.4,
                            "w": 0.2,
                            "h": 0.1,
                        },
                    ],
                },
                {
                    "page_id": "p2",
                    "purpose": "look",
                    "layout_intent": "inset",
                    "panels": [
                        {"panel_id": "c", "action": "looks"},
                        {"panel_id": "d", "action": "turns"},
                    ],
                    "lettering_boxes": [
                        {
                            "kind": "caption",
                            "panel_id": "d",
                            "x": 0.1,
                            "y": 0.1,
                            "w": 0.2,
                            "h": 0.1,
                        }
                    ],
                },
            ],
        }
    )
    pageset = asyncio.run(
        plan_comic_pages(
            "甲板上。", StoryElements(), chat=chat, panel_budget=3, enforce_budget=True
        )
    )
    assert [[panel.panel_id for panel in page.panels] for page in pageset.pages] == [
        ["a", "b"],
        ["c"],
    ]
    assert pageset.pages[1].lettering_boxes == []
    assert "about 3 panels" in chat.user


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_a_density_change_replans_and_the_same_tier_does_not(tmp_path):
    src = "第一章\n方鸿渐在甲板上。"
    chat, img = FakeChat(), FakeImage()
    proj = asyncio.run(creative_comic(src, output_dir=str(tmp_path), chat=chat, image=img))
    assert proj.state.density == "B"
    assert proj.state.panels_per_chunk == PANELS_PER_CHUNK_B
    assert chat.calls > 0

    chat_same, img_same = FakeChat(), FakeImage()
    asyncio.run(
        creative_comic(src, output_dir=str(tmp_path), chat=chat_same, image=img_same, density="B")
    )
    assert chat_same.calls == 0
    assert img_same.calls == 0

    chat_a, img_a = FakeChat(), FakeImage()
    proj_a = asyncio.run(
        creative_comic(src, output_dir=str(tmp_path), chat=chat_a, image=img_a, density="A")
    )
    assert chat_a.calls > 0
    assert img_a.calls > 0
    assert proj_a.state.density == "A"
    assert proj_a.state.panels_per_chunk == PANELS_PER_CHUNK_A
    assert proj_a.state.structure_fingerprint == _structure_fingerprint(
        src, density="A", panels_per_chunk=PANELS_PER_CHUNK_A
    )


def test_generate_leaves_the_tier_unspecified_so_resume_can_inherit():
    """Omitting --density must stay distinguishable from asking for tier B.

    A parser default of ``"B"`` cannot be told apart from an explicit flag, so
    resuming a tier C project without repeating the flag used to read as a
    request to switch tiers.
    """
    args = _build_parser().parse_args(["generate", "book.txt"])
    assert args.density is None
    chosen = _build_parser().parse_args(["generate", "book.txt", "--density", "C"])
    assert chosen.density == "C"
    explicit_b = _build_parser().parse_args(["generate", "book.txt", "--density", "B"])
    assert explicit_b.density == "B"


def test_an_unknown_density_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="density must be"):
        asyncio.run(
            creative_comic(
                "第一章\n方鸿渐。",
                output_dir=str(tmp_path),
                chat=FakeChat(),
                image=FakeImage(),
                density="D",
            )
        )


def test_a_budget_the_planner_exceeded_is_reported(caplog):
    """Tier B does not clip, so the overrun has to be visible somewhere.

    The tier B sentence is the whole contract there: A and C clip the plan, B
    keeps whatever the planner returned. Keeping it is deliberate, but a
    planner that returns 14 against a budget of 8 currently leaves no trace, so
    the one number that says the budget is un-calibrated is invisible. This
    asserts the run logs both numbers instead of changing what it keeps.
    """
    chat = _BudgetChat(
        {
            "chapter_id": "ch",
            "panels": [{"panel_id": str(i), "action": "walks"} for i in range(5)],
        }
    )
    with caplog.at_level("WARNING"):
        board = asyncio.run(
            plan_storyboard(
                "甲板上。", StoryElements(), chat=chat, panel_budget=2, enforce_budget=False
            )
        )
    assert len(board.panels) == 5
    assert any(
        "5" in record.getMessage() and "2" in record.getMessage() for record in caplog.records
    )


def test_a_page_plan_past_the_budget_is_reported_too(caplog):
    """The page plan is the path B actually takes, so it needs the same signal."""
    chat = _BudgetChat(
        {
            "unit_id": "1",
            "pages": [
                {
                    "page_id": "p1",
                    "panels": [{"panel_id": f"p{i}", "action": "walks"} for i in range(4)],
                },
                {
                    "page_id": "p2",
                    "panels": [{"panel_id": f"q{i}", "action": "looks"} for i in range(4)],
                },
            ],
        }
    )
    with caplog.at_level("WARNING"):
        pageset = asyncio.run(
            plan_comic_pages(
                "甲板上。", StoryElements(), chat=chat, panel_budget=3, enforce_budget=False
            )
        )
    kept = sum(len(page.panels) for page in pageset.pages)
    assert kept == 8
    assert any(
        "8" in record.getMessage() and "3" in record.getMessage() for record in caplog.records
    )


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_resuming_a_tier_c_project_without_the_flag_keeps_tier_c(tmp_path):
    """Omitting --density on resume must inherit the tier the project was planned with.

    The flag used to default to ``"B"``, so a CLI that did not repeat it read as
    an explicit request to switch tiers. The structure fingerprint then differed
    from the stored one, the state was rebuilt, and every paid page was redrawn.
    """
    src = "第一章\n方鸿渐在甲板上。"
    first = asyncio.run(
        creative_comic(
            src,
            output_dir=str(tmp_path),
            chat=FakeChat(),
            image=FakeImage(),
            density="C",
        )
    )
    assert first.state.density == "C"

    chat, img = FakeChat(), FakeImage()
    resumed = asyncio.run(creative_comic(src, output_dir=str(tmp_path), chat=chat, image=img))

    assert resumed.state.density == "C"
    assert resumed.state.panels_per_chunk == PANELS_PER_CHUNK_C
    assert chat.calls == 0
    assert img.calls == 0


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_an_explicit_tier_on_resume_still_replans(tmp_path):
    """Inheriting must not swallow a deliberate tier change."""
    src = "第一章\n方鸿渐在甲板上。"
    asyncio.run(
        creative_comic(
            src,
            output_dir=str(tmp_path),
            chat=FakeChat(),
            image=FakeImage(),
            density="C",
        )
    )

    chat, img = FakeChat(), FakeImage()
    switched = asyncio.run(
        creative_comic(src, output_dir=str(tmp_path), chat=chat, image=img, density="A")
    )

    assert switched.state.density == "A"
    assert switched.state.panels_per_chunk == PANELS_PER_CHUNK_A
    assert chat.calls > 0
