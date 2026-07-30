"""tests/test_finished_page_pipeline.py — finished-page orchestration (fakes, no network)."""

import asyncio
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from core.api import ChatProvider, ImageProvider
from core.pipelines.creative_comic import creative_comic
from core.schemas import ProjectState
from core.screenwriter import is_content_policy_rejection


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

    async def generate_single_image(self, prompt, reference_image_paths=None, size=None, **kw):
        self.calls += 1
        return FakeImageOutput()


class FakeChat(ChatProvider):
    def __init__(self):
        self.calls = 0
        self.page_plan_calls = 0

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
        if name == "plan_comic_pages":
            self.page_plan_calls += 1
            return {
                "unit_id": str(self.page_plan_calls),
                "pages": [
                    {
                        "page_id": f"u{self.page_plan_calls}_p0001",
                        "purpose": "establish the village entrance",
                        "layout_intent": "wide establishing top, inset reaction bottom-right",
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


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_finished_page_mode_writes_generated_pages(tmp_path, monkeypatch):
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "finished_page")
    src = "第一章\n福贵在村口。"
    chat, img = FakeChat(), FakeImage()
    proj = asyncio.run(creative_comic(src, output_dir=str(tmp_path), chat=chat, image=img))

    page_files = sorted((tmp_path / "pages").glob("page_*.png"))
    assert page_files

    assert proj.state.render_mode == "finished_page"
    assert "u1_p0001" in proj.state.generated.pages
    assert "u1_p0001" in proj.state.pages_done
    generated_page = proj.state.generated.pages["u1_p0001"]
    assert generated_page.mode == "finished"
    assert Path(generated_page.local).exists()

    assert proj.pdf and Path(proj.pdf).exists()
    assert proj.pages == [str(p) for p in page_files]

    assert img.calls == 2  # 1 portrait + 1 page
    assert chat.calls == 2  # extract + plan_comic_pages

    # Resume: state.json already has the page recorded, so nothing regenerates.
    chat2, img2 = FakeChat(), FakeImage()
    proj2 = asyncio.run(creative_comic(src, output_dir=str(tmp_path), chat=chat2, image=img2))
    assert img2.calls == 0
    assert chat2.calls == 0
    assert proj2.state.pages_done == proj.state.pages_done


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_finished_page_kwarg_overrides_env_default(tmp_path, monkeypatch):
    # Env says panel_compose; explicit kwarg should win.
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "panel_compose")
    src = "第一章\n福贵在村口。"
    chat, img = FakeChat(), FakeImage()
    proj = asyncio.run(
        creative_comic(
            src, output_dir=str(tmp_path), chat=chat, image=img, render_mode="finished_page"
        )
    )
    assert proj.state.render_mode == "finished_page"
    assert sorted((tmp_path / "pages").glob("page_*.png"))


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_finished_page_resumes_after_deleted_page(tmp_path, monkeypatch):
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "finished_page")
    src = "第一章\n福贵在村口。"
    asyncio.run(creative_comic(src, output_dir=str(tmp_path), chat=FakeChat(), image=FakeImage()))

    state = ProjectState.load(tmp_path / "state.json")
    deleted = Path(next(iter(state.generated.pages.values())).local)
    assert deleted.exists()
    deleted.unlink()

    chat2, img2 = FakeChat(), FakeImage()
    proj = asyncio.run(creative_comic(src, output_dir=str(tmp_path), chat=chat2, image=img2))

    assert img2.calls == 1  # only the missing page regenerates; portrait reused
    assert chat2.calls == 0  # page plan reused from page_cache
    assert deleted.exists()
    assert "u1_p0001" in proj.state.pages_done


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_finished_page_filenames_are_position_stable_across_partial_resume(tmp_path, monkeypatch):
    """Regenerating one deleted page must not collide with / corrupt another page's file.

    Position-derived filenames (not a running counter) guarantee this: a
    counter would renumber on resume and could overwrite an unrelated page.
    """
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "finished_page")
    src = "第一章\n福贵在村口。\n第二章\n福贵在读书。"
    asyncio.run(creative_comic(src, output_dir=str(tmp_path), chat=FakeChat(), image=FakeImage()))

    state = ProjectState.load(tmp_path / "state.json")
    assert len(state.generated.pages) == 2
    first_page = state.generated.pages["u1_p0001"]
    second_page = state.generated.pages["u2_p0001"]
    second_path = Path(second_page.local)
    second_bytes_before = second_path.read_bytes()

    Path(first_page.local).unlink()

    chat2, img2 = FakeChat(), FakeImage()
    proj = asyncio.run(creative_comic(src, output_dir=str(tmp_path), chat=chat2, image=img2))

    assert img2.calls == 1  # only the deleted page regenerates
    assert Path(first_page.local).exists()  # regenerated at the *same* path
    assert second_path.exists()
    assert second_path.read_bytes() == second_bytes_before  # untouched, not overwritten
    assert set(proj.state.pages_done) == {"u1_p0001", "u2_p0001"}


class RejectingPageImage(FakeImage):
    """The finished-page image is rejected; the portrait call still succeeds."""

    async def generate_single_image(self, prompt, reference_image_paths=None, size=None, **kw):
        self.calls += 1
        if "Finished readable manga/comic page" in prompt:
            raise RuntimeError("Agnes image error: content_policy_violation: page rejected")
        return FakeImageOutput()


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_finished_page_content_policy_rejection_is_skipped_not_raised(tmp_path, monkeypatch):
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "finished_page")
    src = "第一章\n福贵在村口。"
    proj = asyncio.run(
        creative_comic(src, output_dir=str(tmp_path), chat=FakeChat(), image=RejectingPageImage())
    )
    assert "u1_p0001" in proj.state.skipped_pages
    assert "u1_p0001" not in proj.state.pages_done
    assert "u1_p0001" not in proj.state.generated.pages


def test_is_content_policy_rejection_still_used_by_finished_page_path():
    assert is_content_policy_rejection(RuntimeError("content_policy_violation")) is True


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_finished_page_export_ignores_stale_panel_compose_pages(tmp_path, monkeypatch):
    """panel_compose writes page_01.png; finished_page must not pick those up on export."""
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "finished_page")
    pages_dir = tmp_path / "pages"
    pages_dir.mkdir(parents=True)
    Image.new("RGB", (20, 30), (255, 0, 0)).save(pages_dir / "page_01.png")

    src = "第一章\n福贵在村口。"
    proj = asyncio.run(
        creative_comic(src, output_dir=str(tmp_path), chat=FakeChat(), image=FakeImage())
    )

    exported = [Path(p).name for p in proj.pages]
    assert exported
    assert "page_01.png" not in exported
    assert all(name.startswith("page_c") and "_p" in name for name in exported)
    assert not (pages_dir / "page_01.png").exists()
