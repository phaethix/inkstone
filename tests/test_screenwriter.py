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


def test_plan_storyboard_parses_payload():
    payload = {
        "chapter_id": "ch01",
        "panels": [{"panel_id": "ch01_p01", "action": "look at the sea"}],
    }
    result = asyncio.run(plan_storyboard("text", StoryElements(), chat=FakeChat(payload)))
    assert result.chapter_id == "ch01"
    assert result.panels[0].panel_id == "ch01_p01"


def test_sanitize_text_redacts_banned_terms():
    assert "抹胸" not in sanitize_text("wear a 抹胸", banned=["抹胸"])
    assert "■" in sanitize_text("wear a 抹胸", banned=["抹胸"])
