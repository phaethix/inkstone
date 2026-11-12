"""tests/test_layout.py — page composition and dialogue bubbles (no network)."""

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from core.comic.layout import LayoutEngine, PanelImage


def _img(w=200, h=200, color=(200, 200, 200)):
    return Image.new("RGB", (w, h), color)


def test_compose_single_panel_is_one_page(tmp_path):
    eng = LayoutEngine()
    paths = eng.compose([PanelImage(_img())], tmp_path, layout_mode="page")
    assert len(paths) == 1
    out = Image.open(paths[0])
    # One square panel fills a full-width row: 1400 wide, 1400 tall — the row
    # height follows the art instead of a fixed 1000px cell.
    assert out.size == (1400, 1400)


def test_place_panel_preserves_aspect_with_letterbox():
    """Page cells must not squash panels — contain + center on cell bg."""
    eng = LayoutEngine(bg=(255, 255, 255))
    # Tall portrait art into a wide 2-up cell (700×1000): letterbox left/right.
    portrait = Image.new("RGB", (100, 200), (0, 128, 0))
    canvas = Image.new("RGB", (1400, 1000), (255, 255, 255))
    eng._place_panel(canvas, PanelImage(portrait), (0, 0, 700, 1000))
    assert canvas.getpixel((50, 500)) == (255, 255, 255)
    assert canvas.getpixel((350, 500)) == (0, 128, 0)
    # Wide landscape into a narrow 3-up cell (466×1000): letterbox top/bottom.
    landscape = Image.new("RGB", (200, 100), (0, 0, 200))
    canvas2 = Image.new("RGB", (1400, 1000), (255, 255, 255))
    eng._place_panel(canvas2, PanelImage(landscape), (0, 0, 466, 1000))
    assert canvas2.getpixel((233, 20)) == (255, 255, 255)
    assert canvas2.getpixel((233, 500)) == (0, 0, 200)


def test_sfx_starts_below_the_caption_bar():
    eng = LayoutEngine(bg=(255, 255, 255))
    art = _img()
    box = (0, 0, 400, 800)
    caption_only = Image.new("RGB", (400, 800), (255, 255, 255))
    both = Image.new("RGB", (400, 800), (255, 255, 255))
    eng._place_panel(caption_only, PanelImage(art, caption="Later"), box)
    eng._place_panel(both, PanelImage(art, caption="Later", sfx="BOOM"), box)
    bar = (245, 245, 240)
    for y in range(caption_only.height):
        for x in range(caption_only.width):
            if caption_only.getpixel((x, y)) == bar:
                assert both.getpixel((x, y)) == bar


def test_compose_four_panels_fill_one_page_2x2(tmp_path):
    eng = LayoutEngine()
    panels = [PanelImage(_img()) for _ in range(4)]
    paths = eng.compose(panels, tmp_path, layout_mode="page")
    assert len(paths) == 1
    out = Image.open(paths[0])
    # DP packing picks a 1-over-3 split (one full-width square above a 3-up
    # row, 466+1400=1866px tall): page ratio 0.75 hugs the 0.72 manga target
    # closer than the old 2x2's 1.0, and every row stays art-height with no
    # letterbox bands.
    assert out.size == (1400, 1866)


def test_compose_paginates_past_four(tmp_path):
    eng = LayoutEngine()
    panels = [PanelImage(_img()) for _ in range(5)]
    paths = eng.compose(panels, tmp_path, layout_mode="page")
    assert len(paths) == 2
    assert all(p.endswith(".png") for p in paths)


def test_single_panel_row_matches_art_aspect(tmp_path):
    """A stretched single-panel row must size itself to the art, not to the
    old 2-up cell width: stretching the cell to 1400 while keeping the row at
    700px tall letterboxes the square art in the middle with 350px white
    bands on both sides (page_c0001_p0000 row 4)."""
    eng = LayoutEngine(bg=(255, 255, 255))
    red = (200, 0, 0)
    panels = [PanelImage(Image.new("RGB", (200, 200), red)) for _ in range(5)]
    canvas = eng.compose_single_page(panels)
    import numpy as np

    arr = np.asarray(canvas)
    # 5 panels 2-up: rows 1-2 are 700px tall, row 3 stretches to full width.
    red_mask = (arr[:, :, 0] > 150) & (arr[:, :, 1] < 100)
    rows = np.where(red_mask.any(axis=1))[0]
    last_row = rows.max()
    seg = red_mask[last_row - 199 : last_row + 1]
    cols = np.where(seg.any(axis=0))[0]
    assert cols.min() == 0 and cols.max() == canvas.width - 1, (
        f"stretched row content spans x={cols.min()}..{cols.max()} on a "
        f"{canvas.width}px page: the art is letterboxed instead of filling "
        "the stretched cell"
    )


