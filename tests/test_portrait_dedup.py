"""tests/test_portrait_dedup.py — one face per distinct portrait prompt.

Measured on a real Agnes run: ``沈知意``, ``沈知意@default`` and ``少年@default``
each paid for an image call even though the prompt sent to the model was byte
for byte the same as their base character's. ``portrait_action_key`` includes
``name`` in its params (and ``_portrait_key_material`` includes it in the
material), so two rows of one face never share a key and each is rendered.

A genuine age/look stage (``Name@adult``) is not redundant: its sent prompt
carries the stage locks, so it stays an independent portrait. The group key is
the *final* prompt that would be sent, not the raw character fields.
"""

import asyncio
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from core.api import ChatProvider, ImageProvider
from core.pipelines.creative_comic import creative_comic


class _FakeImageOutput:
    def save(self, path):
        Image.new("RGB", (20, 30), (90, 90, 90)).save(path)


class _CountingImage(ImageProvider):
    def __init__(self):
        self.calls = 0

    async def generate_single_image(self, prompt, reference_image_paths=None, size=None, **kw):
        self.calls += 1
        return _FakeImageOutput()


class _StagedChat(ChatProvider):
    """One character, with a redundant @default stage and a real @adult stage."""

    def __init__(self):
        self.page_plan_calls = 0

    async def chat_function_call(self, messages, tools, tool_choice, **kw):
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
                "stages": [],
                "keeps": [],
                "merges": [],
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
                        "gender": "male",
                        "role": "protagonist",
                        "stages": [
                            {"stage": "default", "portrait_key": "福贵@default"},
                            {
                                "stage": "adult",
                                "portrait_key": "福贵@adult",
                                "age_look": "older weathered face",
                                "outfit_lock": "patched farmer clothes",
                                "hair_lock": "short dark hair",
                            },
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
            self.page_plan_calls += 1
            return {
                "unit_id": str(self.page_plan_calls),
                "pages": [
                    {
                        "page_id": f"u{self.page_plan_calls}_p0001",
                        "purpose": "establish the village entrance",
                        "covers_beats": ["village_gate"],
                        "panels": [
                            {
                                "panel_id": "1",
                                "role": "establishing",
                                "shot": "wide",
                                "action": "福贵 walks through the village entrance",
                                "characters": ["福贵"],
                                "setting_ref": "村口",
                            }
                        ],
                        "reference_characters": ["福贵"],
                        "setting_refs": ["村口"],
                    }
                ],
            }
        return {}


def _fake_export_pdf(self, page_dir, out="comic.pdf", layout="TwoPageRight", direction="R2L"):
    Path(out).write_bytes(b"%PDF-1.4 fake")
    return out


@patch("core.comic.export.ExportEngine.export_pdf", _fake_export_pdf)
def test_same_prompt_variants_share_one_image_call(tmp_path, monkeypatch):
    """base + @default (same sent prompt) draw once; @adult still draws its own."""
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "finished_page")
    img = _CountingImage()
    proj = asyncio.run(
        creative_comic(
            "第一章\n福贵在村口。",
            output_dir=str(tmp_path),
            chat=_StagedChat(),
            image=img,
        )
    )

    portraits = proj.state.generated.portraits
    # The redundant @default row must still resolve to a real portrait file.
    for name in ("福贵", "福贵@default"):
        assert name in portraits, f"{name} lost its portrait"
        assert Path(portraits[name]).is_file()
    # A same-prompt variant reuses the base file rather than a second render.
    assert Path(portraits["福贵@default"]).read_bytes() == Path(portraits["福贵"]).read_bytes()
    # @adult is a genuinely different look, so it keeps its own image.
    assert "福贵@adult" in portraits
    assert Path(portraits["福贵@adult"]).is_file()

    lines = [
        __import__("json").loads(line)
        for line in (tmp_path / "runs.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    portrait_intents = [r for r in lines if r.get("kind") == "intent" and r["stage"] == "portrait"]
    keys = sorted({r["key"] for r in portrait_intents})
    assert keys == ["福贵", "福贵@adult"], f"unexpected portrait intents: {keys}"
    # 2 portraits (base, @adult) + 1 page; base and @default must not both draw.
    assert img.calls == 3, f"expected 3 image calls, got {img.calls}"


class _TwoNamesChat(_StagedChat):
    """Two distinct characters that happen to share one portrait prompt.

    The alias-merge cost probe builds exactly this pair (方鸿渐 / 鸿渐):
    dedup must not fold them into one face, or their identity ledger breaks.
    """

    async def chat_function_call(self, messages, tools, tool_choice, **kw):
        name = tool_choice["function"]["name"]
        if name == "extract_story_elements":
            return {
                "characters": [
                    {"name": "方鸿渐", "l1_prompt": "a young man", "portrait_prompt": "portrait"},
                    {"name": "鸿渐", "l1_prompt": "a young man", "portrait_prompt": "portrait"},
                ],
                "settings": [{"name": "村口", "scene_prompt": "village entrance at dusk"}],
                "style_guide": "manhua",
            }
        if name == "reconcile_visual_bible":
            return {
                "stages": [],
                "keeps": [],
                "merges": [],
                "color_patches": [],
                "style_guide": "manhua",
                "color": {
                    "palette": [{"name": "ink", "hex": "#1A1A1A", "usage": "lines"}],
                    "lighting": "soft",
                    "forbidden": [],
                },
                "canons": [
                    {"canonical_name": "方鸿渐", "face_lock": "a young man", "gender": "male"},
                    {"canonical_name": "鸿渐", "face_lock": "a young man", "gender": "male"},
                ],
            }
        return await super().chat_function_call(messages, tools, tool_choice, **kw)


@patch("core.comic.export.ExportEngine.export_pdf", _fake_export_pdf)
def test_distinct_names_with_same_prompt_are_not_merged(tmp_path, monkeypatch):
    """Dedup is scoped to one base character; unrelated rows keep their own face."""
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "finished_page")
    img = _CountingImage()
    proj = asyncio.run(
        creative_comic(
            "第一章\n方鸿渐和鸿渐在村口。",
            output_dir=str(tmp_path),
            chat=_TwoNamesChat(),
            image=img,
        )
    )
    lines = [
        __import__("json").loads(line)
        for line in (tmp_path / "runs.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    portrait_intents = [r for r in lines if r.get("kind") == "intent" and r["stage"] == "portrait"]
    keys = sorted({r["key"] for r in portrait_intents})
    assert keys == ["方鸿渐", "鸿渐"], f"two distinct faces were folded: {keys}"
    portraits = proj.state.generated.portraits
    assert "方鸿渐" in portraits and "鸿渐" in portraits
    assert proj.state.characters["方鸿渐"].portrait_local
    assert proj.state.characters["鸿渐"].portrait_local
