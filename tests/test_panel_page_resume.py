"""tests/test_panel_page_resume.py — panel_compose resume helpers."""

from pathlib import Path

from PIL import Image

from core.comic.identity import page_state_key
from core.comic.page_lettering import LETTERING_VERSION
from core.pipelines.resume import (
    page_panel_state_key,
    panel_page_chunk_complete,
)
from core.schemas import (
    ComicPagePlan,
    ComicPagePlanSet,
    GeneratedPage,
    GeneratedPanel,
    PagePanelSpec,
    ProjectState,
)


def test_page_panel_state_key_distinct_from_page_key():
    assert page_panel_state_key(0, 0, 0) == "c0000-p0000-g0000"
    assert page_panel_state_key(0, 0, 0) != page_state_key(0, 0)


def _state_with(pageset, tmp_path: Path, *, panels_done: bool, pages_done: bool):
    state = ProjectState(project_id="p")
    state.page_cache["0"] = pageset
    for page_index, plan in enumerate(pageset.pages):
        page_key = page_state_key(0, page_index)
        if pages_done:
            page_path = tmp_path / f"{page_key}.png"
            Image.new("RGB", (4, 4)).save(page_path)
            state.generated.pages[page_key] = GeneratedPage(
                local=str(page_path), lettering_version=LETTERING_VERSION
            )
            state.pages_done.append(page_key)
            for panel_index in range(len(plan.panels)):
                panel_key = page_panel_state_key(0, page_index, panel_index)
                if panels_done:
                    panel_path = tmp_path / f"{panel_key}.png"
                    Image.new("RGB", (4, 4)).save(panel_path)
                    state.generated.panels[panel_key] = GeneratedPanel(
                        local=str(panel_path), chunk_index=0, panel_index=panel_index
                    )
                    state.panels_done.append(panel_key)
    return state


def test_panel_page_chunk_complete_requires_every_panel(tmp_path):
    plan = ComicPagePlan(
        page_id="p1", panels=[PagePanelSpec(panel_id="1"), PagePanelSpec(panel_id="2")]
    )
    pageset = ComicPagePlanSet(pages=[plan])
    state = _state_with(pageset, tmp_path, panels_done=True, pages_done=True)
    assert panel_page_chunk_complete(state, pageset, tmp_path, 0)
    second = page_panel_state_key(0, 0, 1)
    state.panels_done.remove(second)
    assert not panel_page_chunk_complete(state, pageset, tmp_path, 0)


def test_panel_page_chunk_complete_false_when_page_missing(tmp_path):
    plan = ComicPagePlan(page_id="p1", panels=[PagePanelSpec(panel_id="1")])
    pageset = ComicPagePlanSet(pages=[plan])
    state = _state_with(pageset, tmp_path, panels_done=False, pages_done=False)
    assert not panel_page_chunk_complete(state, pageset, tmp_path, 0)


def test_panel_page_chunk_complete_false_when_page_stale(tmp_path):
    plan = ComicPagePlan(page_id="p1", panels=[PagePanelSpec(panel_id="1")])
    pageset = ComicPagePlanSet(pages=[plan])
    state = _state_with(pageset, tmp_path, panels_done=True, pages_done=True)
    assert panel_page_chunk_complete(state, pageset, tmp_path, 0)
    state.stale_pages.append(page_state_key(0, 0))
    assert not panel_page_chunk_complete(state, pageset, tmp_path, 0)


def test_panel_page_chunk_complete_false_when_panel_stale(tmp_path):
    plan = ComicPagePlan(page_id="p1", panels=[PagePanelSpec(panel_id="1")])
    pageset = ComicPagePlanSet(pages=[plan])
    state = _state_with(pageset, tmp_path, panels_done=True, pages_done=True)
    state.stale_panels.append(page_panel_state_key(0, 0, 0))
    assert not panel_page_chunk_complete(state, pageset, tmp_path, 0)


def test_panel_page_chunk_complete_false_after_a_lettering_change(tmp_path):
    plan = ComicPagePlan(page_id="p1", panels=[PagePanelSpec(panel_id="1")])
    pageset = ComicPagePlanSet(pages=[plan])
    state = _state_with(pageset, tmp_path, panels_done=True, pages_done=True)
    page_key = page_state_key(0, 0)
    state.generated.pages[page_key] = state.generated.pages[page_key].model_copy(
        update={"lettering_version": "older"}
    )
    assert not panel_page_chunk_complete(state, pageset, tmp_path, 0)
