"""Regression tests for cache identity, trusted paths, and generation dependencies."""

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image

from core.api import ChatProvider, ImageProvider
from core.pipelines.creative_comic import creative_comic
from core.schemas import ProjectState


def _fake_export_pdf(self, page_dir, out="comic.pdf", layout="TwoPageRight", direction="R2L"):
    Path(out).write_bytes(b"%PDF-1.4 fake")
    return out


class Output:
    def save(self, path):
        Image.new("RGB", (20, 20), (120, 120, 120)).save(path)


class TwoPanelChat(ChatProvider):
    model = "chat-model-a"

    def __init__(self):
        self.calls = 0
        self.board = 0

    async def chat_function_call(self, messages, tools, tool_choice, **kwargs):
        self.calls += 1
        if tool_choice["function"]["name"] == "extract_story_elements":
            return {
                "characters": [{"name": "甲", "portrait_prompt": "portrait alpha"}],
                "settings": [],
            }
        self.board += 1
        return {
            "chapter_id": f"chapter-{self.board}",
            "panels": [
                {
                    "panel_id": f"panel-{self.board}",
                    "characters_present": ["甲"],
                    "reference_characters": ["甲"],
                    "action": "walk",
                }
            ],
        }


class CapturingImage(ImageProvider):
    def __init__(self):
        self.calls = 0
        self.references = []

    async def generate_single_image(self, prompt, reference_image_paths=None, size=None, **kwargs):
        self.calls += 1
        self.references.append(list(reference_image_paths or []))
        return Output()


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_equivalent_newlines_reuse_cache(tmp_path):
    source_crlf = "第一章\r\n甲散步。\r\n第二章\r\n甲回家。"
    asyncio.run(
        creative_comic(
            source_crlf,
            output_dir=str(tmp_path),
            chat=TwoPanelChat(),
            image=CapturingImage(),
        )
    )

    chat, image = TwoPanelChat(), CapturingImage()
    asyncio.run(
        creative_comic(
            source_crlf.replace("\r\n", "\n"),
            output_dir=str(tmp_path),
            chat=chat,
            image=image,
        )
    )

    assert chat.calls == 0
    assert image.calls == 0


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_model_identity_change_invalidates_generated_assets(tmp_path):
    source = "第一章\n甲散步。"
    asyncio.run(
        creative_comic(
            source,
            output_dir=str(tmp_path),
            chat=TwoPanelChat(),
            image=CapturingImage(),
        )
    )

    class ChangedModelImage(CapturingImage):
        model = "image-model-b"
        i2i_model = "image-model-b"

    chat, image = TwoPanelChat(), ChangedModelImage()
    asyncio.run(creative_comic(source, output_dir=str(tmp_path), chat=chat, image=image))

    assert chat.calls > 0
    assert image.calls > 0


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_continuity_setting_invalidates_generated_assets(tmp_path, monkeypatch):
    source = "第一章\n甲散步。\n第二章\n甲回家。"
    monkeypatch.setenv("INKSTONE_PANEL_CONTINUITY", "1")
    asyncio.run(
        creative_comic(
            source,
            output_dir=str(tmp_path),
            chat=TwoPanelChat(),
            image=CapturingImage(),
        )
    )

    monkeypatch.setenv("INKSTONE_PANEL_CONTINUITY", "0")
    chat, image = TwoPanelChat(), CapturingImage()
    asyncio.run(creative_comic(source, output_dir=str(tmp_path), chat=chat, image=image))

    assert chat.calls > 0
    assert image.calls > 0


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_default_mode_keeps_previous_panel_reference(tmp_path, monkeypatch):
    monkeypatch.delenv("INKSTONE_PANEL_CONTINUITY", raising=False)
    image = CapturingImage()
    project = asyncio.run(
        creative_comic(
            "第一章\n甲散步。\n第二章\n甲回家。",
            output_dir=str(tmp_path),
            chat=TwoPanelChat(),
            image=image,
        )
    )

    first_panel = project.state.generated.panels["c0000-p0000"].local
    assert first_panel in image.references[-1]


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_resume_continuity_uses_completed_predecessor(tmp_path, monkeypatch):
    monkeypatch.setenv("INKSTONE_PANEL_CONTINUITY", "1")
    source = "第一章\n甲散步。\n第二章\n甲回家。"
    project = asyncio.run(
        creative_comic(
            source,
            output_dir=str(tmp_path),
            chat=TwoPanelChat(),
            image=CapturingImage(),
        )
    )
    Path(project.state.generated.panels["c0001-p0000"].local).unlink()

    image = CapturingImage()
    asyncio.run(
        creative_comic(source, output_dir=str(tmp_path), chat=TwoPanelChat(), image=image)
    )

    expected_refs = [
        project.state.generated.portraits["甲"],
        project.state.generated.panels["c0000-p0000"].local,
    ]
    assert image.references == [expected_refs]


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_external_portrait_in_state_is_not_sent_to_provider(tmp_path):
    source = "第一章\n甲散步。"
    project = asyncio.run(
        creative_comic(
            source,
            output_dir=str(tmp_path),
            chat=TwoPanelChat(),
            image=CapturingImage(),
        )
    )
    external = tmp_path.parent / "external-secret.png"
    Image.new("RGB", (20, 20), (1, 2, 3)).save(external)
    try:
        state_path = tmp_path / "state.json"
        state = ProjectState.load(state_path)
        state.characters["甲"].portrait_local = str(external)
        state.generated.portraits["甲"] = str(external)
        state.save(state_path)
        Path(project.state.generated.panels["c0000-p0000"].local).unlink()

        image = CapturingImage()
        asyncio.run(
            creative_comic(source, output_dir=str(tmp_path), chat=TwoPanelChat(), image=image)
        )

        restored = ProjectState.load(state_path)
        assert all(str(external) not in refs for refs in image.references)
        assert restored.characters["甲"].portrait_local != str(external)
    finally:
        external.unlink(missing_ok=True)


