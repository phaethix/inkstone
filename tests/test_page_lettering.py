from PIL import Image, ImageChops, ImageDraw

from core.comic import fonts as _fonts
from core.comic.layout import LayoutEngine
from core.comic.page_lettering import (
    LETTERING_VERSION,
    _shift_off_faces,
    fit_lettering_box,
    letter_finished_page,
    resolve_lettering_jobs,
)
from core.schemas import ComicPagePlan


def _system_cjk_font() -> str | None:
    for path in _fonts._CJK_FONT_CANDIDATES:
        if _fonts._try_load(path, _fonts.DEFAULT_CJK_FONT_SIZE) is not None:
            return path
    return None


def test_resolve_uses_boxes_then_heuristics():
    plan = ComicPagePlan.model_validate(
        {
            "page_id": "p1",
            "panels": [
                {"panel_id": "1", "dialogue": "你好", "caption": "旁白"},
                {"panel_id": "2", "sfx": "砰"},
            ],
            "lettering_boxes": [
                {"kind": "dialogue", "panel_id": "1", "x": 0.1, "y": 0.2, "w": 0.3, "h": 0.1},
            ],
        }
    )
    jobs = resolve_lettering_jobs(plan)
    kinds = {(p, k) for p, k, _, _ in jobs}
    assert ("1", "dialogue") in kinds
    assert ("1", "caption") in kinds  # heuristic
    assert ("2", "sfx") in kinds
    dialogue = next(b for p, k, t, b in jobs if k == "dialogue")
    # margin clamp may nudge x slightly inward from 0.1
    assert dialogue[2] <= 0.3 + 1e-6
    assert dialogue[0] >= 0.04


def test_letter_finished_page_draws_nonzero_ink():
    blank = Image.new("RGB", (200, 300), (200, 200, 200))
    plan = ComicPagePlan.model_validate(
        {
            "page_id": "p1",
            "panels": [{"panel_id": "1", "dialogue": "你好世界"}],
            "lettering_boxes": [
                {"kind": "dialogue", "panel_id": "1", "x": 0.1, "y": 0.1, "w": 0.6, "h": 0.2},
            ],
        }
    )
    out = letter_finished_page(blank, plan)
    assert out.size == blank.size
    assert ImageChops.difference(out, blank).getbbox() is not None


def test_letter_finished_page_shrink_wraps_huge_plan_box():
    blank = Image.new("RGB", (200, 400), (200, 200, 200))
    plan = ComicPagePlan.model_validate(
        {
            "page_id": "p1",
            "panels": [
                {
                    "panel_id": "2",
                    "dialogue": "假如老头子消了气呢？",
                }
            ],
            "lettering_boxes": [
                {"kind": "dialogue", "panel_id": "2", "x": 0.1, "y": 0.5, "w": 0.8, "h": 0.4},
            ],
        }
    )
    out = letter_finished_page(blank, plan)
    bottom = out.crop((0, 200, 200, 400))
    whiteish = sum(
        1
        for y in range(bottom.height)
        for x in range(bottom.width)
        if bottom.getpixel((x, y))[0] > 240
    )
    assert whiteish < 8000


def test_fit_lettering_box_stays_inside_page_margin():
    engine = LayoutEngine()
    page_w, page_h = 400, 600
    # Anchor flush to left/top edge — must inset.
    box = fit_lettering_box(
        engine,
        "dialogue",
        "我要这辆车！",
        (0, 0, 300, 200),
        (page_w, page_h),
    )
    x, y, bw, bh = box
    assert x >= int(page_w * 0.04) - 1
    assert y >= int(page_h * 0.04) - 1
    assert x + bw <= page_w - int(page_w * 0.04) + 1
    assert y + bh <= page_h - int(page_h * 0.04) + 1


def test_a_box_moves_off_a_face_and_stays_when_there_is_no_face():
    page = (400, 600)
    box = (100, 120, 80, 40)
    face = (90, 100, 120, 100)
    moved = _shift_off_faces(box, [face], page)
    assert _overlap(moved, face) == 0
    assert _shift_off_faces(box, [], page) == box


def test_lettering_does_not_paint_a_detected_face(monkeypatch):
    blank = Image.new("RGB", (200, 300), (180, 180, 180))
    face = (8, 12, 160, 110)
    monkeypatch.setattr("core.comic.page_lettering.detect_page_faces", lambda image: [face])
    plan = ComicPagePlan.model_validate(
        {
            "page_id": "p1",
            "panels": [{"panel_id": "1", "dialogue": "你好世界"}],
            "lettering_boxes": [
                {"kind": "dialogue", "panel_id": "1", "x": 0.08, "y": 0.08, "w": 0.6, "h": 0.2},
            ],
        }
    )
    out = letter_finished_page(blank, plan)
    fx, fy, fw, fh = face
    white = sum(
        1 for y in range(fy, fy + fh) for x in range(fx, fx + fw) if out.getpixel((x, y))[0] > 240
    )
    assert white == 0
    assert ImageChops.difference(out, blank).getbbox() is not None


