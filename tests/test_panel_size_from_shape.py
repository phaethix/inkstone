"""tests/test_panel_size_from_shape.py — panel canvas size follows shape_hint.

panel_compose renders every page-plan panel at the fixed ``INKSTONE_PANEL_SIZE``
square (1024x1024). The page plan's ``shape_hint`` — the planner's own geometry
vocabulary (`wide` horizontal band, `tall` standing figure, `square`, `rect`)
— never reached the size decision, so a plan asking for "One long horizontal
band: ... two small figures at the table" came back as a full-bleed square
portrait of one character. The last two pages of c0001 are exactly that: a
widescreen_scene and an object_closeup each filling the whole page with a
1024-square up-scaled to page width, showing one chest-up figure where the
plan staged a wide two-figure band.
"""

from core.comic.panel_size import panel_size_for_shape


def test_wide_shape_gets_landscape_canvas():
    assert panel_size_for_shape("wide") == "1536x1024"


def test_rect_shape_gets_landscape_canvas():
    assert panel_size_for_shape("rect") == "1536x1024"


def test_tall_shape_gets_portrait_canvas():
    assert panel_size_for_shape("tall") == "1024x1536"


def test_square_and_unknown_keep_default():
    assert panel_size_for_shape("square") == "1024x1024"
    assert panel_size_for_shape("") == "1024x1024"
    assert panel_size_for_shape("inset") == "1024x1024"
    assert panel_size_for_shape(None) == "1024x1024"


def test_default_is_overridable():
    assert panel_size_for_shape("wide", default="800x600") == "1536x1024"
    assert panel_size_for_shape("inset", default="800x600") == "800x600"
