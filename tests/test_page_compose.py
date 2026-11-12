"""tests/test_page_compose.py — local composition of page-plan panels."""

import numpy as np
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


def test_three_square_panels_leave_no_full_width_blank_bands(tmp_path):
    """Square art must not letterbox any blank bands into the page.

    The panel images are square (1024x1024 like the provider default). The
    DP page packing lays them out 2-up over 1-up (1400x2100, ratio 0.67 —
    right at the manga page shape); every row is packed full-bleed, so no
    fully-blank row may appear anywhere on the page.
    """
    plan = ComicPagePlan(
        page_id="p1",
        panels=[PagePanelSpec(panel_id=str(i + 1)) for i in range(3)],
    )
    images = [Image.new("RGB", (1024, 1024), (120, 140, 160)) for _ in range(3)]
    blank = tmp_path / "blank.png"
    lettered = tmp_path / "lettered.png"
    compose_page_panels(plan, images, blank, lettered)

    arr = np.asarray(Image.open(blank).convert("L"))
    page_h, page_w = arr.shape
    full_blank_rows = int(((arr > 245).mean(axis=1) > 0.98).sum())
    assert 0.45 <= page_w / page_h <= 1.83, (
        f"square panels must render a manga-shaped page, got {page_w}x{page_h}"
    )
    assert full_blank_rows < page_h * 0.1, (
        f"{full_blank_rows}/{page_h} fully blank rows — panels are letterboxed"
    )
