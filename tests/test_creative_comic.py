"""tests/test_creative_comic.py — orchestration end-to-end (fakes, no network)."""

import asyncio
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from core.api import ChatProvider, ImageProvider
from core.pipelines.creative_comic import creative_comic


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


class FakeChat(ChatProvider):
    def __init__(self):
        self.calls = 0
        self.board = 0

    async def chat_function_call(self, messages, tools, tool_choice, **kw):
        self.calls += 1
        name = tool_choice["function"]["name"]
        if name == "extract_story_elements":
            return {
                "characters": [
                    {
                        "name": "方鸿渐",
                        "l1_prompt": "a young man",
                        "portrait_prompt": "portrait of a young man",
                    }
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


def _fake_export_pdf(self, page_dir, out="comic.pdf", layout="TwoPageRight", direction="R2L"):
    # Stand-in for the manga2pdf CLI (not installed in test/CI envs).
    Path(out).write_bytes(b"%PDF-1.4 fake")
    return out


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_creative_comic_generates_and_resumes(tmp_path):
    src = "第一章\n方鸿渐在甲板上。\n第二章\n方鸿渐在读书。"
    chat, img = FakeChat(), FakeImage()
    proj = asyncio.run(creative_comic(src, output_dir=str(tmp_path), chat=chat, image=img))

    # Both chapters produced a panel; portrait generated once for the new char.
    assert set(proj.state.panels_done) == {"ch01_p01", "ch02_p01"}
    assert "方鸿渐" in proj.state.generated.portraits
    assert (tmp_path / "assets" / "portraits" / "方鸿渐.png").exists()
    assert proj.pdf and Path(proj.pdf).exists()
    assert img.calls == 3  # 1 portrait + 2 panels (char reused on 2nd chunk)
    assert chat.calls == 4  # 2 extract + 2 storyboard

    # Resume: everything is in state.json, so no new generation happens.
    chat2, img2 = FakeChat(), FakeImage()
    asyncio.run(creative_comic(src, output_dir=str(tmp_path), chat=chat2, image=img2))
    assert img2.calls == 0
    assert chat2.calls == 0


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_creative_comic_regenerates_deleted_panel(tmp_path):
    src = "第一章\n方鸿渐在甲板上。\n第二章\n方鸿渐在读书。"
    asyncio.run(creative_comic(src, output_dir=str(tmp_path), chat=FakeChat(), image=FakeImage()))

    # Delete one finished panel file, then rerun.
    deleted = tmp_path / "panels" / "ch01_p01.png"
    assert deleted.exists()
    deleted.unlink()

    chat2, img2 = FakeChat(), FakeImage()
    proj = asyncio.run(creative_comic(src, output_dir=str(tmp_path), chat=chat2, image=img2))

    # Exactly the deleted panel is regenerated; the other is skipped (dedup holds).
    assert img2.calls == 1
    assert deleted.exists()
    assert set(proj.state.panels_done) == {"ch01_p01", "ch02_p01"}


def test_creative_comic_webtoon_output(tmp_path):
    src = "第一章\n方鸿渐在甲板上。\n第二章\n方鸿渐在读书。"
    chat, img = FakeChat(), FakeImage()
    proj = asyncio.run(
        creative_comic(src, output_dir=str(tmp_path), chat=chat, image=img, output_format="webtoon")
    )

    # Webtoon produces a single vertical strip PNG and no PDF (no external CLI).
    assert proj.pdf is None
    assert proj.webtoon and Path(proj.webtoon).exists()
    assert proj.webtoon.endswith("webtoon.png")
    assert proj.pages == [proj.webtoon]
