"""tests/test_layout_page_aspect.py — page aspect targets a manga page shape.

The row-packing layout (commit 5538ba7) tiles every panel into one full-width
row per page: a 3-panel page came out 1400x381 (3.67:1) — an extreme strip.
Readers see a sliver in fit-width mode and a horizontal scrollbar at 100%.
The page canvas must pack panels into several rows so the whole page lands
near a portrait manga page (2:3), instead of one long ribbon.
"""

from PIL import Image

from core.comic.layout import LayoutEngine, PanelImage


def _panel(w: int, h: int) -> PanelImage:
    return PanelImage(image=Image.new("RGB", (w, h), (120, 120, 120)))


def test_three_panels_pack_into_rows_near_manga_aspect():
    eng = LayoutEngine(page_width=1400)
    canvas = eng.compose_single_page([_panel(1536, 1024) for _ in range(3)])
    w, h = canvas.size
    ratio = w / h
    # 3.67:1 strip is the bug; a manga page is near 0.7. Allow 2 rows (1.83)
    # through 0.45 (a bit taller than 2:3 portrait).
    assert 0.45 <= ratio <= 1.83, f"page is a strip: {w}x{h} ({ratio:.2f}:1)"


def test_two_panels_page_not_a_wide_strip():
    eng = LayoutEngine(page_width=1400)
    canvas = eng.compose_single_page([_panel(1536, 1024), _panel(1024, 1536)])
    w, h = canvas.size
    assert w / h <= 2.0, f"page too wide: {w}x{h}"


def test_many_panels_still_paginate_reasonably():
    eng = LayoutEngine(page_width=1400)
    panels = [_panel(1536, 1024) for _ in range(8)]
    pages = []
    for group in (panels[:4], panels[4:]):
        pages.append(eng.compose_single_page(group))
    for canvas in pages:
        w, h = canvas.size
        assert 0.45 <= w / h <= 1.83, f"page out of shape: {w}x{h}"


def test_odd_last_row_still_fills_width():
    eng = LayoutEngine(page_width=1400)
    canvas = eng.compose_single_page([_panel(1536, 1024) for _ in range(3)])
    # Bottom row (single panel) must still cover the full page width.
    px = canvas.load()
    W, H = canvas.size
    row = [px[x, H - 1] for x in range(0, W, 7)]
    whites = sum(1 for c in row if c[0] > 240 and c[1] > 240 and c[2] > 240)
    assert whites == 0, f"blank strip on last row ({whites} white samples)"


def test_mixed_ratios_page_stays_in_shape():
    eng = LayoutEngine(page_width=1400)
    panels = [_panel(1536, 1024), _panel(1024, 1024), _panel(1024, 1536)]
    canvas = eng.compose_single_page(panels)
    w, h = canvas.size
    assert 0.45 <= w / h <= 1.83, f"mixed page is a strip: {w}x{h}"
