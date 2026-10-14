"""tests/test_layout_cjk.py — CJK dialogue font resolution and rendering (no network).

Regression guard for the tofu-bubble bug: Pillow's builtin default font has no
CJK glyphs, so CJK dialogue used to render as hollow boxes while every
pixel-level assertion still passed. These tests assert on *content*, not just
on "something was drawn".
"""

import logging

import pytest
from PIL import Image, ImageDraw, ImageFont

from core.comic import fonts
from core.comic.layout import LayoutEngine, PanelImage


@pytest.fixture(autouse=True)
def _reset_font_caches():
    fonts.reset_caches()
    yield
    fonts.reset_caches()


def _system_cjk_font() -> str | None:
    """First platform CJK candidate that exists and really covers CJK, else None."""
    for path in fonts._CJK_FONT_CANDIDATES:
        font = fonts._try_load(path, fonts.DEFAULT_CJK_FONT_SIZE)
        if font is not None:
            return path
    return None


# --------------------------------------------------------------------- #
# text_requires_cjk
# --------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "text,expected",
    [
        ("你好，世界", True),
        ("叶文洁说：不要回答", True),
        ("かなカナ", True),
        ("한국어", True),
        ("全角：！（）", True),
        ("hello world", False),
        ("", False),
        ("numbers 123 & punctuation?!", False),
    ],
)
def test_text_requires_cjk(text, expected):
    assert fonts.text_requires_cjk(text) is expected


# --------------------------------------------------------------------- #
# font_covers_cjk
# --------------------------------------------------------------------- #
def test_default_font_does_not_cover_cjk():
    # The root cause of the tofu bug, asserted directly so a future Pillow
    # upgrade that ships CJK glyphs surfaces here as a deliberate decision.
    assert fonts.font_covers_cjk(ImageFont.load_default()) is False


# --------------------------------------------------------------------- #
# resolve_font
# --------------------------------------------------------------------- #
def test_resolve_font_keeps_default_for_latin():
    font, source = fonts.resolve_font("hello world")
    assert source == "default"
    assert font is not None


def test_resolve_font_warns_and_falls_back_when_no_cjk_font(monkeypatch, caplog):
    monkeypatch.setattr(fonts, "_CJK_FONT_CANDIDATES", ())
    monkeypatch.setenv("INKSTONE_FONT_PATH", "/nonexistent/font.ttf")
    with caplog.at_level(logging.WARNING, logger="core.comic.fonts"):
        font, source = fonts.resolve_font("你好")
        # Second call must not re-warn (one loud warning, not a flood).
        fonts.resolve_font("你好 again")
    assert source == "default-no-cjk"
    assert font is not None  # degraded, but never crashes
    warnings = [r for r in caplog.records if "No CJK-capable font" in r.message]
    assert len(warnings) == 1


def test_resolve_font_recovers_from_bad_env_override(monkeypatch):
    # A bogus INKSTONE_FONT_PATH must not disable the platform scan.
    monkeypatch.setenv("INKSTONE_FONT_PATH", "/nonexistent/font.ttf")
    if _system_cjk_font() is None:
        pytest.skip("no CJK font on this system")
    font, source = fonts.resolve_font("你好")
    assert source != "default-no-cjk"
    assert fonts.font_covers_cjk(font) is True


def test_resolve_font_prefers_explicit_font_path(tmp_path, monkeypatch):
    real = _system_cjk_font()
    if real is None:
        pytest.skip("no CJK font on this system")
    monkeypatch.delenv("INKSTONE_FONT_PATH", raising=False)
    font, source = fonts.resolve_font("你好", font_path=real)
    assert source == f"font_path:{real}"
    assert fonts.font_covers_cjk(font) is True


# --------------------------------------------------------------------- #
# Layout integration: bubbles must not be tofu
# --------------------------------------------------------------------- #
def _ink(draw_target_size, draw_fn) -> bytes:
    img = Image.new("RGB", draw_target_size, "white")
    draw_fn(ImageDraw.Draw(img))
    return img.tobytes()