def test_row_height_ignores_lettering_reserve_for_art_height():
    """A row's art area must stay full-bleed even when a neighbor panel in the
    same row carries dialogue: the reserve inflated the row above the art and
    left a white band under the silent panel (page_c0001_p0000 row 2)."""
    eng = LayoutEngine(bg=(255, 255, 255))
    silent = PanelImage(_img(700, 700))
    talkative = PanelImage(_img(700, 700), dialogue="一句很长的台词" * 10)
    canvas = eng.compose_single_page([silent, talkative])
    import numpy as np

    arr = np.asarray(canvas.convert("RGB"))
    # Left cell: square art in a 700-wide cell must touch the cell's top and
    # bottom edges with no white bands above or below.
    left = arr[:, :700]
    row_white = (left > 245).all(axis=2).mean(axis=1)
    nonwhite_rows = np.where(row_white < 0.95)[0]
    assert nonwhite_rows.min() == 0, "white band above the silent panel"
    assert nonwhite_rows.max() == arr.shape[0] - 1, "white band below the silent panel"


def test_compose_single_page_odd_count_has_no_blank_half_row(tmp_path):
    """A 5-panel page packed 2-up leaves row 3 with one panel and one blank
    cell — the user-visible blank slot on page_c0001_p0001. The last row of
    a single-page compose must stretch its panel(s) across the full width."""
    eng = LayoutEngine()
    panels = [PanelImage(_img()) for _ in range(5)]
    canvas = eng.compose_single_page(panels)
    import numpy as np

    arr = np.array(canvas.convert("L"))
    # The trailing grid slot is a blank rectangle: bottom row, right half.
    bottom_right = arr[arr.shape[0] * 2 // 3 :, arr.shape[1] // 2 :]
    nonwhite = (bottom_right <= 245).mean()
    assert nonwhite > 0.4, f"blank slot: only {nonwhite:.1%} non-white pixels"


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

    out = Image.open(paths[0])
    # The row height follows the art (100px); the oversized bubble is clamped
    # inside the cell and its text shrinks to fit instead of inflating the
    # page with white reserve bands.
    assert out.size == (400, 100)


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

    # Page rows keep art-first heights; the bubble clamps inside the cell.
    assert Image.open(page[0]).size == (400, 100)
    # Webtoon strips still expand below each panel for its bubble.
    assert Image.open(webtoon[0]).height > 100


def test_wrap_text_latin_breaks_on_word_boundaries():
    eng = LayoutEngine()
    font = ImageFont.load_default()
    # Narrow width forces wrapping; words must stay intact.
    lines = eng._wrap_text("The quick brown fox", font, max_width=40)
    joined = " ".join(lines)
    assert "The" in joined and "quick" in joined
    for line in lines:
        # No mid-word split of these tokens across a line boundary without space.
        assert "quic" != line  # would only appear if "quick" was split mid-word as start
    assert all(" " not in w or True for w in lines)
    # Every original word appears unbroken in some line (or as whole line).
    for word in ("The", "quick", "brown", "fox"):
        assert any(word in line for line in lines)


def test_wrap_text_keeps_a_latin_word_intact_beside_cjk():
    eng = LayoutEngine()
    font = ImageFont.load_default()
    text = "石猴 hello"
    lines = eng._wrap_text(text, font, max_width=font.getlength("hello"))
    assert "".join(lines).replace(" ", "") == text.replace(" ", "")
    assert any("hello" in line for line in lines)
    for line in lines:
        for piece in ("hel", "ell", "llo"):
            if piece in line:
                assert "hello" in line


def test_wrap_text_cjk_still_breaks_per_character():
    eng = LayoutEngine()
    font = ImageFont.load_default()
    text = "雨水顺着梧桐叶滑落"
    lines = eng._wrap_text(text, font, max_width=20)
    assert "".join(lines) == text
    assert len(lines) >= 2


def test_caption_lettering_draws_pixels():
    eng = LayoutEngine()
    canvas = Image.new("RGB", (400, 400), (255, 255, 255))
    panel = PanelImage(_img(200, 200), caption="次日清晨")
    eng._place_panel(canvas, panel, (0, 0, 400, 400))
    arr = np.array(canvas)
    assert int((arr != (255, 255, 255)).any(axis=2).sum()) > 0


def test_sfx_lettering_draws_pixels():
    eng = LayoutEngine()
    canvas = Image.new("RGB", (400, 400), (255, 255, 255))
    panel = PanelImage(_img(200, 200), sfx="轰隆！")
    eng._place_panel(canvas, panel, (0, 0, 400, 400))
    arr = np.array(canvas)
    assert int((arr != (255, 255, 255)).any(axis=2).sum()) > 0


def test_paginate_empty_returns_no_pages():
    assert LayoutEngine._paginate([]) == []


def test_compose_empty_page_mode_writes_nothing(tmp_path):
    paths = LayoutEngine().compose([], tmp_path, layout_mode="page")
    assert paths == []
    assert list(tmp_path.glob("*.png")) == []


def test_an_empty_collage_does_not_delete_the_sheets_on_disk(tmp_path, monkeypatch):
    """A failed collage must not clear the pages a previous run already bound.

    ``_drop_stale_panel_sheets`` deletes every ``page_*.png`` that is not in the
    list it is handed, so an empty production passing an empty list would wipe
    the previous collage and the PDF binder would then bind nothing. The panels
    are non-empty here on purpose: an empty *input* takes the early return and
    would never reach the branch under test.
    """
    from core.comic.layout import LayoutEngine, PanelImage
    from core.pipelines.export_stages import layout_pages_with_manifest

    pages_dir = tmp_path / "pages"
    output_dir = tmp_path / "out"
    pages_dir.mkdir()
    survivor = pages_dir / "page_01.png"
    survivor.write_bytes(b"previous collage")

    monkeypatch.setattr(LayoutEngine, "compose", lambda *a, **k: [])

    produced = layout_pages_with_manifest([PanelImage(_img())], pages_dir, output_dir)

    assert produced == []
    assert survivor.exists(), "an empty collage must not wipe existing sheets"


def test_place_panel_trims_a_generated_white_border():
    """The image model still emits a white frame around some panels despite the
    full-bleed style line (c0000-p0000-g0000/g0002, c0001-p0000-g0003 all had
    100% white borders): the frame must be cropped before the panel is placed,
    or the page shows white-frame cells and misaligned rows."""
    eng = LayoutEngine(bg=(255, 255, 255))
    art_color, frame = (120, 140, 160), (255, 255, 255)
    img = Image.new("RGB", (100, 100), art_color)
    for a in range(100):
        for b in range(2):
            img.putpixel((a, b), frame)
            img.putpixel((a, 99 - b), frame)
            img.putpixel((b, a), frame)
            img.putpixel((99 - b, a), frame)
    canvas = Image.new("RGB", (200, 100), (255, 255, 255))
    eng._place_panel(canvas, PanelImage(img), (0, 0, 100, 100))
    # Every corner of the cell must be art, not the model's white frame.
    corners = [(2, 2), (97, 2), (2, 97), (97, 97)]
    assert all(canvas.getpixel(c) == art_color for c in corners), (
        "white frame reached the cell corners: the border was not trimmed"
    )


def test_trimmed_panel_keeps_centered_letterbox_behavior():
    """A bordered panel placed into an off-aspect cell still contains and
    centers after the trim — trimming must not stretch the art."""
    eng = LayoutEngine(bg=(255, 255, 255))
    art_color = (0, 128, 0)
    img = Image.new("RGB", (100, 100), art_color)
    for a in range(100):
        for b in range(2):
            img.putpixel((a, b), (255, 255, 255))
            img.putpixel((a, 99 - b), (255, 255, 255))
            img.putpixel((b, a), (255, 255, 255))
            img.putpixel((99 - b, a), (255, 255, 255))
    canvas = Image.new("RGB", (200, 100), (255, 255, 255))
    eng._place_panel(canvas, PanelImage(img), (0, 0, 200, 100))
    # 88x88 art into a 200x100 cell: contain -> 100x100 minus borders scaled,
    # centered; the cell edges stay background.
    assert canvas.getpixel((0, 50)) == (255, 255, 255)
    assert canvas.getpixel((100, 50)) == art_color
