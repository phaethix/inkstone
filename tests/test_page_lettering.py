from PIL import Image, ImageChops

from core.comic.layout import LayoutEngine
from core.comic.page_lettering import (
    LETTERING_VERSION,
    fit_lettering_box,
    letter_finished_page,
    resolve_lettering_jobs,
)
from core.schemas import ComicPagePlan


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
    assert LETTERING_VERSION == "deferred_v3"
