from core.comic.lettering_lang import (
    lettering_field_mismatches,
    sanitize_lettering_text,
    sanitize_plan_lettering,
    source_lettering_script,
    strip_mismatched_lettering,
    strip_pinyin_glosses,
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
            "panels": [{"panel_id": "1", "dialogue": "Hello there", "caption": "傍晚，村口。"}],
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


def test_strip_pinyin_glosses_removes_parentheses():
    raw = "一清醒过来，他已经是“骆驼祥子”了。（Yī xǐngguò lái, tā yǐjīng shì “Luòtuo Xiángzi” le.）"
    assert strip_pinyin_glosses(raw) == "一清醒过来，他已经是“骆驼祥子”了。"
    ascii_paren = "海甸的小店，三日昏迷 (Haidian de xiaodian, sanri hunmi)"
    assert "Haidian" not in strip_pinyin_glosses(ascii_paren)
    assert "海甸的小店，三日昏迷" in strip_pinyin_glosses(ascii_paren)


def test_sanitize_lettering_strips_pinyin_and_truncates():
    long_pinyin = (
        "自从一到城里来，他就是“祥子”，仿佛根本没有个姓；如今，“骆驼祥子”之上，"
        "就更没有人关心他到底姓什么了。（Zicong yi dao chengli lai...）"
    )
    out = sanitize_lettering_text(long_pinyin, kind="caption", script="cjk")
    assert out is not None
    assert "Zicong" not in out
    assert "（" not in out and "(" not in out
    assert len(out) <= 48
    assert out.endswith("…") or len(long_pinyin) <= 48


def test_sanitize_plan_lettering_cleans_fields():
    plan = ComicPagePlan.model_validate(
        {
            "page_id": "p1",
            "panels": [
                {
                    "panel_id": "1",
                    "caption": "海甸的小店，三日昏迷（Hǎidiàn de xiǎodiàn, sānrì hūnmí）",
                    "dialogue": "我要这辆车！",
                }
            ],
        }
    )
    fixed = sanitize_plan_lettering(plan, "cjk")
    assert fixed.panels[0].caption == "海甸的小店，三日昏迷"
    assert fixed.panels[0].dialogue == "我要这辆车！"
