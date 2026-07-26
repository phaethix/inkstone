"""tests.test_d2_screenwriter — plan_page_script 结构与 span.text 反推（不调网络）。"""

import asyncio

from core.api import ChatProvider
from core.schemas import PageScript, Panel, Storyboard, StoryElements
from core.screenwriter import PAGE_SCRIPT_TOOL, plan_page_script


class FakeChat(ChatProvider):
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    async def chat_function_call(self, messages, tools, tool_choice, **kwargs):
        self.calls += 1
        return self.payload


def _board(n: int) -> Storyboard:
    return Storyboard(chapter_id="chapter_1", panels=[Panel(panel_id=f"p{i}") for i in range(n)])


def test_plan_page_script_returns_page_script_with_pages():
    # 8 个面板 → 期望 ≈2 页（每 4 面板一页；模型负责分组，这里返回 2 页）
    payload = {
        "chapter_id": "chapter_1",
        "pages": [
            {
                "page_index": 0,
                "required_information": "方鸿渐在甲板远眺。",
                "causal_links": [{"cause": "登船", "effect": "遇见苏小姐"}],
                "source_spans": [{"start": 0, "end": 3, "chapter_id": "chapter_1"}],
                "panel_ids": ["p0", "p1", "p2", "p3"],
            },
            {
                "page_index": 1,
                "required_information": "方鸿渐在书房读书。",
                "causal_links": [],
                "source_spans": [{"start": 10, "end": 13, "chapter_id": "chapter_1"}],
                "panel_ids": ["p4", "p5", "p6", "p7"],
            },
        ],
        "skipped_pages": [],
    }
    board = _board(8)
    ps = asyncio.run(
        plan_page_script(board, StoryElements(), "方鸿渐在甲板远眺大海。", chat=FakeChat(payload))
    )
    assert isinstance(ps, PageScript)
    assert ps.chapter_id == "chapter_1"
    assert len(ps.pages) == 2  # 与 storyboard 分组数一致
    for page in ps.pages:
        assert page.required_information.strip()  # 必含信息非空
        assert page.source_spans  # 至少 1 条 span
        sp = page.source_spans[0]
        assert sp.start is not None and sp.end is not None and sp.chapter_id == "chapter_1"


def test_span_text_server_side_derived():
    """span.text 由服务端反推为 chunk[start:end]，即便模型未回传 text。"""
    chunk = "方鸿渐在甲板远眺大海，遇见苏小姐。"
    payload = {
        "chapter_id": "chapter_1",
        "pages": [
            {
                "page_index": 0,
                "required_information": "甲板远眺。",
                "causal_links": [],
                "source_spans": [{"start": 0, "end": 3}],
                "panel_ids": [],
            }
        ],
        "skipped_pages": [],
    }
    ps = asyncio.run(plan_page_script(_board(1), StoryElements(), chunk, chat=FakeChat(payload)))
    assert ps.pages[0].source_spans[0].text == chunk[0:3]  # "方鸿渐"


def test_plan_page_script_does_not_call_chat_when_provided():
    payload = {
        "chapter_id": "c",
        "pages": [
            {
                "page_index": 0,
                "required_information": "x",
                "causal_links": [],
                "source_spans": [],
                "panel_ids": [],
            }
        ],
        "skipped_pages": [],
    }
    chat = FakeChat(payload)
    asyncio.run(plan_page_script(_board(1), StoryElements(), "text", chat=chat))
    assert chat.calls == 1  # 仅一次 chat_function_call


def test_page_script_tool_schema_present():
    assert PAGE_SCRIPT_TOOL["type"] == "function"
    assert PAGE_SCRIPT_TOOL["function"]["name"] == "plan_page_script"
    assert "parameters" in PAGE_SCRIPT_TOOL["function"]
