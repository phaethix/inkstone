from core.comic.lettering_lang import (
    lettering_field_mismatches,
    source_lettering_script,
    strip_mismatched_lettering,
)
from core.schemas import ComicPagePlan


def test_source_lettering_script_chinese_novel():
    assert source_lettering_script("第一章\n福贵在村口看着夕阳。") == "cjk"


def test_source_lettering_script_english():
    assert source_lettering_script("Chapter one. Fugui stood at the gate.") == "latin"


def test_mismatches_english_dialogue_on_chinese_source():
    plan = ComicPagePlan.model_validate(
        {
            "page_id": "p1",
            "panels": [
                {"panel_id": "1", "dialogue": "Hello there", "caption": "傍晚，村口。"}
            ],
        }
    )
    bad = lettering_field_mismatches(plan, "cjk")
    assert ("1", "dialogue", "Hello there") in bad
    assert not any(k == "caption" for _, k, _ in bad)


def test_strip_mismatched_lettering_drops_english_on_cjk():
    plan = ComicPagePlan.model_validate(
        {
            "page_id": "p1",
            "panels": [{"panel_id": "1", "dialogue": "Hello", "caption": "傍晚"}],
            "lettering_boxes": [
                {"kind": "dialogue", "panel_id": "1", "x": 0.1, "y": 0.1, "w": 0.3, "h": 0.1},
                {"kind": "caption", "panel_id": "1", "x": 0.1, "y": 0.0, "w": 0.5, "h": 0.1},
            ],
        }
    )
    fixed = strip_mismatched_lettering(plan, "cjk")
    assert fixed.panels[0].dialogue is None
    assert fixed.panels[0].caption == "傍晚"
    assert all(b.kind != "dialogue" for b in fixed.lettering_boxes)
