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


def test_bubble_fill_adapts_to_surrounding_art_not_fixed_white():
    """A warm-white block looks like a sticker on a dark night scene.

    The bubble fill must be sampled/blended from the local artwork so it reads
    as part of the picture. On a dark blue background it must NOT be the fixed
    bright (250,247,240) paper tone.
    """
    from core.comic.page_lettering import blend_fill_with_art

    art = Image.new("RGB", (320, 120), (61, 80, 113))  # dark blue night sky
    fill = blend_fill_with_art(art, (20, 20, 260, 80))
    # Sampled fill must be clearly darker / closer to the art than a white sticker.
    assert sum(fill) < sum((250, 247, 240)) - 90, "fill is still a glaring light block"
    # ...but must still be distinguishable from the raw background.
    assert sum(abs(fill[i] - (61, 80, 113)[i]) for i in range(3)) > 20


def test_pick_lettering_region_prefers_flat_empty_area():
    """Position must come from image analysis, not a hardcoded corner.

    Given a canvas with a busy/textured subject on the right and a flat calm
    sky on the left, the analyzer must place the box in the calm region.
    """
    from core.comic.page_lettering import pick_lettering_region

    img = Image.new("RGB", (400, 600), (61, 80, 113))
    d = ImageDraw.Draw(img)
    # Busy subject block on the right half (high edge/variance).
    for i in range(0, 600, 6):
        d.rectangle([200, i, 400, i + 3], fill=(180, 120, 60))
        d.rectangle([200, i + 3, 400, i + 6], fill=(30, 40, 50))
    box = pick_lettering_region(img, (0.05, 0.08, 0.3, 0.1))
    # The chosen box center must be in the calm left area, not over the subject.
    cx = box[0] + box[2] / 2
    assert cx < 200, "analyzer put the text over the busy subject / wrong area"


def test_text_ink_stays_legible_on_dark_sampled_fill():
    """On a dark art-derived fill, black ink would be unreadable."""
    from core.comic.page_lettering import text_ink_for_fill

    assert text_ink_for_fill((61, 80, 113)) == "white"
    assert text_ink_for_fill((245, 240, 230)) == "black"


def test_analysis_respects_each_box_preferred_location():
    """Different hinted positions must not collapse into one 'calmest' cell.

    A regression: every job's pick_lettering_region returned the same top-right
    sky cell, so three boxes stacked into a column. The analyzer must keep each
    box near its own hinted region instead of globally grabbing one flat spot.
    """
    from core.comic.page_lettering import pick_lettering_region

    img = Image.new("RGB", (400, 600), (90, 110, 140))
    d = ImageDraw.Draw(img)
    for i in range(0, 600, 8):
        d.line([(0, i), (400, i + 40)], fill=(120, 140, 170), width=2)
    right_top = pick_lettering_region(img, (0.55, 0.10, 0.40, 0.12))
    left_bottom = pick_lettering_region(img, (0.05, 0.78, 0.40, 0.12))
    rt_cx, rt_cy = right_top[0] + right_top[2] / 2, right_top[1] + right_top[3] / 2
    lb_cx, lb_cy = left_bottom[0] + left_bottom[2] / 2, left_bottom[1] + left_bottom[3] / 2
    # right-top box must stay on the right and upper half; left-bottom on left/lower.
    assert rt_cx > 200 and rt_cy < 300, "right-top box was pulled away from its hint"
    assert lb_cx < 200 and lb_cy > 300, "left-bottom box was pulled away from its hint"


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


def test_caption_uses_the_same_soft_fill_not_a_harsh_white_bar():
    """Captions must be as soft as bubbles, not glaring white black-edged bars.

    The earlier soft-bubble fix left _draw_caption untouched: a (245,245,240)
    rectangle with a 2px black outline still sat across the artwork. Against
    watercolor-style pages that hard white bar reads as a UI sticker.
    """
    path = _system_cjk_font()
    if path is None:
        import pytest

        pytest.skip("no CJK font on this system")
    engine = LayoutEngine(font_path=path)
    canvas = Image.new("RGB", (320, 120), (200, 200, 200))
    engine._draw_caption(ImageDraw.Draw(canvas), (20, 20, 260, 80), "两个人便这样倚着栏杆")
    fill = canvas.getpixel((30, 30))
    assert fill != (245, 245, 240), "caption is still the old hard white bar"
    assert fill == (250, 247, 240), "caption should share the bubble's soft paper tone"
    # The 2px black border must be gone: sample the very corner pixel.
    assert canvas.getpixel((20, 20)) != (0, 0, 0), "caption still has a harsh black outline"


def test_middle_caption_is_pushed_to_a_page_edge_band():
    """A caption anchored in the middle of the page must not cover the figures.

    cv2 finds zero anime faces, and band-midpoint nudging misjudges the real
    multi-panel layout: with a 4-panel band (band_h=0.25) a caption at y=0.4
    stayed at center 0.45 right across the characters. Captions belong in a
    quiet top/bottom margin band regardless of the panel band used to place it.
    """
    from core.comic.page_lettering import _safe_anchor

    # Real finished-page plan: 4 horizontal panel bands, this caption's anchor
    # lands in the second band. The band-mid heuristic does not push it out.
    x, y, w, h = _safe_anchor(0.3, 0.40, 0.45, 0.10, band_y0=0.25, band_h=0.25, kind="caption")
    center = y + h / 2
    # Must land in a top or bottom edge band, never the middle third.
    assert center < 0.2 or center > 0.8, (
        "caption is still stranded in the middle where the figures are"
    )


def _vision_plan():
    return ComicPagePlan.model_validate(
        {"page_id": "p1", "panels": [{"panel_id": "1", "dialogue": "你好，世界。"}]}
    )


def test_letter_finished_page_uses_provided_placement_boxes():
    """Vision boxes must override the deterministic placer's own position."""
    blank = Image.new("RGB", (400, 600), (60, 80, 110))
    plan = _vision_plan()
    # Force a box at a location the deterministic placer would never choose:
    # the bottom-right corner.
    forced = [(260, 500, 120, 40)]
    out = letter_finished_page(blank, plan, placement_boxes=forced)
    default = letter_finished_page(blank, plan)
    # The forced box region must differ from the plain background, and the
    # result must differ from the default (no-vision) rendering.
    assert out.getpixel((280, 515)) != (60, 80, 110), "no lettering drawn at forced box"
    assert out.tobytes() != default.tobytes(), "vision boxes were ignored"


def test_letter_finished_page_falls_back_when_placement_boxes_none():
    """Passing None (or omitting) must keep the old deterministic behavior."""
    blank = Image.new("RGB", (400, 600), (60, 80, 110))
    plan = _vision_plan()
    a = letter_finished_page(blank, plan)
    b = letter_finished_page(blank, plan, placement_boxes=None)
    assert a.tobytes() == b.tobytes()
