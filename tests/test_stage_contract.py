"""Phase 1: a stage's historical input is a bound, not the whole book.

Design §5. ``page_plan`` may read earlier layouts, but only through a declared
window. The strings themselves stay out of the declared input, so a change to
one early page cannot re-plan the rest of the book. Render-only knobs stay out
of the structure fingerprint, which is what the chat caches key on.
"""

import json

from core.comic.export import ExportEngine, _pdf_batch_size
from core.comic.layout import LayoutEngine
from core.comic.page_lettering import letter_finished_page
from core.comic.stage_contract import (
    RECENT_LAYOUT_LIMIT,
    RENDER_ONLY_PARAMS,
    historical_identity,
    stage_contract,
    stage_source_hash,
)
from core.pipelines.creative_comic import (
    _EXPORT_SOURCES,
    _LAYOUT_SOURCES,
    _LETTER_SOURCES,
    _LETTER_STAGE_SRC,
    _WEBTOON_SOURCES,
    _creative_comic,
    _recent_layout_intents,
    _structure_fingerprint,
)
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


def test_bible_reads_one_summary_rather_than_earlier_chunks():
    contract = stage_contract("bible")
    assert contract.reads_accumulated is True
    assert contract.historical_limit == 1
    assert contract.hard_inputs == frozenset({"source_chunk", "character_table", "bible_summary"})
    assert "visual_bible" not in contract.hard_inputs
    identity = historical_identity("bible")
    blob = json.dumps(identity)
    assert set(identity) == {"bible_summary"}
    assert identity["bible_summary"]["limit"] == 1
    assert set(identity["bible_summary"]) == {"limit", "summarizers"}
    assert "recent_layouts" not in identity
    assert "intent-" not in blob
    assert "state" not in historical_identity.__code__.co_varnames


def test_chat_stages_do_not_take_render_only_params():
    for stage in ("extract", "bible", "beats", "page_plan", "storyboard", "page_script"):
        contract = stage_contract(stage)
        assert contract.hard_inputs.isdisjoint(RENDER_ONLY_PARAMS)


def test_local_stages_declare_their_inputs_without_an_accumulated_window():
    expected = {
        "portrait": frozenset({"bible_entry_for_character", "character_asset"}),
        "page_script": frozenset({"source_chunk", "storyboard", "extracted_elements"}),
        "letter": frozenset({"blank_page", "page_plan", "source_text"}),
        "export": frozenset({"bound_images", "binding"}),
        "layout": frozenset({"panel_images", "drawn_text", "page_geometry"}),
    }
    for stage, hard in expected.items():
        contract = stage_contract(stage)
        assert contract.reads_accumulated is False
        assert contract.historical_limit is None
        assert contract.hard_inputs == hard
        assert contract.hard_inputs.isdisjoint(RENDER_ONLY_PARAMS)
    assert "font_path" not in stage_contract("letter").hard_inputs
    assert historical_identity("layout") == {}
    assert historical_identity("export") == {}
    portrait = stage_contract("portrait")
    assert portrait.soft_refs == frozenset({"canonical_portrait_ref"})
    panel = stage_contract("render.panel")
    assert panel.reads_accumulated is False
    assert "prev_panel" in panel.soft_refs
    assert "prev_panel" not in panel.hard_inputs


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


def test_local_stage_sources_are_the_declared_drawers():
    assert _LETTER_STAGE_SRC == stage_source_hash(*_LETTER_SOURCES)
    assert _LETTER_STAGE_SRC != stage_source_hash(letter_finished_page)
    assert LayoutEngine._draw_bubble in _LETTER_SOURCES
    assert LayoutEngine._compose_pages not in _LETTER_SOURCES
    assert LayoutEngine._paginate in _LAYOUT_SOURCES
    assert letter_finished_page not in _LAYOUT_SOURCES
    assert LayoutEngine._compose_webtoon in _WEBTOON_SOURCES
    assert LayoutEngine._compose_pages not in _WEBTOON_SOURCES
    assert ExportEngine.export_pdf in _EXPORT_SOURCES
    assert _pdf_batch_size not in _EXPORT_SOURCES
    for sources in (_LETTER_SOURCES, _LAYOUT_SOURCES, _WEBTOON_SOURCES, _EXPORT_SOURCES):
        assert _creative_comic not in sources


def test_render_page_keeps_the_continuity_image_soft():
    contract = stage_contract("render.page")
    assert "l2_reference" in contract.soft_refs
    assert "l2_reference" not in contract.hard_inputs