def test_cjk_dialogue_bubble_is_not_tofu(monkeypatch):
    if _system_cjk_font() is None:
        pytest.skip("no CJK font on this system")
    monkeypatch.delenv("INKSTONE_FONT_PATH", raising=False)
    eng = LayoutEngine()
    text = "叶文洁说：不要回答。"

    resolved = _ink((400, 120), lambda d: eng._draw_bubble(d, (10, 10, 380, 100), text))

    # Force the degraded path (no CJK font anywhere) for the same text.
    monkeypatch.setattr(fonts, "_CJK_FONT_CANDIDATES", ())
    fonts.reset_caches()
    degraded = _ink((400, 120), lambda d: eng._draw_bubble(d, (10, 10, 380, 100), text))

    assert resolved != degraded


def test_engine_compose_with_cjk_dialogue(tmp_path, monkeypatch):
    if _system_cjk_font() is None:
        pytest.skip("no CJK font on this system")
    monkeypatch.delenv("INKSTONE_FONT_PATH", raising=False)
    eng = LayoutEngine(page_width=400, cell_height=100)
    paths = eng.compose(
        [PanelImage(Image.new("RGB", (400, 100), (200, 200, 200)), dialogue="你好，世界。")],
        tmp_path,
        layout_mode="page",
    )
    assert len(paths) == 1
    # Bubble expands the cell and renders without raising.
    assert Image.open(paths[0]).height >= 100


def test_a_long_cjk_caption_stays_inside_its_bar():
    path = _system_cjk_font()
    if path is None:
        pytest.skip("no CJK font on this system")
    engine = LayoutEngine(font_path=path)
    text = "石猴跳进瀑布发现水帘洞里面有铁板桥和石屋群猴才知道那里是洞天福地"
    short = "次日"
    wide, tall = 220, 80
    fitted, _lines = engine._fitted_caption(short, wide - 20, tall)
    assert getattr(fitted, "size", None) == fonts.DEFAULT_CJK_FONT_SIZE
    box = (16, 16, 120, 40)
    canvas = Image.new("RGB", (200, 160), (180, 180, 180))
    engine._draw_caption(ImageDraw.Draw(canvas), box, text)
    _pixels_outside_stay_background(canvas, box, (180, 180, 180))
    x, y, w, h = box
    for py in range(y + h + 3, canvas.height):
        for px in range(canvas.width):
            assert canvas.getpixel((px, py)) == (180, 180, 180)
    ink = [
        canvas.getpixel((px, py))
        for py in range(y + 4, y + h - 4)
        for px in range(x + 4, x + w - 4)
    ]
    assert any(pixel != (245, 245, 240) for pixel in ink)


def _pixels_outside_stay_background(canvas, box, background):
    x, y, w, h = box
    for px in range(x, x + w):
        assert canvas.getpixel((px, y - 1)) == background
        assert canvas.getpixel((px, y + h)) == background
    for py in range(y, y + h):
        assert canvas.getpixel((x - 1, py)) == background
        assert canvas.getpixel((x + w, py)) == background


def _pixels_below_stay_background(canvas, box, background):
    _x, y, _w, h = box
    for py in range(y + h + 8, canvas.height):
        for px in range(canvas.width):
            assert canvas.getpixel((px, py)) == background


def test_a_long_cjk_bubble_and_sfx_stay_inside_their_boxes():
    path = _system_cjk_font()
    if path is None:
        pytest.skip("no CJK font on this system")
    engine = LayoutEngine(font_path=path)
    text = "石猴跳进瀑布发现水帘洞里面有铁板桥和石屋群猴才知道那里是洞天福地"
    short_font, _lines = engine._fitted_caption("你好", 200, 80)
    assert getattr(short_font, "size", None) == fonts.DEFAULT_CJK_FONT_SIZE
    background = (180, 180, 180)
    bubble = (16, 16, 120, 40)
    canvas = Image.new("RGB", (200, 160), background)
    engine._draw_bubble(ImageDraw.Draw(canvas), bubble, text)
    _pixels_below_stay_background(canvas, bubble, background)
    _pixels_outside_stay_background(canvas, bubble, background)
    assert _has_dark_pixel(canvas, bubble)
    sfx = (16, 16, 100, 36)
    canvas = Image.new("RGB", (200, 160), background)
    engine._draw_sfx(ImageDraw.Draw(canvas), sfx, text)
    _pixels_below_stay_background(canvas, sfx, background)
    _pixels_outside_stay_background(canvas, sfx, background)
    assert _has_dark_pixel(canvas, sfx)


