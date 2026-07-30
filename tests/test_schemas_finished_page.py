# tests/test_schemas_finished_page.py
from core.schemas import (
    ComicPagePlan,
    ComicPagePlanSet,
    GeneratedPage,
    ProjectState,
)


def test_page_panel_spec_and_plan_round_trip():
    plan = ComicPagePlan.model_validate(
        {
            "page_id": "p0001",
            "purpose": "Establish the station and introduce 福贵",
            "layout_intent": "Wide establishing top; diagonal inset reaction bottom-right",
            "panels": [
                {
                    "panel_id": "1",
                    "role": "establishing",
                    "shape_hint": "wide",
                    "shot": "wide",
                    "action": "福贵 walks through dusk streets",
                    "characters": ["福贵"],
                    "setting_ref": "城中街道",
                    "caption": "傍晚，街灯初上。",
                    "dialogue": None,
                    "sfx": "沙沙",
                }
            ],
            "reference_characters": ["福贵"],
            "setting_refs": ["城中街道"],
        }
    )
    assert plan.page_id == "p0001"
    assert plan.panels[0].caption == "傍晚，街灯初上。"
    assert plan.panels[0].dialogue is None


def test_comic_page_plan_set_and_generated_page_on_state():
    pageset = ComicPagePlanSet.model_validate(
        {
            "unit_id": "0",
            "pages": [
                {"page_id": "p0001", "purpose": "x", "layout_intent": "splash", "panels": []}
            ],
        }
    )
    state = ProjectState(project_id="demo")
    state.page_cache["0"] = pageset
    state.generated.pages["p0001"] = GeneratedPage(
        local="pages/page_01.png",
        page_id="p0001",
        unit_index=0,
        page_index=0,
        mode="finished",
        caption="傍晚，街灯初上。",
    )
    state.pages_done.append("p0001")
    state.render_mode = "finished_page"
    state.stage = "pages"
    blob = state.model_dump_json()
    loaded = ProjectState.model_validate_json(blob)
    assert loaded.page_cache["0"].pages[0].page_id == "p0001"
    assert loaded.generated.pages["p0001"].mode == "finished"
    assert loaded.render_mode == "finished_page"
