"""tests/test_creative_comic.py — orchestration end-to-end (fakes, no network)."""

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
    assert set(proj.state.panels_done) == {"c0000-p0000", "c0001-p0000"}
    assert {panel.source_panel_id for panel in proj.state.generated.panels.values()} == {
        "ch01_p01",
        "ch02_p01",
    }
    assert "方鸿渐" in proj.state.generated.portraits
    assert Path(proj.state.generated.portraits["方鸿渐"]).exists()
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
    state = ProjectState.load(tmp_path / "state.json")
    deleted = Path(next(iter(state.generated.panels.values())).local)
    assert deleted.exists()
    deleted.unlink()

    chat2, img2 = FakeChat(), FakeImage()
    proj = asyncio.run(creative_comic(src, output_dir=str(tmp_path), chat=chat2, image=img2))

    # Exactly the deleted panel is regenerated; the other is skipped (dedup holds).
    assert img2.calls == 1
    assert deleted.exists()
    assert set(proj.state.panels_done) == {"c0000-p0000", "c0001-p0000"}


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_creative_comic_resume_reuses_chunk_cache(tmp_path):
    src = "第一章\n方鸿渐在甲板上。\n第二章\n方鸿渐在读书。"
    asyncio.run(creative_comic(src, output_dir=str(tmp_path), chat=FakeChat(), image=FakeImage()))

    # Delete one finished panel, then rerun.
    state = ProjectState.load(tmp_path / "state.json")
    deleted = Path(next(iter(state.generated.panels.values())).local)
    assert deleted.exists()
    deleted.unlink()

    chat2, img2 = FakeChat(), FakeImage()
    proj = asyncio.run(creative_comic(src, output_dir=str(tmp_path), chat=chat2, image=img2))

    # The cached storyboard/extraction is reused: the (billable) chat API is NOT
    # re-called, and only the single missing panel is regenerated (portrait reused).
    assert chat2.calls == 0
    assert img2.calls == 1
    assert deleted.exists()
    assert set(proj.state.panels_done) == {"c0000-p0000", "c0001-p0000"}


class FakeChatAlias(FakeChat):
    """Second chunk introduces a variant name of the first character."""

    async def chat_function_call(self, messages, tools, tool_choice, **kw):
        self.calls += 1
        name = tool_choice["function"]["name"]
        if name == "extract_story_elements":
            char = "方鸿渐" if self.calls <= 1 else "鸿渐"
            return {
                "characters": [
                    {"name": char, "l1_prompt": "a person", "portrait_prompt": "portrait"}
                ],
                "settings": [{"name": "甲板", "scene_prompt": "deck"}],
                "style_guide": "manhua",
            }
        if name == "plan_storyboard":
            self.board += 1
            char = "方鸿渐" if self.board <= 1 else "鸿渐"
            return {
                "chapter_id": f"ch{self.board:02d}",
                "panels": [
                    {
                        "panel_id": f"ch{self.board:02d}_p01",
                        "characters_present": [char],
                        "setting_ref": "甲板",
                        "action": "stand",
                        "reference_characters": [char],
                        "size": "1024x1024",
                    }
                ],
            }
        return {}


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_creative_comic_flags_character_alias_for_review(tmp_path):
    src = "第一章\n方鸿渐在甲板上。\n第二章\n鸿渐在读书。"
    proj = asyncio.run(
        creative_comic(src, output_dir=str(tmp_path), chat=FakeChatAlias(), image=FakeImage())
    )

    # Both names stay as separate characters (no auto-merge / no mis-consistency).
    assert "方鸿渐" in proj.state.characters
    assert "鸿渐" in proj.state.characters
    # The variant is surfaced for human review rather than silently forked.
    assert len(proj.state.needs_review) == 1
    sugg = proj.state.needs_review[0]
    assert sugg.new_name == "鸿渐" and sugg.candidate == "方鸿渐"
    assert sugg.suggested is True


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_creative_comic_regenerates_stale_panel(tmp_path):
    src = "第一章\n方鸿渐在甲板上。"
    asyncio.run(creative_comic(src, output_dir=str(tmp_path), chat=FakeChat(), image=FakeImage()))
    state = ProjectState.load(tmp_path / "state.json")
    key = "c0000-p0000"
    assert key in state.panels_done
    from core.comic.identity import force_regen_panels

    force_regen_panels(state, [key])
    state.save(tmp_path / "state.json")

    chat2, img2 = FakeChat(), FakeImage()
    proj = asyncio.run(creative_comic(src, output_dir=str(tmp_path), chat=chat2, image=img2))
    assert img2.calls == 1
    assert key in proj.state.panels_done
    assert key not in proj.state.stale_panels


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_creative_comic_merges_settings_across_chunks(tmp_path):
    src = "第一章\n方鸿渐在甲板上。\n第二章\n方鸿渐在读书。"
    proj = asyncio.run(
        creative_comic(src, output_dir=str(tmp_path), chat=FakeChat(), image=FakeImage())
    )
    assert "甲板" in proj.state.settings
    assert proj.state.settings["甲板"].scene_prompt == "deck"

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


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_changed_source_invalidates_resume_state(tmp_path):
    asyncio.run(
        creative_comic(
            "第一章\n方鸿渐在甲板上。",
            output_dir=str(tmp_path),
            chat=FakeChat(),
            image=FakeImage(),
        )
    )

    chat, image = FakeChat(), FakeImage()
    project = asyncio.run(
        creative_comic(
            "第一章\n方鸿渐改在图书馆读书。",
            output_dir=str(tmp_path),
            chat=chat,
            image=image,
        )
    )

    assert chat.calls > 0
    assert image.calls > 0
    assert project.state.source_fingerprint