def test_lettering_stays_inside_a_box_narrower_than_one_glyph():
    path = _system_cjk_font()
    if path is None:
        pytest.skip("no CJK font on this system")
    engine = LayoutEngine(font_path=path)
    text = "石猴跳进瀑布发现水帘洞"
    background = (180, 180, 180)
    boxes = (
        (engine._draw_caption, (16, 16, 18, 80)),
        (engine._draw_bubble, (16, 16, 18, 80)),
        (engine._draw_sfx, (16, 16, 18, 40)),
    )
    for drawer, box in boxes:
        canvas = Image.new("RGB", (80, 140), background)
        drawer(ImageDraw.Draw(canvas), box, text)
        _pixels_outside_stay_background(canvas, box, background)


def test_cjk_punctuation_stays_with_the_neighboring_glyph():
    path = _system_cjk_font()
    if path is None:
        pytest.skip("no CJK font on this system")
    engine = LayoutEngine(font_path=path)
    font, _source = fonts.resolve_font("石，「", font_path=path)
    closing = "石猴，跳"
    width = font.getlength("石猴")
    assert font.getlength("石猴，") > width
    assert font.getlength("猴，") <= width
    lines = engine._wrap_text(closing, font, max_width=width)
    assert "".join(lines) == closing
    assert all(not line.startswith("，") for line in lines)
    assert any(line.endswith("，") for line in lines)
    opening = "石「猴"
    width = font.getlength("石「")
    assert font.getlength("石「猴") > width
    assert font.getlength("「猴") <= width
    lines = engine._wrap_text(opening, font, max_width=width)
    assert "".join(lines) == opening
    assert all(not line.endswith("「") for line in lines)
    assert any(line.startswith("「") for line in lines)


def test_a_closing_mark_does_not_strand_an_opening_mark():
    path = _system_cjk_font()
    if path is None:
        pytest.skip("no CJK font on this system")
    engine = LayoutEngine(font_path=path)
    font, _source = fonts.resolve_font("他说：“跳”", font_path=path)
    text = "他说：“跳”"
    width = font.getlength("他说")
    lines = engine._wrap_text(text, font, max_width=width)
    assert "".join(lines) == text
    assert any(line.startswith("“") and "跳" in line for line in lines)
    assert all(not line.endswith(("“", "「")) for line in lines)


def test_an_ellipsis_hangs_on_the_previous_glyph():
    path = _system_cjk_font()
    if path is None:
        pytest.skip("no CJK font on this system")
    engine = LayoutEngine(font_path=path)
    font, _source = fonts.resolve_font("石猴跳……", font_path=path)
    text = "石猴跳……"
    width = font.getlength("石猴跳")
    assert font.getlength("跳……") <= width
    assert font.getlength(text) > width
    lines = engine._wrap_text(text, font, max_width=width)
    assert "".join(lines) == text
    assert any(line.endswith("……") for line in lines)
    assert all(not line.startswith("…") for line in lines)


def test_a_halfwidth_comma_hangs_on_the_previous_cjk_glyph():
    path = _system_cjk_font()
    if path is None:
        pytest.skip("no CJK font on this system")
    engine = LayoutEngine(font_path=path)
    font, _source = fonts.resolve_font("石猴,跳", font_path=path)
    text = "石猴,跳"
    width = font.getlength("石猴")
    assert font.getlength("石猴,") > width
    assert font.getlength("猴,") <= width
    lines = engine._wrap_text(text, font, max_width=width)
    assert "".join(lines) == text
    assert any(line.endswith(",") for line in lines)
    assert all(not line.startswith(",") for line in lines)
    latin = "石猴 hello,"
    latin_width = font.getlength("hello")
    latin_lines = engine._wrap_text(latin, font, max_width=latin_width)
    assert "".join(latin_lines).replace(" ", "") == latin.replace(" ", "")
    assert any("hello" in line for line in latin_lines)


def test_a_halfwidth_opening_mark_stays_with_the_next_glyph():
    path = _system_cjk_font()
    if path is None:
        pytest.skip("no CJK font on this system")
    engine = LayoutEngine(font_path=path)
    font, _source = fonts.resolve_font("石(猴", font_path=path)
    text = "石(猴"
    width = font.getlength("石(")
    assert font.getlength("石(猴") > width
    assert font.getlength("(猴") <= width
    lines = engine._wrap_text(text, font, max_width=width)
    assert "".join(lines) == text
    assert any(line.startswith("(") for line in lines)
    assert all(not line.endswith(("(", "[", "{")) for line in lines)


