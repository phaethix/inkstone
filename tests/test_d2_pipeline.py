"""tests.test_d2_pipeline — creative_comic 集成：page_script 写入/续跑/拒绝（fakes，不网络）。"""

import asyncio
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from core.api import ChatProvider, ImageProvider
from core.pipelines.creative_comic import creative_comic
from core.schemas import ProjectState


class FakeImageOutput:
    def __init__(self):
        self.fmt = "b64"
        self.data = ""
        self.ext = "png"

    def save(self, path):
        Image.new("RGB", (20, 20), (120, 120, 120)).save(path)


class FakeImage(ImageProvider):
    def __init__(self):
        self.calls = 0

    async def generate_single_image(self, prompt, reference_image_paths=None, size=None, **kw):
        self.calls += 1
        return FakeImageOutput()


class D2FakeChat(ChatProvider):
    """extract/storyboard 复用基线行为，并额外产出 page_script。"""

    def __init__(self):
        self.calls = 0
        self.board = 0

    async def chat_function_call(self, messages, tools, tool_choice, **kw):
        self.calls += 1
        name = tool_choice["function"]["name"]
        if name == "plan_page_script":
            return {
                "chapter_id": f"ch{self.board:02d}",
                "pages": [
                    {
                        "page_index": 0,
                        "required_information": "方鸿渐在甲板上。",
                        "causal_links": [],
                        "source_spans": [],
                        "panel_ids": [f"ch{self.board:02d}_p01"],
                    }
                ],
                "skipped_pages": [],
            }
        if name == "extract_story_elements":
            return {
                "characters": [
                    {"name": "方鸿渐", "l1_prompt": "a young man", "portrait_prompt": "portrait"}
                ],
                "settings": [{"name": "甲板", "scene_prompt": "deck"}],
                "style_guide": "manhua",
            }
        if name == "plan_storyboard":
            self.board += 1
            return {
                "chapter_id": f"ch{self.board:02d}",
                "panels": [
                    {
                        "panel_id": f"ch{self.board:02d}_p01",
                        "characters_present": ["方鸿渐"],
                        "setting_ref": "甲板",
                        "action": "look at the sea",
                        "reference_characters": ["方鸿渐"],
                        "size": "1024x1024",
                    }
                ],
            }
        return {}


class RejectPageScriptChat(D2FakeChat):
    async def chat_function_call(self, messages, tools, tool_choice, **kw):
        name = tool_choice["function"]["name"]
        if name == "plan_page_script":
            raise RuntimeError("content_policy_violation: page-script rejected")
        return await super().chat_function_call(messages, tools, tool_choice, **kw)


def _fake_export_pdf(self, page_dir, out="comic.pdf", layout="TwoPageRight", direction="R2L"):
    Path(out).write_bytes(b"%PDF-1.4 fake")
    return out


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_d2_pipeline_writes_and_resumes_page_script(tmp_path, monkeypatch):
    monkeypatch.setenv("INKSTONE_PAGE_SCRIPT", "1")
    src = "第一章\n方鸿渐在甲板上。\n第二章\n方鸿渐在读书。"
    chat = D2FakeChat()
    asyncio.run(creative_comic(src, output_dir=str(tmp_path), chat=chat, image=FakeImage()))
    # 2 chunks → 2 extract + 2 storyboard + 2 page_script = 6
    assert chat.calls == 6
    state = ProjectState.load(tmp_path / "state.json")
    for key in ("0", "1"):
        ps = state.chunk_cache[key].page_script
        assert ps is not None
        assert ps.pages and ps.pages[0].required_information
    # 续跑：已缓存 → page_script 复用，不重算（不重复计费）
    chat2 = D2FakeChat()
    asyncio.run(creative_comic(src, output_dir=str(tmp_path), chat=chat2, image=FakeImage()))
    assert chat2.calls == 0
    state2 = ProjectState.load(tmp_path / "state.json")
    for key in ("0", "1"):
        assert state2.chunk_cache[key].page_script is not None


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_d2_pipeline_skips_page_script_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("INKSTONE_PAGE_SCRIPT", raising=False)
    src = "第一章\n方鸿渐在甲板上。\n第二章\n方鸿渐在读书。"
    chat = D2FakeChat()
    asyncio.run(creative_comic(src, output_dir=str(tmp_path), chat=chat, image=FakeImage()))
    assert chat.calls == 4  # 2 extract + 2 storyboard (no plan_page_script)
    state = ProjectState.load(tmp_path / "state.json")
    for key in ("0", "1"):
        assert state.chunk_cache[key].page_script is None


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_d2_pipeline_skipped_pages_on_policy_rejection(tmp_path, monkeypatch):
    monkeypatch.setenv("INKSTONE_PAGE_SCRIPT", "1")
    src = "第一章\n方鸿渐在甲板上。\n第二章\n方鸿渐在读书。"
    chat = RejectPageScriptChat()
    proj = asyncio.run(creative_comic(src, output_dir=str(tmp_path), chat=chat, image=FakeImage()))
    # 信息闸门失效但不阻断整块：panels 仍产出
    assert set(proj.state.panels_done) == {"c0000-p0000", "c0001-p0000"}
    state = ProjectState.load(tmp_path / "state.json")
    for key in ("0", "1"):
        ps = state.chunk_cache[key].page_script
        assert ps is not None
        assert ps.pages == []
        assert ps.skipped_pages == [0]  # 每 chunk 1 面板 → 1 页被跳过


def test_d2_pipeline_extension_block_is_small():
    """creative_comic.py 在 storyboard 后、panels 前仅插入一个扩展块（净增 ≤20 行）。"""
    path = Path(__file__).resolve().parents[1] / "core" / "pipelines" / "creative_comic.py"
    text = path.read_text(encoding="utf-8")
    start = text.index("# ---- page-script")
    end = text.index("# ---- panels ----", start)
    block = text[start:end]
    added = [ln for ln in block.splitlines() if ln.strip()]
    assert len(added) <= 22