class UnsafeIdentifierChat(FakeChat):
    async def chat_function_call(self, messages, tools, tool_choice, **kw):
        name = tool_choice["function"]["name"]
        if name == "extract_story_elements":
            return {
                "characters": [{"name": "../../escaped-portrait", "portrait_prompt": "portrait"}],
                "settings": [],
            }
        return {
            "chapter_id": "unsafe",
            "panels": [
                {
                    "panel_id": "../../escaped-panel",
                    "characters_present": ["../../escaped-portrait"],
                    "reference_characters": ["../../escaped-portrait"],
                    "action": "walk",
                }
            ],
        }


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_model_identifiers_cannot_escape_project_directory(tmp_path):
    escaped_portrait = tmp_path.parent / "escaped-portrait.png"
    escaped_panel = tmp_path.parent / "escaped-panel.png"
    try:
        project = asyncio.run(
            creative_comic(
                "第一章\n测试。",
                output_dir=str(tmp_path),
                chat=UnsafeIdentifierChat(),
                image=FakeImage(),
            )
        )
        project_root = tmp_path.resolve()
        asset_paths = [*project.state.generated.portraits.values()]
        asset_paths.extend(panel.local for panel in project.state.generated.panels.values())
        assert all(Path(path).resolve().is_relative_to(project_root) for path in asset_paths)
        assert not escaped_portrait.exists()
        assert not escaped_panel.exists()
    finally:
        escaped_portrait.unlink(missing_ok=True)
        escaped_panel.unlink(missing_ok=True)


class OrderedDialogueChat(FakeChat):
    async def chat_function_call(self, messages, tools, tool_choice, **kw):
        name = tool_choice["function"]["name"]
        if name == "extract_story_elements":
            return {
                "characters": [{"name": "甲", "portrait_prompt": "portrait"}],
                "settings": [],
                "style_guide": "ink wash",
            }
        return {
            "chapter_id": "chapter",
            "panels": [
                {"panel_id": "z-first", "action": "first", "dialogue": "first dialogue"},
                {"panel_id": "a-second", "action": "second", "dialogue": "second dialogue"},
            ],
        }


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_layout_uses_storyboard_order_and_dialogue(tmp_path, monkeypatch):
    captured = []

    def capture_compose(self, panels, output_dir, *, layout_mode="page"):
        captured.extend(panel.dialogue for panel in panels)
        return []

    monkeypatch.setattr("core.pipelines.creative_comic.LayoutEngine.compose", capture_compose)
    asyncio.run(
        creative_comic(
            "第一章\n测试。",
            output_dir=str(tmp_path),
            chat=OrderedDialogueChat(),
            image=FakeImage(),
        )
    )

    assert captured == ["first dialogue", "second dialogue"]


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_legacy_generated_panel_metadata_is_restored_from_storyboard(tmp_path, monkeypatch):
    asyncio.run(
        creative_comic(
            "第一章\n测试。",
            output_dir=str(tmp_path),
            chat=OrderedDialogueChat(),
            image=FakeImage(),
        )
    )
    state_path = tmp_path / "state.json"
    state = ProjectState.load(state_path)
    for generated in state.generated.panels.values():
        generated.chunk_index = 0
        generated.panel_index = 0
        generated.dialogue = None
    state.save(state_path)

    captured = []

    def capture_compose(self, panels, output_dir, *, layout_mode="page"):
        captured.extend(panel.dialogue for panel in panels)
        return []

    monkeypatch.setattr("core.pipelines.creative_comic.LayoutEngine.compose", capture_compose)
    asyncio.run(
        creative_comic(
            "第一章\n测试。",
            output_dir=str(tmp_path),
            chat=OrderedDialogueChat(),
            image=FakeImage(),
        )
    )

    assert captured == ["first dialogue", "second dialogue"]


