"""tests.test_safety — content-policy rejection handling (no network).

Covers the M2 requirement that a single panel rejected by the upstream content
filter is skipped (logged + recorded) instead of aborting the whole run, plus the
detector used to classify such failures.
"""

import asyncio
from pathlib import Path
from unittest.mock import patch

import requests
from requests import Response

from core.api import ChatProvider, ImageProvider
from core.pipelines.creative_comic import creative_comic
from core.screenwriter import is_content_policy_rejection


class FakeImageOutput:
    def __init__(self):
        self.fmt = "b64"
        self.data = ""
        self.ext = "png"

    def save(self, path):
        from PIL import Image

        Image.new("RGB", (20, 20), (120, 120, 120)).save(path)


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


class RejectingImage(ImageProvider):
    """Succeeds the portrait + chapter-1 panel, then rejects chapter-2's panel
    with a content-policy error so we can assert graceful skip."""

    def __init__(self):
        self.calls = 0

    async def generate_single_image(self, prompt, reference_image_paths=None, size=None, **kw):
        self.calls += 1
        # call order: portrait(1) -> ch01 panel(2) -> ch02 panel(3, rejected)
        if self.calls == 3:
            raise RuntimeError("Agnes image error: content_policy_violation: prompt rejected")
        return FakeImageOutput()


def _fake_export_pdf(self, page_dir, out="comic.pdf", layout="TwoPageRight", direction="R2L"):
    # Stand-in for the manga2pdf CLI (not installed in test/CI envs).
    Path(out).write_bytes(b"%PDF-1.4 fake")
    return out


def _http_400() -> requests.HTTPError:
    resp = Response()
    resp.status_code = 400
    return requests.HTTPError("400 Client Error", response=resp)


def test_is_content_policy_rejection_detects_variants():
    assert is_content_policy_rejection(_http_400()) is True
    assert is_content_policy_rejection(RuntimeError("content_policy_violation")) is True
    assert is_content_policy_rejection(RuntimeError("content policy rejected")) is True
    # Genuine transient / other failures must NOT be classified as content rejections.
    assert is_content_policy_rejection(requests.HTTPError("500 Server Error")) is False
    assert is_content_policy_rejection(RuntimeError("rate limit")) is False


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_orchestration_skips_rejected_panel(tmp_path):
    src = "第一章\n方鸿渐在甲板上。\n第二章\n方鸿渐在读书。"
    chat, img = FakeChat(), RejectingImage()
    proj = asyncio.run(creative_comic(src, output_dir=str(tmp_path), chat=chat, image=img))

    # Chapter 1 panel succeeded; chapter 2's panel was skipped, not crash.
    assert "ch01_p01" in proj.state.panels_done
    assert "ch02_p01" in proj.state.skipped
    assert "ch02_p01" not in proj.state.panels_done
    assert img.calls == 3  # portrait + 2 panel attempts (1 rejected but still a call)
    assert proj.pdf and Path(proj.pdf).exists()
