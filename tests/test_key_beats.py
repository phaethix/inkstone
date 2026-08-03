# tests/test_key_beats.py
import asyncio
import hashlib
import json

from core.comic.key_beats import (
    beat_coverage_retry_note,
    covers_beats_prompt_line,
    uncovered_must_draw_beats,
)
from core.comic.page_prompt import render_finished_page_prompt
from core.pipelines.creative_comic import _render_fingerprint
from core.schemas import (
    CharacterAsset,
    ComicPagePlan,
    ComicPagePlanSet,
    KeyBeat,
    KeyBeatSet,
    ModelSnapshot,
    StoryElements,
)
from core.screenwriter import extract_key_beats, plan_comic_pages


class FakeBeatChat:
    def __init__(self):
        self.calls = 0
        self.names: list[str] = []

    async def chat_function_call(self, messages, tools, tool_choice, **kw):
        self.calls += 1
        name = tool_choice["function"]["name"]
        self.names.append(name)
        if name == "extract_key_beats":
            return {
                "beats": [
                    {
                        "beat_id": "open_letter",
                        "summary": "thick unsigned letter opened",
                        "must_draw": True,
                        "characters": ["R"],
                    },
                    {
                        "beat_id": "child_death",
                        "summary": "dead child and four candles",
                        "must_draw": True,
                        "characters": ["陌生女人"],
                    },
                ]
            }
        if name == "plan_comic_pages":
            # First plan misses a beat; second covers both if retry note present.
            user = messages[-1]["content"]
            if "child_death" in user and "CRITICAL" in user:
                covers = ["open_letter", "child_death"]
            elif self.names.count("plan_comic_pages") > 1:
                covers = ["open_letter", "child_death"]
            else:
                covers = ["open_letter"]
            return {
                "unit_id": "u1",
                "pages": [
                    {
                        "page_id": "p1",
                        "purpose": "beats",
                        "layout_intent": "widescreen_scene",
                        "covers_beats": covers,
                        "panels": [
                            {
                                "panel_id": "1",
                                "characters": ["R"],
                                "action": "stages the beat",
                            }
                        ],
                    }
                ],
            }
        return {}


def test_uncovered_must_draw_and_retry_note():
    beats = KeyBeatSet(
        beats=[
            KeyBeat(beat_id="a", summary="A", must_draw=True),
            KeyBeat(beat_id="b", summary="B", must_draw=True),
            KeyBeat(beat_id="c", summary="C", must_draw=False),
        ]
    )
    pageset = ComicPagePlanSet(
        pages=[
            ComicPagePlan.model_validate(
                {
                    "page_id": "p1",
                    "covers_beats": ["a"],
                    "panels": [],
                }
            )
        ]
    )
    missing = uncovered_must_draw_beats(beats, pageset)
    assert [b.beat_id for b in missing] == ["b"]
    note = beat_coverage_retry_note(missing)
    assert "b" in note and "CRITICAL" in note


def test_extract_and_plan_retry_covers_beats():
    chat = FakeBeatChat()
    elements = StoryElements(characters=[], settings=[], style_guide="manhua")
    beats = asyncio.run(extract_key_beats("厚信。孩子死了。", elements, chat=chat))
    assert {b.beat_id for b in beats.beats} == {"open_letter", "child_death"}
    pageset = asyncio.run(
        plan_comic_pages("厚信。孩子死了。", elements, chat=chat, key_beats=beats)
    )
    missing = uncovered_must_draw_beats(beats, pageset)
    if missing:
        pageset = asyncio.run(
            plan_comic_pages(
                "厚信。孩子死了。",
                elements,
                chat=chat,
                key_beats=beats,
                extra_user_note=beat_coverage_retry_note(missing),
            )
        )
    assert not uncovered_must_draw_beats(beats, pageset)


def test_page_prompt_includes_covers_beats_staging_line():
    plan = ComicPagePlan.model_validate(
        {
            "page_id": "p1",
            "purpose": "x",
            "layout_intent": "widescreen_scene",
            "covers_beats": ["open_letter"],
            "panels": [{"panel_id": "1", "characters": ["R"], "action": "opens letter"}],
        }
    )
    assert covers_beats_prompt_line(plan) is not None
    text = render_finished_page_prompt(
        plan,
        characters_by_name={"R": CharacterAsset(name="R", l1_prompt="man")},
        settings_by_name={},
    )
    assert "open_letter" in text
    assert "physical" in text.lower() or "environment" in text.lower()


def test_fingerprint_includes_beats_token():
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
        "beats": "v1",
        "lettering": "deferred_v3",
    }
    expected = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert fp == expected