class ParallelChat(FakeChat):
    async def chat_function_call(self, messages, tools, tool_choice, **kw):
        name = tool_choice["function"]["name"]
        if name == "extract_story_elements":
            return {
                "characters": [
                    {"name": "甲", "portrait_prompt": "portrait"},
                    {"name": "乙", "portrait_prompt": "portrait"},
                    {"name": "丙", "portrait_prompt": "portrait"},
                ],
                "settings": [],
            }
        return {
            "chapter_id": "parallel",
            "panels": [
                {"panel_id": "p1", "action": "one"},
                {"panel_id": "p2", "action": "two"},
                {"panel_id": "p3", "action": "three"},
            ],
        }


class TrackingImage(FakeImage):
    def __init__(self):
        super().__init__()
        self.active = 0
        self.max_active = 0

    async def generate_single_image(self, prompt, reference_image_paths=None, size=None, **kw):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0.02)
            return FakeImageOutput()
        finally:
            self.active -= 1


class ReusedPanelIdChat(FakeChat):
    def __init__(self, panel_id="panel-1"):
        super().__init__()
        self.panel_id = panel_id

    async def chat_function_call(self, messages, tools, tool_choice, **kw):
        name = tool_choice["function"]["name"]
        if name == "extract_story_elements":
            return {
                "characters": [{"name": "甲", "portrait_prompt": "portrait"}],
                "settings": [],
            }
        self.board += 1
        return {
            "chapter_id": f"chapter-{self.board}",
            "panels": [{"panel_id": self.panel_id, "action": f"scene-{self.board}"}],
        }


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_reused_model_panel_ids_are_isolated_per_chunk(tmp_path):
    project = asyncio.run(
        creative_comic(
            "第一章\n甲散步。\n第二章\n甲回家。",
            output_dir=str(tmp_path),
            chat=ReusedPanelIdChat(),
            image=FakeImage(),
        )
    )

    assert len(project.state.generated.panels) == 2
    assert len(project.state.panels_done) == 2
    assert len({panel.local for panel in project.state.generated.panels.values()}) == 2
    source_ids = {panel.source_panel_id for panel in project.state.generated.panels.values()}
    assert source_ids == {"panel-1"}


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_model_id_cannot_collide_with_internal_panel_key(tmp_path):
    project = asyncio.run(
        creative_comic(
            "第一章\n甲散步。\n第二章\n甲回家。",
            output_dir=str(tmp_path),
            chat=ReusedPanelIdChat(panel_id="c0000-p0000"),
            image=FakeImage(),
        )
    )

    assert set(project.state.generated.panels) == {"c0000-p0000", "c0001-p0000"}
    assert len(project.state.panels_done) == 2


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_independent_image_work_uses_bounded_concurrency(tmp_path, monkeypatch):
    monkeypatch.setenv("INKSTONE_IMAGE_CONCURRENCY", "3")
    monkeypatch.setenv("INKSTONE_PANEL_CONTINUITY", "0")
    image = TrackingImage()
    asyncio.run(
        creative_comic("第一章\n测试。", output_dir=str(tmp_path), chat=ParallelChat(), image=image)
    )

    assert image.max_active == 3
