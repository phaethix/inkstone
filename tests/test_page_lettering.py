from PIL import Image, ImageChops

from core.comic.page_lettering import letter_finished_page, resolve_lettering_jobs
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
    assert dialogue == (0.1, 0.2, 0.3, 0.1)


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
    # Full 0.8×0.4 of 200×400 ≈ 25600 white if chrome filled the plan box.
    assert whiteish < 8000
