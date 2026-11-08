"""tests/test_panel_compose_pipeline.py — panel_compose orchestration (fakes, no network)."""

import asyncio
import json
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
        Image.new("RGB", (20, 30), (90, 90, 90)).save(path)


class FakeImage(ImageProvider):
    def __init__(self):
        self.calls = 0
        self.panel_prompts: list[str] = []

    async def generate_single_image(self, prompt, reference_image_paths=None, size=None, **kw):
        self.calls += 1
        if "Exactly" in prompt or "No people in this panel" in prompt:
            self.panel_prompts.append(prompt)
        return FakeImageOutput()


class FakeChat(ChatProvider):
    """Same staged answers as tests/test_finished_page_pipeline.py."""

    def __init__(self):
        self.calls = 0

    async def chat_function_call(self, messages, tools, tool_choice, **kw):
        self.calls += 1
        name = tool_choice["function"]["name"]
        if name == "extract_story_elements":
            return {
                "characters": [
                    {
                        "name": "福贵",
                        "l1_prompt": "a middle-aged farmer",
                        "portrait_prompt": "portrait of a farmer",
                    }
                ],
                "settings": [{"name": "村口", "scene_prompt": "village entrance at dusk"}],
                "style_guide": "manhua",
            }
        if name == "reconcile_visual_bible":
            return {
                "merges": [],
                "stages": [],
                "keeps": [],
                "color_patches": [],
                "style_guide": "manhua",
                "color": {
                    "palette": [{"name": "ink", "hex": "#1A1A1A", "usage": "lines"}],
                    "lighting": "soft",
                    "forbidden": [],
                },
                "canons": [
                    {
                        "canonical_name": "福贵",
                        "face_lock": "a middle-aged farmer",
                        "stages": [
                            {
                                "stage": "adult",
                                "outfit_lock": "simple farmer clothes",
                                "hair_lock": "short dark hair",
                                "portrait_key": "福贵",
                            }
                        ],
                    }
                ],
            }
        if name == "extract_key_beats":
            return {
                "beats": [
                    {
                        "beat_id": "village_gate",
                        "summary": "福贵 walks through the village entrance",
                        "must_draw": True,
                        "characters": ["福贵"],
                    }
                ]
            }
        if name == "plan_comic_pages":
            return {
                "unit_id": "1",
                "pages": [
                    {
                        "page_id": "u1_p0001",
                        "purpose": "establish the village entrance",
                        "layout_intent": "single wide establishing panel",
                        "covers_beats": ["village_gate"],
                        "panels": [
                            {
                                "panel_id": "1",
                                "role": "establishing",
                                "shape_hint": "wide",
                                "shot": "wide",
                                "action": "福贵 walks through the village entrance",
                                "characters": ["福贵"],
                                "setting_ref": "村口",
                                "caption": "傍晚，村口。",
                                "dialogue": [{"speaker": "福贵", "text": "我回来了。"}],
                            }
                        ],
                        "reference_characters": ["福贵"],
                        "setting_refs": ["村口"],
                    }
                ],
            }
        return {}


def _fake_export_pdf(self, page_dir, out="comic.pdf", layout="TwoPageRight", direction="R2L"):
    # Stand-in for the manga2pdf CLI (not installed in test/CI envs).
    Path(out).write_bytes(b"%PDF-1.4 fake")
    return out


@patch("core.comic.export.ExportEngine.export_pdf", _fake_export_pdf)
def test_panel_compose_renders_panels_and_composes_pages(tmp_path, monkeypatch):
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "panel_compose")
    # Lettering must be a contiguous span of the source (scrub_chunk_lettering),
    # so the caption and the spoken line are copied into the chunk text — same
    # contract as test_finished_page_writes_blank_and_lettered.
    src = "第一章\n福贵在村口。傍晚，村口。我回来了。"
    chat, img = FakeChat(), FakeImage()
    proj = asyncio.run(creative_comic(src, output_dir=str(tmp_path), chat=chat, image=img))

    page_files = sorted((tmp_path / "pages").glob("page_c*_p*.png"))
    blank_files = sorted((tmp_path / "pages" / "blank").glob("page_c*_p*.png"))
    panel_files = sorted((tmp_path / "panels").glob("c0000-p0000-g*.png"))
    assert page_files and blank_files and panel_files

    assert proj.state.render_mode == "panel_compose"
    page_key = "c0000-p0000"
    assert page_key in proj.state.generated.pages
    assert page_key in proj.state.pages_done
    page = proj.state.generated.pages[page_key]
    assert page.mode == "finished_lettered"
    assert Path(page.blank_local).is_file()
    assert blank_files[0].read_bytes() != page_files[0].read_bytes()
    panel_key = "c0000-p0000-g0000"
    assert panel_key in proj.state.generated.panels
    assert panel_key in proj.state.panels_done

    assert proj.pdf and Path(proj.pdf).exists()
    assert img.calls == 2  # 1 portrait + 1 panel
    assert chat.calls == 4  # extract + reconcile + beats + page_plan

    prompt = img.panel_prompts[0]
    assert "Exactly 1 character in this panel: 福贵" in prompt
    assert "village_gate" not in prompt
    assert "covers_beats" not in prompt
    assert "clean artwork with no overlays" in prompt

    lines = [
        json.loads(line)
        for line in (tmp_path / "runs.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    intents = [row for row in lines if row["kind"] == "intent"]
    assert [(row["stage"], row["key"]) for row in intents] == [
        ("extract", "c0000"),
        ("bible", "c0000"),
        ("portrait", "福贵"),
        ("beats", "c0000"),
        ("page_plan", "c0000"),
        ("render", panel_key),
    ]

    chat2, img2 = FakeChat(), FakeImage()
    proj2 = asyncio.run(creative_comic(src, output_dir=str(tmp_path), chat=chat2, image=img2))
    assert img2.calls == 0
    assert chat2.calls == 0
    assert proj2.state.panels_done == proj.state.panels_done