def test_a_dash_hangs_on_the_previous_cjk_glyph():
    path = _system_cjk_font()
    if path is None:
        pytest.skip("no CJK font on this system")
    engine = LayoutEngine(font_path=path)
    font, _source = fonts.resolve_font("石猴—～·", font_path=path)
    width = font.getlength("石猴")
    for mark in ("—", "～", "·"):
        text = f"石猴{mark}跳"
        assert font.getlength(f"猴{mark}") <= width
        assert font.getlength(f"石猴{mark}") > width
        lines = engine._wrap_text(text, font, max_width=width)
        assert "".join(lines) == text
        assert any(line.endswith(mark) for line in lines)
        assert all(not line.startswith(mark) for line in lines)
    pair = "石猴——跳"
    pair_width = font.getlength("石猴跳")
    assert font.getlength("猴——") <= pair_width
    assert font.getlength("石猴——") > pair_width
    lines = engine._wrap_text(pair, font, max_width=pair_width)
    assert "".join(lines) == pair
    assert any(line.endswith("——") for line in lines)
    assert all(not line.startswith("—") for line in lines)


def test_a_unit_mark_hangs_on_the_previous_digit():
    path = _system_cjk_font()
    if path is None:
        pytest.skip("no CJK font on this system")
    engine = LayoutEngine(font_path=path)
    font, _source = fonts.resolve_font("50%℃", font_path=path)
    cases = (
        ("50%的", "50", "50%", "0%", "%"),
        ("30℃后", "30", "30℃", "0℃", "℃"),
    )
    for text, head, whole, hung, mark in cases:
        width = font.getlength(head if mark == "℃" else hung)
        assert font.getlength(head) <= width
        assert font.getlength(hung) <= width
        assert font.getlength(whole) > width
        lines = engine._wrap_text(text, font, max_width=width)
        assert "".join(lines) == text
        assert any(line.endswith(mark) for line in lines)
        assert all(not line.startswith(mark) for line in lines)


def test_a_fullwidth_percent_hangs_on_the_previous_digit():
    path = _system_cjk_font()
    if path is None:
        pytest.skip("no CJK font on this system")
    engine = LayoutEngine(font_path=path)
    font, _source = fonts.resolve_font("50％", font_path=path)
    cases = (
        ("50％的", "50", "50％", "0％"),
        ("５０％的", "５０", "５０％", "０％"),
    )
    for text, head, whole, hung in cases:
        width = font.getlength(hung if head[0] == "5" else head)
        assert font.getlength(head) <= width
        assert font.getlength(hung) <= width
        assert font.getlength(whole) > width
        lines = engine._wrap_text(text, font, max_width=width)
        assert "".join(lines) == text
        assert any(line.endswith("％") for line in lines)
        assert all(not line.startswith("％") for line in lines)


def test_a_letter_unit_hangs_on_the_previous_digit():
    path = _system_cjk_font()
    if path is None:
        pytest.skip("no CJK font on this system")
    engine = LayoutEngine(font_path=path)
    font, _source = fonts.resolve_font("50cm", font_path=path)
    cases = (
        ("约50cm后", "50cm", "0cm", "cm"),
        ("重30kg的", "30kg", "0kg", "kg"),
    )
    for text, whole, hung, mark in cases:
        width = font.getlength(hung)
        assert font.getlength(whole) > width
        assert font.getlength(hung) <= width
        lines = engine._wrap_text(text, font, max_width=width)
        assert "".join(lines) == text
        assert any(mark in line for line in lines)
        assert all(mark in line or not any(char in line for char in mark) for line in lines)
        assert all(not line.startswith(mark) for line in lines)


def test_a_decimal_unit_hangs_on_the_previous_digit():
    path = _system_cjk_font()
    if path is None:
        pytest.skip("no CJK font on this system")
    engine = LayoutEngine(font_path=path)
    font, _source = fonts.resolve_font("3.14cm", font_path=path)
    cases = (
        ("约3.14cm后", "3.14cm", "4cm", "cm"),
        ("重2.5kg的", "2.5kg", "5kg", "kg"),
    )
    for text, whole, hung, mark in cases:
        width = font.getlength(hung)
        assert font.getlength(whole) > width
        assert font.getlength(hung) <= width
        lines = engine._wrap_text(text, font, max_width=width)
        assert "".join(lines) == text
        assert any(mark in line for line in lines)
        assert all(mark in line or not any(char in line for char in mark) for line in lines)
        assert all(not line.startswith(mark) for line in lines)