def _overlap(box, other) -> int:
    x, y, bw, bh = box
    ox, oy, ow, oh = other
    ix = max(0, min(x + bw, ox + ow) - max(x, ox))
    iy = max(0, min(y + bh, oy + oh) - max(y, oy))
    return ix * iy


def test_resolve_strips_pinyin_before_overlay():
    plan = ComicPagePlan.model_validate(
        {
            "page_id": "p1",
            "panels": [
                {
                    "panel_id": "1",
                    "caption": "海甸的小店，三日昏迷（Hǎidiàn de xiǎodiàn, sānrì hūnmí）",
                }
            ],
        }
    )
    jobs = resolve_lettering_jobs(plan, source_text="祥子在北平。")
    assert len(jobs) == 1
    assert jobs[0][2] == "海甸的小店，三日昏迷"
    assert LETTERING_VERSION == "deferred_v4"


def test_short_dialogue_stays_on_one_line_after_box_fit():
    """A line that fits must not be re-wrapped into a broken two-character word.

    ``这首歌，倒是比人坦诚。`` is 154px on one line. Shrinking the bubble to the
    width of a *wrapped* line and then re-wrapping inside ``_draw_bubble`` split
    ``坦诚`` across ``坦`` / ``诚。``. The fitted box must be wide enough that the
    drawer renders the whole sentence on one line.
    """
    path = _system_cjk_font()
    if path is None:
        import pytest

        pytest.skip("no CJK font on this system")
    text = "这首歌，倒是比人坦诚。"
    engine = LayoutEngine(font_path=path)
    page = (768, 1088)
    box = fit_lettering_box(
        engine,
        "dialogue",
        text,
        (61, 87, 276, 130),
        page,
    )
    font, _ = _fonts.resolve_font(text, font_path=path)
    # _draw_bubble wraps at (box_width - 2*pad); the fitted box must leave that
    # inner width >= the single-line text width.
    inner = box[2] - 20
    assert font.getlength(text) <= inner, (
        "fitted bubble is too narrow: the drawer will re-wrap and break 坦诚"
    )
    drawn_lines = engine._wrap_text(text, font, max_width=inner)
    assert drawn_lines == [text]


def test_letter_finished_page_keeps_short_dialogue_on_one_line():
    """End-to-end: overlay the real sentence and confirm it is not split mid-word."""
    path = _system_cjk_font()
    if path is None:
        import pytest

        pytest.skip("no CJK font on this system")
    text = "这首歌，倒是比人坦诚。"
    blank = Image.new("RGB", (768, 1088), (200, 200, 200))
    plan = ComicPagePlan.model_validate(
        {
            "page_id": "p1",
            "panels": [{"panel_id": "1", "dialogue": text, "speaker": "沈知意"}],
            "lettering_boxes": [
                {"kind": "dialogue", "panel_id": "1", "x": 0.08, "y": 0.08, "w": 0.36, "h": 0.12},
            ],
        }
    )
    out = letter_finished_page(blank, plan, font_path=path, source_text=text)
    assert out.size == blank.size
    assert ImageChops.difference(out, blank).getbbox() is not None
    # The rendered bubble must be a single-line box (height ≈ one line + pad),
    # not the two-line box that split 坦诚.
    engine = LayoutEngine(font_path=path)
    jobs = resolve_lettering_jobs(plan, source_text=text)
    for _pid, kind, t, (nx, ny, nw, nh) in jobs:
        if kind != "dialogue":
            continue
        anchor = (int(nx * 768), int(ny * 1088), int(nw * 768), int(nh * 1088))
        box = fit_lettering_box(engine, kind, t, anchor, (768, 1088))
        font, _ = _fonts.resolve_font(t, font_path=path)
        assert engine._wrap_text(t, font, max_width=box[2] - 20) == [t]


def test_bubble_uses_a_soft_fill_not_a_harsh_white_sticker():
    """The dialogue bubble should read as part of the art, not a UI sticker.

    A pure-white, 2px black-outlined rounded rectangle is visually loud against
    soft cel-shaded art. The new style uses a soft translucent-ish fill that is
    clearly lighter than the art but not glaring white, with a thin soft border.
    """
    path = _system_cjk_font()
    if path is None:
        import pytest

        pytest.skip("no CJK font on this system")
    engine = LayoutEngine(font_path=path)
    canvas = Image.new("RGB", (320, 120), (200, 200, 200))
    engine._draw_bubble(ImageDraw.Draw(canvas), (20, 20, 260, 80), "你好，世界。")
    # Sample the bubble's inner fill (well inside the border, away from glyphs).
    fill = canvas.getpixel((30, 30))
    # Not pure white (255,255,255) — a soft warm paper tone instead.
    assert fill != (255, 255, 255)
    assert min(fill) >= 235, "fill should stay light enough for black text"
