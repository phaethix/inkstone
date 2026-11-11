"""tests/test_layout_mixed_ratios.py — mixed-aspect rows must not leave blank blocks.

User screenshots of comic.pdf showed large pure-white rectangles: a 2-up row
packing a 1536x1024 wide panel next to an 832x1248 tall panel, with a fixed
700px cell width and ``ImageOps.contain``, letterboxes the wide art by 291px
top and bottom — half of that cell is white. The shape_hint-driven panel sizes
made ratios even more mixed (1.5 / 0.67 / 1.0 on one page), so the fixed grid
plus contain amplified the blank area.

The fix sizes each row's columns in proportion to the panels' own aspect
ratios: wide art gets a wider column, tall art a narrower one, and every
panel in the row ends up the same height with zero letterboxing, without any
cropping or squashing.
"""

import numpy as np
from PIL import Image

from core.comic.layout import LayoutEngine, PanelImage


def _solid(w: int, h: int, color: tuple[int, int, int]) -> Image.Image:
    return Image.new("RGB", (w, h), color)


def _blank_cell_fraction(canvas: Image.Image, cols: int) -> list[float]:
    """Largest contiguous pure-white block inside each grid cell.

    Whole-page row averages hide a blank half: a 55%-white left cell next to
    a 0%-white right cell averages to 27%. Measure each column band on its
    own, matching how a reader sees the white rectangle.
    """
    arr = np.asarray(canvas.convert("RGB"))
    white = (arr > 245).all(axis=2)
    out: list[float] = []
    for c in range(cols):
        x0, x1 = arr.shape[1] * c // cols, arr.shape[1] * (c + 1) // cols
        band = white[:, x0:x1]
        out.append(band.mean())
    return out


def test_mixed_aspect_row_has_no_blank_bands():
    """A wide + tall row must be full-bleed: no large pure-white rectangle.

    Reproduces page_c0000_p0000 row 0 (1536x1024 next to 832x1248), the
    exact cell pair in the user screenshot with 291px white bands.
    """
    eng = LayoutEngine(bg=(255, 255, 255))
    wide = _solid(1536, 1024, (30, 60, 90))
    tall = _solid(832, 1248, (120, 60, 30))
    canvas = eng.compose_single_page([PanelImage(wide), PanelImage(tall)])
    fractions = _blank_cell_fraction(canvas, 2)
    assert max(fractions) < 0.02, (
        f"white fraction per cell {[round(f, 3) for f in fractions]}: "
        "mixed-aspect row is letterboxed instead of packed"
    )


def test_mixed_aspect_row_column_widths_follow_ratios():
    """The wide panel must get the wider column, not an equal 700/700 split."""
    eng = LayoutEngine(bg=(255, 255, 255))
    wide_color, tall_color = (30, 60, 90), (120, 60, 30)
    wide = _solid(1536, 1024, wide_color)
    tall = _solid(832, 1248, tall_color)
    canvas = eng.compose_single_page([PanelImage(wide), PanelImage(tall)])
    arr = np.asarray(canvas.convert("RGB"))
    mid = arr.shape[0] // 2
    wide_mask = (arr[mid, :, 0] == wide_color[0]) & (arr[mid, :, 2] == wide_color[2])
    tall_mask = (arr[mid, :, 0] == tall_color[0]) & (arr[mid, :, 2] == tall_color[2])
    wide_span = int(wide_mask.sum())
    tall_span = int(tall_mask.sum())
    assert wide_span > tall_span, (
        f"wide column {wide_span}px is not wider than tall column {tall_span}px"
    )
    assert wide_span + tall_span == arr.shape[1], "columns must tile the full width"


def test_four_mixed_panels_no_blank_in_either_row():
    """Full page_c0000_p0000 shape mix across two rows: no white block."""
    eng = LayoutEngine(bg=(255, 255, 255))
    panels = [
        PanelImage(_solid(1536, 1024, (10, 20, 30))),  # wide
        PanelImage(_solid(832, 1248, (40, 50, 60))),  # tall
        PanelImage(_solid(1536, 1024, (70, 80, 90))),  # rect (wide)
        PanelImage(_solid(1024, 1024, (100, 110, 120))),  # inset (square)
    ]
    canvas = eng.compose_single_page(panels)
    # Two rows of two columns: check each half-height x half-width quadrant.
    arr = np.asarray(canvas.convert("RGB"))
    white = (arr > 245).all(axis=2)
    h, w = white.shape
    quadrants = [
        white[: h // 2, : w // 2],
        white[: h // 2, w // 2 :],
        white[h // 2 :, : w // 2],
        white[h // 2 :, w // 2 :],
    ]
    assert max(q.mean() for q in quadrants) < 0.05, (
        f"white fraction per quadrant {[round(q.mean(), 3) for q in quadrants]}"
    )


def test_equal_ratio_row_keeps_equal_columns():
    """Square + square still splits evenly; ratio sizing must not distort
    the ordinary 2x2 case."""
    eng = LayoutEngine(bg=(255, 255, 255))
    a, b = (10, 20, 30), (40, 50, 60)
    canvas = eng.compose_single_page(
        [PanelImage(_solid(1024, 1024, a)), PanelImage(_solid(1024, 1024, b))]
    )
    arr = np.asarray(canvas.convert("RGB"))
    mid = arr.shape[0] // 2
    left = (arr[mid, :700, 0] == a[0]).all()
    right = (arr[mid, 700:, 0] == b[0]).all()
    assert left and right, "equal-ratio row must keep the 700/700 split"
