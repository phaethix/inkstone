"""Phase 1: a stage's historical input is a bound, not the whole book.

Design §5. ``page_plan`` may read earlier layouts, but only through a declared
window. The strings themselves stay out of the declared input, so a change to
one early page cannot re-plan the rest of the book. Render-only knobs stay out
of the structure fingerprint, which is what the chat caches key on.
"""

import json

from core.comic.stage_contract import (
    RECENT_LAYOUT_LIMIT,
    RENDER_ONLY_PARAMS,
    historical_identity,
    stage_contract,
)
from core.pipelines.creative_comic import _recent_layout_intents, _structure_fingerprint
from core.schemas import ComicPagePlan, ComicPagePlanSet, ProjectState


def _state_with_intents(count: int) -> ProjectState:
    state = ProjectState(project_id="p")
    for index in range(count):
        state.page_cache[str(index)] = ComicPagePlanSet(
            unit_id=f"u{index}",
            pages=[ComicPagePlan(page_id=f"p{index}", layout_intent=f"intent-{index}")],
        )
    return state


def test_page_plan_declares_a_bounded_historical_dependency():
    contract = stage_contract("page_plan")
    assert contract.reads_accumulated is True
    assert contract.historical_limit == RECENT_LAYOUT_LIMIT == 8
    assert "page_cache" not in contract.hard_inputs
    assert "visual_bible" not in contract.hard_inputs
    assert "bible_entries_for_page" in contract.hard_inputs


def test_declared_historical_input_excludes_the_intent_strings():
    identity = historical_identity("page_plan")
    blob = json.dumps(identity)
    assert identity["recent_layouts"]["limit"] == 8
    assert set(identity["recent_layouts"]) == {"limit", "summarizers"}
    assert "intent-" not in blob
    assert "state" not in historical_identity.__code__.co_varnames


def test_the_runtime_window_uses_the_declared_limit():
    state = _state_with_intents(12)
    window = _recent_layout_intents(state)
    assert window == [f"intent-{index}" for index in range(4, 12)]
    assert len(window) == RECENT_LAYOUT_LIMIT


def test_chat_stages_do_not_take_render_only_params():
    for stage in ("extract", "bible", "beats", "page_plan", "storyboard"):
        contract = stage_contract(stage)
        assert contract.hard_inputs.isdisjoint(RENDER_ONLY_PARAMS)


def test_render_only_params_do_not_change_the_structure_fingerprint(monkeypatch):
    source = "第一章\n方鸿渐在甲板上。"
    monkeypatch.setenv("INKSTONE_PAGE_SIZE", "1024x1024")
    monkeypatch.setenv("INKSTONE_L3", "0")
    monkeypatch.setenv("INKSTONE_PANEL_CONTINUITY", "0")
    first = _structure_fingerprint(source)
    monkeypatch.setenv("INKSTONE_PAGE_SIZE", "1024x1536")
    monkeypatch.setenv("INKSTONE_L3", "1")
    monkeypatch.setenv("INKSTONE_PANEL_CONTINUITY", "1")
    assert _structure_fingerprint(source) == first


def test_render_page_keeps_the_continuity_image_soft():
    contract = stage_contract("render.page")
    assert "l2_reference" in contract.soft_refs
    assert "l2_reference" not in contract.hard_inputs
