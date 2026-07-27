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