def test_a_permille_hangs_on_the_previous_digit():
    path = _system_cjk_font()
    if path is None:
        pytest.skip("no CJK font on this system")
    engine = LayoutEngine(font_path=path)
    font, _source = fonts.resolve_font("90‰′″的", font_path=path)
    cases = (
        ("90‰的", "90‰", "0‰", "‰"),
        ("90′的", "90′", "0′", "′"),
        ("90″的", "90″", "0″", "″"),
    )
    for text, whole, hung, mark in cases:
        width = font.getlength(hung)
        assert font.getlength("90") <= width
        assert font.getlength(hung) <= width
        assert font.getlength(whole) > width
        lines = engine._wrap_text(text, font, max_width=width)
        assert "".join(lines) == text
        assert any(line.endswith(mark) for line in lines)
        assert all(not line.startswith(mark) for line in lines)


def test_a_spaced_letter_unit_hangs_on_the_previous_digit():
    path = _system_cjk_font()
    if path is None:
        pytest.skip("no CJK font on this system")
    engine = LayoutEngine(font_path=path)
    font, _source = fonts.resolve_font("约50 cm后", font_path=path)
    cases = (
        ("约50 cm后", "50 cm", "0 cm", "cm"),
        ("重30 kg的", "30 kg", "0 kg", "kg"),
    )
    for text, whole, hung, mark in cases:
        width = font.getlength(hung)
        assert font.getlength(whole) > width
        assert font.getlength(hung) <= width
        lines = engine._wrap_text(text, font, max_width=width)
        assert "".join(lines) == text
        assert any(mark in line for line in lines)
        assert all(mark in line or not any(char in line for char in mark) for line in lines)
        assert all(not line.startswith(mark) for line in lines)


def test_a_symbol_unit_hangs_on_the_previous_digit():
    path = _system_cjk_font()
    if path is None:
        pytest.skip("no CJK font on this system")
    engine = LayoutEngine(font_path=path)
    font, _source = fonts.resolve_font("30℉Ωμ的", font_path=path)
    cases = (
        ("30℉后", "30", "30℉", "0℉", "℉"),
        ("50Ω的", "50", "50Ω", "0Ω", "Ω"),
        ("50μ后", "50", "50μ", "0μ", "μ"),
    )
    for text, digits, whole, hung, mark in cases:
        width = font.getlength(hung)
        assert font.getlength(digits) <= width
        assert font.getlength(hung) <= width
        assert font.getlength(whole) > width
        lines = engine._wrap_text(text, font, max_width=width)
        assert "".join(lines) == text
        assert any(line.endswith(mark) for line in lines)
        assert all(not line.startswith(mark) for line in lines)


def test_a_compat_unit_hangs_on_the_previous_digit():
    path = _system_cjk_font()
    if path is None:
        pytest.skip("no CJK font on this system")
    engine = LayoutEngine(font_path=path)
    font, _source = fonts.resolve_font("50㎡㎏㎝的", font_path=path)
    cases = (
        ("50㎡的", "50", "50㎡", "0㎡", "㎡"),
        ("50㎏的", "50", "50㎏", "0㎏", "㎏"),
        ("50㎝的", "50", "50㎝", "0㎝", "㎝"),
        ("50㎜的", "50", "50㎜", "0㎜", "㎜"),
        ("50㎞的", "50", "50㎞", "0㎞", "㎞"),
    )
    for text, digits, whole, hung, mark in cases:
        width = font.getlength(hung)
        assert font.getlength(digits) <= width
        assert font.getlength(hung) <= width
        assert font.getlength(whole) > width
        lines = engine._wrap_text(text, font, max_width=width)
        assert "".join(lines) == text
        assert any(line.endswith(mark) for line in lines)
        assert all(not line.startswith(mark) for line in lines)


