"""tests/test_screenwriter.py — extraction/storyboard parsing + sanitize (no net)."""

import asyncio

from core.api import ChatProvider
from core.schemas import StoryElements
from core.screenwriter import extract_story_elements, plan_storyboard, sanitize_text


class FakeChat(ChatProvider):
    def __init__(self, payload):
        self.payload = payload

    async def chat_function_call(self, messages, tools, tool_choice, **kwargs):
        return self.payload


def test_extract_story_elements_parses_payload():
    payload = {
        "characters": [{"name": "方鸿渐", "l1_prompt": "a young man"}],
        "settings": [{"name": "甲板", "scene_prompt": "ocean liner deck"}],
        "style_guide": "manhua style",
    }
    result = asyncio.run(extract_story_elements("text", chat=FakeChat(payload)))
    assert isinstance(result, StoryElements)
    assert result.characters[0].name == "方鸿渐"
    assert result.settings[0].scene_prompt == "ocean liner deck"
    assert result.style_guide == "manhua style"


def test_extract_story_elements_accepts_stringified_character_list():
    payload = {
        "characters": '[{"name": "张一新", "l1_prompt": "conflict and reflection."}]',
        "settings": "[]",
        "style_guide": "manhua style",
    }
    result = asyncio.run(extract_story_elements("text", chat=FakeChat(payload)))
    assert result.characters[0].name == "张一新"
    assert result.settings == []


def test_plan_storyboard_parses_payload():
    payload = {
        "chapter_id": "ch01",
        "panels": [{"panel_id": "ch01_p01", "action": "look at the sea"}],
    }
    result = asyncio.run(plan_storyboard("text", StoryElements(), chat=FakeChat(payload)))
    assert result.chapter_id == "ch01"
    assert result.panels[0].panel_id == "ch01_p01"


def test_plan_storyboard_instructs_source_language_for_dialogue():
    """Chinese novels must not silently become English speech bubbles."""
    seen: dict = {}

    class CaptureChat(ChatProvider):
        async def chat_function_call(self, messages, tools, tool_choice, **kwargs):
            seen["messages"] = messages
            return {
                "chapter_id": "ch01",
                "panels": [
                    {
                        "panel_id": "p1",
                        "action": "look at the sea",
                        "dialogue": "这海上的日子，倒也清静。",
                    }
                ],
            }

    text = "刘慈欣 三体\n叶文洁望着天线。"
    result = asyncio.run(plan_storyboard(text, StoryElements(), chat=CaptureChat()))
    blob = " ".join(m["content"] for m in seen["messages"])
    assert "same language" in blob.lower() or "Chinese" in blob
    assert "never English" in blob or "do not translate" in blob.lower()
    assert result.panels[0].dialogue and "海上" in result.panels[0].dialogue


def test_system_prompt_forbids_translating_dialogue():
    from core.screenwriter import SYSTEM_PROMPT

    assert "dialogue" in SYSTEM_PROMPT.lower()
    assert "same language" in SYSTEM_PROMPT.lower() or "Chinese" in SYSTEM_PROMPT


def test_sanitize_text_redacts_banned_terms():
    assert "抹胸" not in sanitize_text("wear a 抹胸", banned=["抹胸"])
    assert "■" in sanitize_text("wear a 抹胸", banned=["抹胸"])


def test_system_prompt_requires_evidence():
    from core.screenwriter import SYSTEM_PROMPT

    lower = SYSTEM_PROMPT.lower()
    assert "verbatim" in lower, "SYSTEM_PROMPT must require verbatim source quotes"
    assert "evidence" in lower, "SYSTEM_PROMPT must reference evidence"


def test_system_prompt_instructs_no_fabrication():
    from core.screenwriter import SYSTEM_PROMPT

    lower = SYSTEM_PROMPT.lower()
    assert "do not invent" in lower or "do not" in lower
