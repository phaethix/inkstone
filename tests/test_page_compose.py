"""tests/test_page_compose.py — local composition of page-plan panels."""

import pytest
from PIL import Image

from core.comic.page_compose import compose_page_panels
from core.schemas import ComicPagePlan, PagePanelSpec


def test_compose_writes_blank_and_lettered_pages(tmp_path):
    plan = ComicPagePlan(
        page_id="p1",
        panels=[
            PagePanelSpec(panel_id="1", dialogue="我回来了。", caption="傍晚，村口。"),
            PagePanelSpec(panel_id="2"),
        ],
    )
    images = [Image.new("RGB", (300, 200), (200, 200, 200)) for _ in plan.panels]
    blank = tmp_path / "blank.png"
    lettered = tmp_path / "lettered.png"
    compose_page_panels(plan, images, blank, lettered)
    assert blank.is_file() and lettered.is_file()
    assert blank.read_bytes() != lettered.read_bytes()


def test_compose_rejects_panel_count_mismatch(tmp_path):
    plan = ComicPagePlan(page_id="p1", panels=[PagePanelSpec(panel_id="1")])
    with pytest.raises(ValueError):
        compose_page_panels(plan, [], tmp_path / "b.png", tmp_path / "l.png")
