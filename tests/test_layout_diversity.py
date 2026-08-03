# tests/test_layout_diversity.py
import hashlib
import json

from core.comic.layout_diversity import (
    ANTI_CENTER_STANDEE_LINE,
    LAYOUT_CATALOG,
    consecutive_layout_streak,
    layout_diversity_instructions,
    normalize_layout_intent,
    summarize_recent_layouts,
)
from core.comic.page_prompt import render_finished_page_prompt
from core.pipelines.creative_comic import _render_fingerprint
from core.schemas import CharacterAsset, ColorBible, ComicPagePlan, ModelSnapshot, VisualBible


def test_normalize_and_catalog():
    assert "splash_action" in LAYOUT_CATALOG
    assert normalize_layout_intent("splash_action — fight") == "splash_action"
    assert normalize_layout_intent("object_closeup: letter") == "object_closeup"


def test_consecutive_layout_streak():
    assert consecutive_layout_streak([]) == 0
    assert consecutive_layout_streak(["splash_action", "dialogue_grid"]) == 1
    assert consecutive_layout_streak(["dialogue_grid", "dialogue_grid", "dialogue_grid: talk"]) == 3


def test_summarize_and_instructions_include_recent():
    summary = summarize_recent_layouts(
        ["splash_action", "widescreen_scene", "object_closeup"], limit=2
    )
    assert "widescreen_scene" in summary
    assert "object_closeup" in summary
    text = layout_diversity_instructions(["splash_action", "splash_action"])
    assert "splash_action" in text
    assert "Do NOT reuse" in text
    assert "dialogue_grid" in text  # catalog token present


def test_page_prompt_includes_anti_standee_line():
    plan = ComicPagePlan.model_validate(
        {
            "page_id": "p1",
            "purpose": "read",
            "layout_intent": "widescreen_scene",
            "panels": [{"panel_id": "1", "characters": ["R"], "action": "R walks"}],
        }
    )
    text = render_finished_page_prompt(
        plan,
        characters_by_name={"R": CharacterAsset(name="R", l1_prompt="man")},
        settings_by_name={},
        visual_bible=VisualBible(
            version="bible_v3",
            style_guide="manhua",
            color=ColorBible(palette=[], lighting="", forbidden=[]),
            characters={},
            content_hash="x",
        ),
    )
    assert "full-body standing" in text.lower() or "standee" in text.lower()
    assert ANTI_CENTER_STANDEE_LINE.split(";")[0][:20].lower() in text.lower() or (
        "centered full-body" in text.lower()
    )


def test_render_fingerprint_includes_anti_template_token():
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
        "lettering": "deferred_v3",
    }
    expected = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert fp == expected
