# tests/test_voice_timeline.py
import hashlib
import json

from core.comic.page_prompt import render_finished_page_prompt
from core.comic.voice import (
    looks_like_letter_narration,
    sanitize_plan_voice,
    timeline_prompt_lines,
)
from core.pipelines.creative_comic import _render_fingerprint
from core.schemas import (
    CharacterAsset,
    CharacterCanon,
    ColorBible,
    ComicPagePlan,
    ModelSnapshot,
    VisualBible,
)


def _bible() -> VisualBible:
    return VisualBible(
        version="bible_v3",
        style_guide="period",
        color=ColorBible(palette=[], lighting="", forbidden=[]),
        characters={
            "R": CharacterCanon(
                canonical_name="R",
                gender="male",
                narrative_function="letter_reader",
                face_lock="adult man",
                stages=[],
            ),
            "陌生女人": CharacterCanon(
                canonical_name="陌生女人",
                gender="female",
                narrative_function="letter_writer",
                face_lock="adult woman",
                stages=[],
            ),
        },
        content_hash="x",
    )


def test_looks_like_letter_narration():
    assert looks_like_letter_narration("我的儿子昨天死了")
    assert not looks_like_letter_narration("请进。")


def test_sanitize_moves_writer_voice_off_reader_dialogue():
    plan = ComicPagePlan.model_validate(
        {
            "page_id": "p1",
            "purpose": "read letter",
            "timeline": "present",
            "layout_intent": "object_closeup",
            "panels": [
                {
                    "panel_id": "1",
                    "characters": ["R"],
                    "action": "R reads",
                    "dialogue": "我的儿子昨天死了，请你相信我",
                    "speaker": "R",
                }
            ],
            "lettering_boxes": [
                {"kind": "dialogue", "panel_id": "1", "x": 0.1, "y": 0.1, "w": 0.3, "h": 0.1}
            ],
        }
    )
    out = sanitize_plan_voice(plan, _bible())
    assert out.panels[0].dialogue is None
    assert "儿子" in (out.panels[0].caption or "")
    assert out.panels[0].speaker == ""
    assert not any(b.kind == "dialogue" for b in out.lettering_boxes)


def test_timeline_prompt_lines_and_page_injection():
    assert any("present" in line.casefold() for line in timeline_prompt_lines("present"))
    past_lines = timeline_prompt_lines("past")
    assert any("past" in line.casefold() or "memory" in line.casefold() for line in past_lines)
    plan = ComicPagePlan.model_validate(
        {
            "page_id": "p1",
            "purpose": "memory",
            "timeline": "past",
            "layout_intent": "widescreen_scene",
            "panels": [{"panel_id": "1", "characters": ["陌生女人"], "action": "she waits"}],
        }
    )
    text = render_finished_page_prompt(
        plan,
        characters_by_name={"陌生女人": CharacterAsset(name="陌生女人", l1_prompt="woman")},
        settings_by_name={},
        visual_bible=_bible(),
    )
    assert "Timeline=past" in text or "memory" in text.lower()


def test_fingerprint_includes_voice_timeline():
    snapshot = ModelSnapshot(chat="chat", t2i="image", i2i="image")
    fp = _render_fingerprint(
        "style",
        snapshot=snapshot,
        panel_continuity=False,
        l3_enabled=False,
        render_mode="finished_page",
        page_size="1024x1536",
    )
    payload = {
        "style_guide": "style",
        "model_snapshot": snapshot.model_dump(),
        "panel_continuity": False,
        "l3_enabled": False,
        "render_mode": "finished_page",
        "page_size": "1024x1536",
        "identity": "metaphor_v2",
        "stage_lock": "v1",
        "layout": "anti_template_v1",
        "voice_timeline": "v1",
        "lettering": "deferred_v3",
    }
    expected = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert fp == expected
