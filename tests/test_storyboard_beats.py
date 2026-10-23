"""Storyboard and page panels name the beats they stage. No extra model call."""

import asyncio
from unittest.mock import patch

from core.comic.adaptation import assign_panel_beat_ids, stamp_page_panel_beats
from core.pipelines.creative_comic import _structure_fingerprint, creative_comic
from core.schemas import (
    ComicPagePlan,
    ComicPagePlanSet,
    KeyBeat,
    KeyBeatSet,
    PagePanelSpec,
    Panel,
    ProjectState,
    StoryElements,
)
from core.screenwriter import plan_storyboard
from tests.test_creative_comic import FakeChat, FakeImage, _fake_export_pdf
from tests.test_finished_page_pipeline import FakeChat as FinishedChat
from tests.test_finished_page_pipeline import FakeImage as FinishedImage
from tests.test_finished_page_pipeline import _fake_export_pdf as _fake_page_export


def test_a_required_beat_is_written_onto_a_panel_and_an_unknown_id_is_dropped():
    panels = [
        Panel(panel_id="p1", beat_ids=["nope"]),
        Panel(panel_id="p2"),
    ]
    assign_panel_beat_ids(
        panels,
        [
            KeyBeat(beat_id="hatch", summary="the stone splits", must_draw=True),
            KeyBeat(beat_id="walk", summary="the monkey walks", must_draw=False),
        ],
    )
    assert panels[0].beat_ids == ["hatch"]
    assert panels[1].beat_ids == []
    assign_panel_beat_ids(
        panels,
        [
            KeyBeat(beat_id="hatch", summary="the stone splits", must_draw=True),
            KeyBeat(beat_id="walk", summary="the monkey walks", must_draw=False),
        ],
    )
    assert panels[0].beat_ids == ["hatch"]


def test_page_covers_are_split_across_panels_and_a_missing_required_beat_is_added():
    pageset = ComicPagePlanSet(
        pages=[
            ComicPagePlan(
                page_id="p1",
                covers_beats=["hatch", "made-up"],
                panels=[PagePanelSpec(panel_id="a"), PagePanelSpec(panel_id="b")],
            )
        ]
    )
    stamp_page_panel_beats(
        pageset,
        KeyBeatSet(
            beats=[
                KeyBeat(beat_id="hatch", summary="the stone splits", must_draw=True),
                KeyBeat(beat_id="walk", summary="the monkey walks", must_draw=True),
            ]
        ),
    )
    page = pageset.pages[0]
    assert page.panels[0].beat_ids == ["hatch"]
    assert page.panels[1].beat_ids == ["walk"]
    assert page.covers_beats == ["hatch", "walk"]


def test_plan_storyboard_asks_for_beat_ids_and_drops_one_the_beats_do_not_name():
    seen: dict = {}

    class CaptureChat:
        async def chat_function_call(self, messages, tools, tool_choice, **kwargs):
            seen["messages"] = messages
            return {
                "chapter_id": "ch01",
                "panels": [
                    {"panel_id": "p1", "action": "the stone splits", "beat_ids": ["hatch", "nope"]},
                    {"panel_id": "p2", "action": "look at the sea"},
                ],
            }

    beats = KeyBeatSet(beats=[KeyBeat(beat_id="hatch", summary="the stone splits", must_draw=True)])
    board = asyncio.run(
        plan_storyboard("第一章\n石猴出世。", StoryElements(), chat=CaptureChat(), key_beats=beats)
    )
    blob = " ".join(message["content"] for message in seen["messages"])
    assert "beat_ids" in blob
    assert board.panels[0].beat_ids == ["hatch"]
    assert board.panels[1].beat_ids == []


def test_plan_storyboard_without_beats_does_not_keep_an_invented_id():
    class Chat:
        async def chat_function_call(self, messages, tools, tool_choice, **kwargs):
            return {
                "chapter_id": "ch01",
                "panels": [{"panel_id": "p1", "action": "look", "beat_ids": ["invented"]}],
            }

    board = asyncio.run(plan_storyboard("甲板上。", StoryElements(), chat=Chat()))
    assert board.panels[0].beat_ids == []


@patch("core.comic.export.ExportEngine.export_pdf", _fake_page_export)
def test_a_finished_page_stores_the_required_beat_on_its_panel(tmp_path, monkeypatch):
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "finished_page")

    class BeatChat(FinishedChat):
        async def chat_function_call(self, messages, tools, tool_choice, **kw):
            if tool_choice["function"]["name"] == "extract_key_beats":
                self.calls += 1
                return {
                    "beats": [
                        {"beat_id": "hatch", "summary": "the stone splits", "must_draw": True},
                        {"beat_id": "walk", "summary": "the monkey walks", "must_draw": False},
                    ]
                }
            return await super().chat_function_call(messages, tools, tool_choice, **kw)

    src = "第一章\n石猴从石头里走出来。"
    proj = asyncio.run(
        creative_comic(src, output_dir=str(tmp_path), chat=BeatChat(), image=FinishedImage())
    )
    panel = proj.state.page_cache["0"].pages[0].panels[0]
    assert panel.beat_ids == ["hatch"]
    assert proj.state.structure_fingerprint == _structure_fingerprint(src)

    chat2 = BeatChat()
    proj2 = asyncio.run(
        creative_comic(src, output_dir=str(tmp_path), chat=chat2, image=FinishedImage())
    )
    assert chat2.calls == 0
    assert proj2.state.page_cache["0"].pages[0].panels[0].beat_ids == ["hatch"]


@patch("core.comic.export.ExportEngine.export_pdf", _fake_export_pdf)
def test_a_cached_storyboard_gains_beat_ids_without_another_call(tmp_path):
    src = "第一章\n方鸿渐在甲板上。"
    proj = asyncio.run(
        creative_comic(src, output_dir=str(tmp_path), chat=FakeChat(), image=FakeImage())
    )
    assert proj.state.chunk_cache["0"].storyboard.panels[0].beat_ids == []

    state = ProjectState.load(tmp_path / "state.json")
    state.beat_cache["0"] = KeyBeatSet(
        beats=[KeyBeat(beat_id="deck", summary="he looks at the sea", must_draw=True)]
    )
    state.save(tmp_path / "state.json")

    chat2 = FakeChat()
    proj2 = asyncio.run(
        creative_comic(src, output_dir=str(tmp_path), chat=chat2, image=FakeImage())
    )
    assert chat2.calls == 0
    assert proj2.state.chunk_cache["0"].storyboard.panels[0].beat_ids == ["deck"]