def test_a_currency_mark_hangs_on_the_previous_digit():
    path = _system_cjk_font()
    if path is None:
        pytest.skip("no CJK font on this system")
    engine = LayoutEngine(font_path=path)
    font, _source = fonts.resolve_font("50¥元的", font_path=path)
    cases = (
        ("50¥的", "50", "50¥", "0¥", "¥"),
        ("50$的", "50", "50$", "0$", "$"),
        ("50€的", "50", "50€", "0€", "€"),
        ("50£的", "50", "50£", "0£", "£"),
        ("50￥的", "50", "50￥", "0￥", "￥"),
        ("50元的", "50", "50元", "0元", "元"),
    )
    for text, digits, whole, hung, mark in cases:
        width = font.getlength(hung)
        assert font.getlength(digits) <= width
        assert font.getlength(hung) <= width
        assert font.getlength(whole) > width
        lines = engine._wrap_text(text, font, max_width=width)
        assert "".join(lines) == text
        assert any(line.endswith(mark) for line in lines)
        assert all(not line.startswith(mark) for line in lines)


def test_a_spaced_currency_mark_keeps_its_space():
    path = _system_cjk_font()
    if path is None:
        pytest.skip("no CJK font on this system")
    engine = LayoutEngine(font_path=path)
    font, _source = fonts.resolve_font("50 元¥的", font_path=path)
    cases = (
        ("约50 元后", "50 元", "0 元", "元"),
        ("约50 ¥后", "50 ¥", "0 ¥", "¥"),
    )
    for text, whole, hung, mark in cases:
        width = font.getlength(hung)
        assert font.getlength(whole) > width
        assert font.getlength(hung) <= width
        lines = engine._wrap_text(text, font, max_width=width)
        assert "".join(lines) == text
        assert any(line.endswith(f" {mark}") for line in lines)
        assert all(not line.startswith(mark) for line in lines)


def test_a_currency_word_hangs_on_the_previous_digit():
    path = _system_cjk_font()
    if path is None:
        pytest.skip("no CJK font on this system")
    engine = LayoutEngine(font_path=path)
    font, _source = fonts.resolve_font("50块角分的", font_path=path)
    cases = (
        ("50块的", "50", "50块", "0块", "块"),
        ("50角的", "50", "50角", "0角", "角"),
        ("50分的", "50", "50分", "0分", "分"),
    )
    for text, digits, whole, hung, mark in cases:
        width = font.getlength(hung)
        assert font.getlength(digits) <= width
        assert font.getlength(hung) <= width
        assert font.getlength(whole) > width
        lines = engine._wrap_text(text, font, max_width=width)
        assert "".join(lines) == text
        assert any(line.endswith(mark) for line in lines)
        assert all(not line.startswith(mark) for line in lines)
    text = "约50 块后"
    width = font.getlength("0 块")
    assert font.getlength("50 块") > width
    lines = engine._wrap_text(text, font, max_width=width)
    assert "".join(lines) == text
    assert any(line.endswith(" 块") for line in lines)


def test_a_thousands_group_stays_together():
    path = _system_cjk_font()
    if path is None:
        pytest.skip("no CJK font on this system")
    engine = LayoutEngine(font_path=path)
    font, _source = fonts.resolve_font("1,000人", font_path=path)
    cases = (
        ("1,000人", "1,000", "000", "1,"),
        ("12,345人", "12,345", "345", "12,"),
    )
    for text, whole, group, hung in cases:
        width = font.getlength(group)
        assert font.getlength(whole) > width
        assert font.getlength(group) <= width
        assert font.getlength(hung) <= width
        lines = engine._wrap_text(text, font, max_width=width)
        assert "".join(lines) == text
        assert any(group in line for line in lines)
        assert all(",0" not in line and ",00" not in line for line in lines)


def test_a_grouped_decimal_keeps_its_groups():
    path = _system_cjk_font()
    if path is None:
        pytest.skip("no CJK font on this system")
    engine = LayoutEngine(font_path=path)
    font, _source = fonts.resolve_font("1,000.50元", font_path=path)
    text = "1,000.50元"
    width = font.getlength("000.")
    assert font.getlength("1,000.50") > width
    assert font.getlength("000.") <= width
    assert font.getlength("50") <= width
    lines = engine._wrap_text(text, font, max_width=width)
    assert "".join(lines) == text
    assert any("000." in line for line in lines)
    assert all(",00" not in line for line in lines)
    assert all("0.5" not in line for line in lines)


