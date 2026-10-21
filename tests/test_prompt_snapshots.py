"""Golden snapshots for every chat-stage request.

A prompt edit that is not intentional should fail here before it can change a
plan. Regenerate deliberately with::

    UPDATE_PROMPTS=1 .venv/bin/python -m pytest tests/test_prompt_snapshots.py

The snapshot carries the full messages, the forced tool, the model, and the
request digest. ``system``/``user`` changes both show up in the JSON diff.
"""

import json
import os
from pathlib import Path

import pytest

from core import screenwriter as sw
from core.schemas import (
    CharacterAsset,
    KeyBeat,
    KeyBeatSet,
    Panel,
    Setting,
    Storyboard,
    StoryElements,
    VisualBible,
)

FIXTURES = Path(__file__).parent / "fixtures" / "prompts"
TEXT = "第一回\n福贵在村口。"
MODEL = "fixture-model"


def _context():
    chars = {
        "福贵": CharacterAsset(
            name="福贵", l1_prompt="middle-aged farmer", portrait_prompt="portrait"
        )
    }
    elements = StoryElements(
        characters=[
            CharacterAsset(name="福贵", l1_prompt="middle-aged farmer", portrait_prompt="portrait")
        ],
        settings=[Setting(name="村口", scene_prompt="village entrance at dusk")],
        style_guide="manhua",
    )
    beats = KeyBeatSet(
        beats=[
            KeyBeat(
                beat_id="b1",
                summary="福贵回到村口",
                must_draw=True,
                characters=["福贵"],
            )
        ]
    )
    bible = VisualBible(style_guide="manhua")
    board = Storyboard(
        chapter_id="ch01",
        panels=[
            Panel(
                panel_id="p1",
                action="福贵走进村口",
                characters_present=["福贵"],
                reference_characters=["福贵"],
            )
        ],
    )
    return chars, elements, beats, bible, board


def _requests():
    chars, elements, beats, bible, board = _context()
    return {
        "extract": sw.build_extract_request(TEXT, model=MODEL),
        "beats": sw.build_beats_request(TEXT, elements, model=MODEL),
        "bible": sw.build_bible_request(
            TEXT,
            chars,
            bible,
            alias_hints=[("福贵", "富贵", "name variant")],
            preferred_style="manhua",
            model=MODEL,
        ),
        "storyboard": sw.build_storyboard_request(
            TEXT, elements, key_beats=beats, panel_budget=8, tier="B", model=MODEL
        ),
        "page_plan": sw.build_page_plan_request(
            TEXT,
            elements,
            recent_layouts=["splash_action"],
            visual_bible=bible,
            key_beats=beats,
            panel_budget=8,
            tier="B",
            model=MODEL,
        ),
        "page_script": sw.build_page_script_request(board, elements, TEXT, model=MODEL),
    }


@pytest.mark.parametrize(
    "stage", ["extract", "beats", "bible", "storyboard", "page_plan", "page_script"]
)
def test_stage_prompt_matches_its_snapshot(stage):
    request = _requests()[stage]
    actual = {
        "stage": stage,
        "digest": request.digest(),
        "tool": request.tool_choice["function"]["name"],
        "model": request.model,
        "messages": request.messages,
    }
    path = FIXTURES / f"{stage}.json"
    if os.environ.get("UPDATE_PROMPTS"):
        path.write_text(json.dumps(actual, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return
    expected = json.loads(path.read_text(encoding="utf-8"))
    assert actual == expected
