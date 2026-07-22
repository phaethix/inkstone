"""tests/test_layout.py — page composition and dialogue bubbles (no network)."""

import numpy as np
from PIL import Image, ImageDraw

from core.comic.layout import LayoutEngine, PanelImage


def _img(w=200, h=200, color=(200, 200, 200)):
    return Image.new("RGB", (w, h), color)


def test_compose_single_panel_is_one_page(tmp_path):
    eng = LayoutEngine()
    paths = eng.compose([PanelImage(_img())], tmp_path, layout_mode="page")
    assert len(paths) == 1
    out = Image.open(paths[0])
    assert out.size == (1400, 1000)


def test_compose_four_panels_fill_one_page_2x2(tmp_path):
    eng = LayoutEngine()
    panels = [PanelImage(_img()) for _ in range(4)]
    paths = eng.compose(panels, tmp_path, layout_mode="page")
    assert len(paths) == 1
    out = Image.open(paths[0])
    assert out.size == (1400, 2000)


def test_compose_paginates_past_four(tmp_path):
    eng = LayoutEngine()
    panels = [PanelImage(_img()) for _ in range(5)]
    paths = eng.compose(panels, tmp_path, layout_mode="page")
    assert len(paths) == 2
    assert all(p.endswith(".png") for p in paths)


def test_dialogue_bubble_draws_non_background_pixels():
    eng = LayoutEngine()
    img = Image.new("RGB", (200, 120), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    eng._draw_bubble(draw, (20, 20, 160, 80), "你好，世界。")
    # Border + text are dark, so the bubble area is not pure white.
    arr = np.array(img)
    non_white = int((arr != (255, 255, 255)).any(axis=2).sum())
    assert non_white > 0


def test_webtoon_stacks_vertically(tmp_path):
    eng = LayoutEngine(page_width=400)
    panels = [PanelImage(_img(400, 100)) for _ in range(3)]
    paths = eng.compose(panels, tmp_path, layout_mode="webtoon")
    assert len(paths) == 1
    out = Image.open(paths[0])
    assert out.size == (400, 300)


def test_webtoon_renders_dialogue_bubble(tmp_path):
    eng = LayoutEngine(page_width=400)
    plain = eng.compose([PanelImage(_img(400, 100))], tmp_path / "plain", layout_mode="webtoon")
    with_dialogue = eng.compose(
        [PanelImage(_img(400, 100), dialogue="hello")],
        tmp_path / "dialogue",
        layout_mode="webtoon",
    )

    assert Image.open(plain[0]).tobytes() != Image.open(with_dialogue[0]).tobytes()


def test_webtoon_expands_for_long_dialogue(tmp_path):
    eng = LayoutEngine(page_width=400)
    text = "很长的对白" * 200
    paths = eng.compose(
        [PanelImage(_img(400, 100), dialogue=text)],
        tmp_path,
        layout_mode="webtoon",
    )

    assert Image.open(paths[0]).height > 100


def test_page_expands_for_long_dialogue(tmp_path):
    eng = LayoutEngine(page_width=400, cell_height=100)
    text = "很长的对白" * 200
    paths = eng.compose([PanelImage(_img(400, 100), dialogue=text)], tmp_path, layout_mode="page")

    assert Image.open(paths[0]).height > 100


def test_explicit_newlines_expand_dialogue_bubble(tmp_path):
    eng = LayoutEngine(page_width=400, cell_height=100)
    text = "\n".join(["line"] * 20)
    page = eng.compose(
        [PanelImage(_img(400, 100), dialogue=text)],
        tmp_path / "page",
        layout_mode="page",
    )
    webtoon = eng.compose(
        [PanelImage(_img(400, 100), dialogue=text)],
        tmp_path / "webtoon",
        layout_mode="webtoon",
    )

    assert Image.open(page[0]).height > 100
    assert Image.open(webtoon[0]).height > 100