def test_a_grouped_number_keeps_its_letter_unit():
    path = _system_cjk_font()
    if path is None:
        pytest.skip("no CJK font on this system")
    engine = LayoutEngine(font_path=path)
    font, _source = fonts.resolve_font("1,000.50cm的", font_path=path)
    text = "1,000.50cm后"
    width = font.getlength("50cm")
    assert font.getlength("1,000.50") > width
    assert font.getlength("000.") <= width
    assert font.getlength("50cm") <= width
    lines = engine._wrap_text(text, font, max_width=width)
    assert "".join(lines) == text
    assert any("000." in line for line in lines)
    assert any("cm" in line and any(char.isdigit() for char in line) for line in lines)
    assert all("cm" in line or ("c" not in line and "m" not in line) for line in lines)
    assert all(",00" not in line for line in lines)


def test_a_classifier_hangs_on_the_previous_digit():
    path = _system_cjk_font()
    if path is None:
        pytest.skip("no CJK font on this system")
    engine = LayoutEngine(font_path=path)
    font, _source = fonts.resolve_font("50个只名位岁的", font_path=path)
    cases = (
        ("50个的", "50", "50个", "0个", "个"),
        ("50只的", "50", "50只", "0只", "只"),
        ("50名的", "50", "50名", "0名", "名"),
        ("50位的", "50", "50位", "0位", "位"),
        ("50岁的", "50", "50岁", "0岁", "岁"),
    )
    for text, digits, whole, hung, mark in cases:
        width = font.getlength(hung)
        assert font.getlength(digits) <= width
        assert font.getlength(hung) <= width
        assert font.getlength(whole) > width
        lines = engine._wrap_text(text, font, max_width=width)
        assert "".join(lines) == text
        assert any(line.endswith(mark) for line in lines)
        assert all(not line.startswith(mark) for line in lines)
    text = "约50 个后"
    width = font.getlength("0 个")
    assert font.getlength("50 个") > width
    lines = engine._wrap_text(text, font, max_width=width)
    assert "".join(lines) == text
    assert any(line.endswith(" 个") for line in lines)
    particle = "50的人"
    width = font.getlength("0的")
    lines = engine._wrap_text(particle, font, max_width=width)
    assert "".join(lines) == particle
    assert any(line.startswith("的") for line in lines)


def test_a_clock_keeps_its_minutes():
    path = _system_cjk_font()
    if path is None:
        pytest.skip("no CJK font on this system")
    engine = LayoutEngine(font_path=path)
    font, _source = fonts.resolve_font("12:30到的", font_path=path)
    cases = (
        ("12:30到", "12:30", "2:30", ":30"),
        ("09:05到", "09:05", "9:05", ":05"),
    )
    for text, whole, hung, minutes in cases:
        width = font.getlength(hung)
        assert font.getlength(whole) > width
        assert font.getlength(hung) <= width
        lines = engine._wrap_text(text, font, max_width=width)
        assert "".join(lines) == text
        assert any(hung in line for line in lines)
        torn = minutes[:2]
        assert all(torn not in line or minutes in line for line in lines)


def test_a_clock_keeps_its_seconds():
    path = _system_cjk_font()
    if path is None:
        pytest.skip("no CJK font on this system")
    engine = LayoutEngine(font_path=path)
    font, _source = fonts.resolve_font("12:30:45后的", font_path=path)
    text = "12:30:45后"
    cases = (
        ("30:45", "30:45"),
        ("0:45", "30"),
    )
    for sample, kept in cases:
        width = font.getlength(sample)
        assert font.getlength("12:30:45") > width
        assert font.getlength(kept) <= width
        lines = engine._wrap_text(text, font, max_width=width)
        assert "".join(lines) == text
        assert any(kept in line for line in lines)
        assert all("12:3" not in line for line in lines)
        assert all("45" in line or "5" not in line for line in lines)


