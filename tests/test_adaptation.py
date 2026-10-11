"""Adaptation plans copy key beats onto the source unit that owns their chunk."""

import asyncio
from unittest.mock import patch

from core.comic.adaptation import adaptation_plan, owning_unit, sync_adaptation_plans
from core.comic.segmentation import source_units
from core.pipelines.creative_comic import _structure_fingerprint, creative_comic
from core.schemas import KeyBeat, KeyBeatSet, ProjectState
from tests.test_finished_page_pipeline import FakeChat, FakeImage, _fake_export_pdf


def test_a_must_draw_beat_is_required_and_the_next_beat_depends_on_it():
    plan = adaptation_plan(
        "u0000",
        [
            KeyBeat(beat_id="hatch", summary="the stone splits", must_draw=True),
            KeyBeat(beat_id="walk", summary="the monkey walks", must_draw=False),
        ],
    )
    assert [beat.status for beat in plan.beats] == ["required", "optional"]
    assert plan.beats[0].depends_on == []
    assert plan.beats[1].depends_on == ["hatch"]
    assert plan.beats[1].unit_id == "u0000"


def test_a_chunk_that_crosses_two_units_is_not_assigned():
    units = source_units("第一章\n甲。\n第二章\n乙。")
    assert owning_unit(units, "甲。") == "u0000"
    assert owning_unit(units, units[0].text + units[1].text) is None
    assert owning_unit(units, "甲。\n第二章\n乙。") == "u0001"


def test_an_older_state_loads_with_no_plans():
    state = ProjectState.model_validate_json('{"project_id": "p1"}')
    assert state.adaptation_plans == {}


def test_sync_fills_the_owning_unit_and_does_not_replace_a_filled_plan():
    units = source_units("第一章\n石猴出世。")
    state = ProjectState(project_id="p", source_units=units)
    state.beat_cache["0"] = KeyBeatSet(
        beats=[KeyBeat(beat_id="hatch", summary="the stone splits", must_draw=True)]
    )
    sync_adaptation_plans(state, ["石猴出世。"])
    assert state.adaptation_plans["u0000"].beats[0].beat_id == "hatch"
    state.beat_cache["0"] = KeyBeatSet(
        beats=[KeyBeat(beat_id="other", summary="a later extract", must_draw=True)]
    )
    sync_adaptation_plans(state, ["石猴出世。"])
    assert state.adaptation_plans["u0000"].beats[0].beat_id == "hatch"


class _BeatChat(FakeChat):
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


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_a_finished_page_run_stores_the_plan_without_a_fingerprint_change(tmp_path, monkeypatch):
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "finished_page")
    src = "第一章\n石猴从石头里走出来。"
    chat, img = _BeatChat(), FakeImage()
    proj = asyncio.run(creative_comic(src, output_dir=str(tmp_path), chat=chat, image=img))
    plan = proj.state.adaptation_plans["u0000"]
    assert [beat.status for beat in plan.beats] == ["required", "optional"]
    assert plan.beats[1].depends_on == ["hatch"]
    assert proj.state.structure_fingerprint == _structure_fingerprint(src)

    chat2, img2 = _BeatChat(), FakeImage()
    proj2 = asyncio.run(creative_comic(src, output_dir=str(tmp_path), chat=chat2, image=img2))
    assert chat2.calls == 0
    assert proj2.state.adaptation_plans["u0000"].beats[1].depends_on == ["hatch"]
