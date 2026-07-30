"""tests/test_screenwriter_pages.py — plan_comic_pages parsing (no net)."""

import asyncio

from core.api import ChatProvider
from core.schemas import ComicPagePlanSet, StoryElements
from core.screenwriter import PAGE_PLAN_TOOL, plan_comic_pages


class FakeChat(ChatProvider):
    def __init__(self, payload):
        self.payload = payload

    async def chat_function_call(self, messages, tools, tool_choice, **kwargs):
        assert tools[0]["function"]["name"] == "plan_comic_pages"
        return self.payload


def test_plan_comic_pages_validates_tool_payload():
    payload = {
        "unit_id": "0",
        "pages": [
            {
                "page_id": "p0001",
                "purpose": "open on the street",
                "layout_intent": "tall walking strip left; plaza wide right",
                "panels": [
                    {
                        "panel_id": "1",
                        "role": "establishing",
                        "action": "walks",
                        "characters": ["福贵"],
                        "caption": "傍晚，街灯初上。",
                    }
                ],
                "reference_characters": ["福贵"],
            }
        ],
    }
    elements = StoryElements(characters=[], settings=[], style_guide="manhua")
    out = asyncio.run(plan_comic_pages("傍晚……", elements, chat=FakeChat(payload)))
    assert isinstance(out, ComicPagePlanSet)
    assert out.unit_id == "0"
    assert out.pages[0].page_id == "p0001"
    assert out.pages[0].panels[0].caption == "傍晚，街灯初上。"


def test_plan_comic_pages_instructs_finished_layout_not_flat_grid():
    seen: dict = {}

    class CaptureChat(ChatProvider):
        async def chat_function_call(self, messages, tools, tool_choice, **kwargs):
            seen["messages"] = messages
            return {
                "unit_id": "0",
                "pages": [
                    {
                        "page_id": "p0001",
                        "purpose": "street scene",
                        "layout_intent": "splash with inset",
                        "panels": [{"panel_id": "1", "action": "walks", "caption": "傍晚。"}],
                    }
                ],
            }

    text = "傍晚，街灯初上。"
    asyncio.run(plan_comic_pages(text, StoryElements(), chat=CaptureChat()))
    blob = " ".join(m["content"] for m in seen["messages"])
    assert "finished" in blob.lower() or "readable pages" in blob.lower()
    assert "2x2" in blob or "flat" in blob.lower()


def test_page_plan_tool_schema_present():
    assert PAGE_PLAN_TOOL["type"] == "function"
    assert PAGE_PLAN_TOOL["function"]["name"] == "plan_comic_pages"
    assert "parameters" in PAGE_PLAN_TOOL["function"]


def test_system_prompt_mentions_manga_geometry():
    from core.screenwriter import SYSTEM_PROMPT

    assert "splash" in SYSTEM_PROMPT.lower() or "diagonal" in SYSTEM_PROMPT.lower()
    assert "2x2" in SYSTEM_PROMPT