def test_a_clock_keeps_its_minutes_beside_letters():
    path = _system_cjk_font()
    if path is None:
        pytest.skip("no CJK font on this system")
    engine = LayoutEngine(font_path=path)
    font, _source = fonts.resolve_font("AM12:30pm后的", font_path=path)
    text = "AM12:30后"
    width = font.getlength("2:30")
    assert font.getlength("AM12:30") > width
    assert font.getlength("2:30") <= width
    lines = engine._wrap_text(text, font, max_width=width)
    assert "".join(lines) == text
    assert any("2:30" in line for line in lines)
    assert all("12:3" not in line for line in lines)
    text = "12:30pm后"
    width = font.getlength("0pm")
    assert font.getlength("12:30pm") > width
    assert font.getlength("2:30") <= width
    assert font.getlength("30pm") > width
    lines = engine._wrap_text(text, font, max_width=width)
    assert "".join(lines) == text
    assert any("2:30" in line for line in lines)
    assert any("pm" in line for line in lines)
    assert all("12:3" not in line and "0pm" not in line for line in lines)


def test_a_number_range_keeps_both_sides():
    path = _system_cjk_font()
    if path is None:
        pytest.skip("no CJK font on this system")
    engine = LayoutEngine(font_path=path)
    font, _source = fonts.resolve_font("约10-20人的", font_path=path)
    cases = (
        ("约10-20人", "0-20", "10-", "20"),
        ("约10–20人", "0–20", "10–", "20"),
    )
    for text, sample, hung, right in cases:
        width = font.getlength(sample)
        assert font.getlength(hung + right) > width
        assert font.getlength(hung) <= width
        assert font.getlength(right) <= width
        lines = engine._wrap_text(text, font, max_width=width)
        assert "".join(lines) == text
        assert any(line.endswith(hung) for line in lines)
        assert any(line == right or line.startswith(right) for line in lines)
        assert all(f"{hung}2" not in line for line in lines)


def test_a_date_keeps_its_fields():
    path = _system_cjk_font()
    if path is None:
        pytest.skip("no CJK font on this system")
    engine = LayoutEngine(font_path=path)
    font, _source = fonts.resolve_font("2026-10-13日的", font_path=path)
    cases = (
        ("2026-10-13日", "6-10-13", "2026-", "10-13", "2026-1"),
        ("2026/10/13日", "6/10/13", "2026/", "10/13", "2026/1"),
    )
    for text, sample, hung, kept, torn in cases:
        width = font.getlength(sample)
        assert font.getlength(hung + kept[:2]) > width
        assert font.getlength(hung) <= width
        assert font.getlength(kept) <= width
        lines = engine._wrap_text(text, font, max_width=width)
        assert "".join(lines) == text
        assert any(kept in line for line in lines)
        assert any(line.endswith(hung) for line in lines)
        assert all(torn not in line for line in lines)


def test_a_spaced_clock_keeps_its_minutes():
    path = _system_cjk_font()
    if path is None:
        pytest.skip("no CJK font on this system")
    engine = LayoutEngine(font_path=path)
    font, _source = fonts.resolve_font("12:30 pm后的", font_path=path)
    text = "12:30 pm后"
    width = font.getlength("30 pm")
    assert font.getlength("12:30") <= width
    assert font.getlength("12:30 pm") > width
    assert font.getlength("30 pm") <= width
    lines = engine._wrap_text(text, font, max_width=width)
    assert "".join(lines) == text
    assert any(line.endswith("30 pm") for line in lines)
    assert all("12:3" not in line for line in lines)


def test_a_numeric_expression_keeps_both_operands():
    path = _system_cjk_font()
    if path is None:
        pytest.skip("no CJK font on this system")
    engine = LayoutEngine(font_path=path)
    font, _source = fonts.resolve_font("共12×34人的", font_path=path)
    cases = (
        ("共12×34人", "2×34", "12×", "34"),
        ("共12x34人", "2x34", "12x", "34"),
        ("共12*34人", "2*34", "12*", "34"),
        ("剩12/34杯", "2/34", "12/", "34"),
    )
    for text, sample, hung, right in cases:
        width = font.getlength(sample)
        assert font.getlength(hung + right) > width
        assert font.getlength(hung) <= width
        assert font.getlength(right) <= width
        lines = engine._wrap_text(text, font, max_width=width)
        assert "".join(lines) == text
        assert any(line.endswith(hung) for line in lines)
        assert any(line == right or line.startswith(right) for line in lines)
        assert all(f"{hung}3" not in line for line in lines)


def _has_dark_pixel(canvas, box) -> bool:
    x, y, w, h = box
    return any(
        canvas.getpixel((px, py))[0] < 40 for py in range(y, y + h) for px in range(x, x + w)
    )