class OneRejectedPortraitChat(ChatProvider):
    async def chat_function_call(self, messages, tools, tool_choice, **kwargs):
        if tool_choice["function"]["name"] == "extract_story_elements":
            return {
                "characters": [
                    {"name": "good", "portrait_prompt": "portrait good"},
                    {"name": "blocked", "portrait_prompt": "portrait blocked"},
                ],
                "settings": [],
            }
        return {"chapter_id": "one", "panels": []}


class OneRejectedPortraitImage(CapturingImage):
    async def generate_single_image(self, prompt, reference_image_paths=None, size=None, **kwargs):
        if "portrait blocked" in prompt:
            raise RuntimeError("content_policy_violation")
        return await super().generate_single_image(prompt, reference_image_paths, size, **kwargs)


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_successful_portraits_survive_sibling_policy_rejection(tmp_path):
    project = asyncio.run(
        creative_comic(
            "第一章\n测试。",
            output_dir=str(tmp_path),
            chat=OneRejectedPortraitChat(),
            image=OneRejectedPortraitImage(),
        )
    )

    portrait = project.state.characters["good"].portrait_local
    assert portrait is not None
    assert Path(portrait).exists()


class OperationalPortraitImage(CapturingImage):
    async def generate_single_image(self, prompt, reference_image_paths=None, size=None, **kwargs):
        if "portrait blocked" in prompt:
            raise RuntimeError("provider unavailable")
        return await super().generate_single_image(prompt, reference_image_paths, size, **kwargs)


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_successful_portrait_is_checkpointed_before_sibling_operational_failure(tmp_path):
    with pytest.raises(RuntimeError, match="provider unavailable"):
        asyncio.run(
            creative_comic(
                "第一章\n测试。",
                output_dir=str(tmp_path),
                chat=OneRejectedPortraitChat(),
                image=OperationalPortraitImage(),
            )
        )

    state = ProjectState.load(tmp_path / "state.json")
    portrait = state.characters["good"].portrait_local
    assert portrait is not None
    assert Path(portrait).is_file()


class OperationalPanelChat(ChatProvider):
    async def chat_function_call(self, messages, tools, tool_choice, **kwargs):
        if tool_choice["function"]["name"] == "extract_story_elements":
            return {"characters": [], "settings": []}
        return {
            "chapter_id": "one",
            "panels": [
                {"panel_id": "failed", "action": "provider failure"},
                {"panel_id": "good", "action": "successful panel"},
            ],
        }


class OperationalPanelImage(CapturingImage):
    async def generate_single_image(self, prompt, reference_image_paths=None, size=None, **kwargs):
        if "provider failure" in prompt:
            raise RuntimeError("provider unavailable")
        return await super().generate_single_image(prompt, reference_image_paths, size, **kwargs)


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_successful_panel_is_checkpointed_before_sibling_operational_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("INKSTONE_PANEL_CONTINUITY", "0")
    with pytest.raises(RuntimeError, match="provider unavailable"):
        asyncio.run(
            creative_comic(
                "第一章\n测试。",
                output_dir=str(tmp_path),
                chat=OperationalPanelChat(),
                image=OperationalPanelImage(),
            )
        )

    state = ProjectState.load(tmp_path / "state.json")
    generated = state.generated.panels["c0000-p0001"]
    assert Path(generated.local).is_file()
