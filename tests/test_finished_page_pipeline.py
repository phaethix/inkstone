"""tests/test_finished_page_pipeline.py — finished-page orchestration (fakes, no network)."""

import asyncio
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from core.api import ChatProvider, ImageProvider
from core.pipelines.creative_comic import (
    _render_fingerprint,
    creative_comic,
    is_unsupported_image_size_error,
)
from core.schemas import ModelSnapshot, ProjectState
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


class RecordingImage(FakeImage):
    def __init__(self):
        super().__init__()
        self.prompts: list[str] = []

    async def generate_single_image(self, prompt, reference_image_paths=None, size=None, **kw):
        if "Finished readable manga/comic page" in prompt:
            self.prompts.append(prompt)
        return await super().generate_single_image(prompt, reference_image_paths, size, **kw)


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
            return {"beats": []}
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


def test_render_fingerprint_includes_visual_bible_hash():
    snapshot = ModelSnapshot(chat="chat", t2i="image", i2i="image")
    fp = _render_fingerprint(
        "style",
        snapshot=snapshot,
        panel_continuity=False,
        l3_enabled=False,
        render_mode="finished_page",
        page_size="1024x1536",
        bible_version="bible_v1",
        bible_hash="deadbeef",
    )
    payload = {
        "style_guide": "style",
        "model_snapshot": snapshot.model_dump(),
        "panel_continuity": False,
        "l3_enabled": False,
        "render_mode": "finished_page",
        "page_size": "1024x1536",
        "identity": "metaphor_v2",
        "stage_lock": "v1",
        "layout": "anti_template_v1",
        "voice_timeline": "v1",
        "beats": "v1",
        "lettering": "deferred_v3",
        "visual_bible": "bible_v1",
        "bible_hash": "deadbeef",
    }
    expected = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert fp == expected


def test_render_fingerprint_uses_bible_v2_token():
    snapshot = ModelSnapshot(chat="chat", t2i="image", i2i="image")
    fp = _render_fingerprint(
        "style",
        snapshot=snapshot,
        panel_continuity=False,
        l3_enabled=False,
        render_mode="finished_page",
        page_size="1024x1536",
        bible_version="bible_v2",
        bible_hash="abc",
    )
    payload = {
        "style_guide": "style",
        "model_snapshot": snapshot.model_dump(),
        "panel_continuity": False,
        "l3_enabled": False,
        "render_mode": "finished_page",
        "page_size": "1024x1536",
        "identity": "metaphor_v2",
        "stage_lock": "v1",
        "layout": "anti_template_v1",
        "voice_timeline": "v1",
        "beats": "v1",
        "lettering": "deferred_v3",
        "visual_bible": "bible_v2",
        "bible_hash": "abc",
    }
    expected = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert fp == expected


def test_render_fingerprint_uses_bible_v3_token():
    snapshot = ModelSnapshot(chat="chat", t2i="image", i2i="image")
    fp = _render_fingerprint(
        "style",
        snapshot=snapshot,
        panel_continuity=False,
        l3_enabled=False,
        render_mode="finished_page",
        page_size="1024x1536",
        bible_version="bible_v3",
        bible_hash="abc",
    )
    payload = {
        "style_guide": "style",
        "model_snapshot": snapshot.model_dump(),
        "panel_continuity": False,
        "l3_enabled": False,
        "render_mode": "finished_page",
        "page_size": "1024x1536",
        "identity": "metaphor_v2",
        "stage_lock": "v1",
        "layout": "anti_template_v1",
        "voice_timeline": "v1",
        "beats": "v1",
        "lettering": "deferred_v3",
        "visual_bible": "bible_v3",
        "bible_hash": "abc",
    }
    expected = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert fp == expected


def test_render_fingerprint_tracks_deferred_lettering_version():
    snapshot = ModelSnapshot(chat="chat", t2i="image", i2i="image")
    expected_payload = json.dumps(
        {
            "style_guide": "manhua",
            "model_snapshot": snapshot.model_dump(),
            "panel_continuity": False,
            "l3_enabled": False,
            "render_mode": "finished_page",
            "page_size": "1024x1536",
            "lettering": "deferred_v3",
            "identity": "metaphor_v2",
            "stage_lock": "v1",
            "layout": "anti_template_v1",
            "voice_timeline": "v1",
            "beats": "v1",
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )

    assert (
        _render_fingerprint(
            "manhua",
            snapshot=snapshot,
            panel_continuity=False,
            l3_enabled=False,
        )
        == hashlib.sha256(expected_payload.encode("utf-8")).hexdigest()
    )


def test_render_fingerprint_omits_lettering_for_panel_compose():
    snapshot = ModelSnapshot(chat="chat", t2i="image", i2i="image")
    expected_payload = json.dumps(
        {
            "style_guide": "manhua",
            "model_snapshot": snapshot.model_dump(),
            "panel_continuity": False,
            "l3_enabled": False,
            "render_mode": "panel_compose",
            "page_size": "1024x1536",
            "identity": "metaphor_v2",
            "stage_lock": "v1",
            "layout": "anti_template_v1",
            "voice_timeline": "v1",
            "beats": "v1",
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    assert (
        _render_fingerprint(
            "manhua",
            snapshot=snapshot,
            panel_continuity=False,
            l3_enabled=False,
            render_mode="panel_compose",
        )
        == hashlib.sha256(expected_payload.encode("utf-8")).hexdigest()
    )
    assert '"lettering"' not in expected_payload


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_finished_page_mode_writes_generated_pages(tmp_path, monkeypatch):
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "finished_page")
    src = "第一章\n福贵在村口。"
    chat, img = FakeChat(), FakeImage()
    proj = asyncio.run(creative_comic(src, output_dir=str(tmp_path), chat=chat, image=img))

    page_files = sorted((tmp_path / "pages").glob("page_*.png"))
    assert page_files

    assert proj.state.render_mode == "finished_page"
    assert "c0000:u1_p0001" in proj.state.generated.pages
    assert "c0000:u1_p0001" in proj.state.pages_done
    generated_page = proj.state.generated.pages["c0000:u1_p0001"]
    assert generated_page.mode == "finished_lettered"
    assert generated_page.blank_local
    assert Path(generated_page.blank_local).exists()
    assert Path(generated_page.local).exists()

    assert proj.pdf and Path(proj.pdf).exists()
    assert proj.pages == [str(p) for p in page_files]

    assert img.calls == 2  # 1 portrait + 1 page
    assert chat.calls == 4  # extract + reconcile + extract_key_beats + plan_comic_pages

    # Resume: state.json already has the page recorded, so nothing regenerates.
    chat2, img2 = FakeChat(), FakeImage()
    proj2 = asyncio.run(creative_comic(src, output_dir=str(tmp_path), chat=chat2, image=img2))
    assert img2.calls == 0
    assert chat2.calls == 0
    assert proj2.state.pages_done == proj.state.pages_done


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_finished_page_writes_blank_and_lettered(tmp_path, monkeypatch):
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "finished_page")
    img = RecordingImage()
    proj = asyncio.run(
        creative_comic("第一章\n福贵在村口。", output_dir=str(tmp_path), chat=FakeChat(), image=img)
    )

    assert any(
        "no readable" in p.lower()
        or "no speech bubbles" in p.lower()
        or "empty speech" in p.lower()
        for p in img.prompts
    )
    assert all("DIALOGUE (exact):" not in p for p in img.prompts)
    page_key = next(iter(proj.state.generated.pages))
    generated_page = proj.state.generated.pages[page_key]
    assert generated_page.mode == "finished_lettered"
    assert generated_page.blank_local and Path(generated_page.blank_local).exists()
    assert Path(generated_page.local).exists()
    assert Path(generated_page.blank_local).read_bytes() != Path(generated_page.local).read_bytes()


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_finished_page_reletters_from_blank_without_new_image(tmp_path, monkeypatch):
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "finished_page")
    out = str(tmp_path)
    asyncio.run(
        creative_comic(
            "第一章\n福贵在村口。", output_dir=out, chat=FakeChat(), image=RecordingImage()
        )
    )

    state = ProjectState.load(tmp_path / "state.json")
    key = next(iter(state.generated.pages))
    generated_page = state.generated.pages[key]
    Path(generated_page.local).unlink()
    state.pages_done = [done_key for done_key in state.pages_done if done_key != key]
    generated_page.local = str(tmp_path / "pages" / "missing.png")
    state.save(tmp_path / "state.json")

    img2 = RecordingImage()
    proj2 = asyncio.run(
        creative_comic("第一章\n福贵在村口。", output_dir=out, chat=FakeChat(), image=img2)
    )

    assert img2.prompts == []
    assert Path(proj2.state.generated.pages[key].local).exists()
    assert proj2.state.generated.pages[key].mode == "finished_lettered"


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

    assert img2.calls == 0  # missing lettered page is rebuilt from the retained blank
    assert chat2.calls == 0  # page plan reused from page_cache
    assert deleted.exists()
    assert "c0000:u1_p0001" in proj.state.pages_done


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
    first_page = state.generated.pages["c0000:u1_p0001"]
    second_page = state.generated.pages["c0001:u2_p0001"]
    second_path = Path(second_page.local)
    second_bytes_before = second_path.read_bytes()

    Path(first_page.local).unlink()

    chat2, img2 = FakeChat(), FakeImage()
    proj = asyncio.run(creative_comic(src, output_dir=str(tmp_path), chat=chat2, image=img2))

    assert img2.calls == 0  # deleted lettered page is rebuilt from its position-stable blank
    assert Path(first_page.local).exists()  # regenerated at the *same* path
    assert second_path.exists()
    assert second_path.read_bytes() == second_bytes_before  # untouched, not overwritten
    assert set(proj.state.pages_done) == {"c0000:u1_p0001", "c0001:u2_p0001"}


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
    assert "c0000:u1_p0001" in proj.state.skipped_pages
    assert "c0000:u1_p0001" not in proj.state.pages_done
    assert "c0000:u1_p0001" not in proj.state.generated.pages


def test_is_content_policy_rejection_still_used_by_finished_page_path():
    assert is_content_policy_rejection(RuntimeError("content_policy_violation")) is True


def test_is_unsupported_image_size_error_detects_size_rejects():
    assert is_unsupported_image_size_error(RuntimeError("invalid size: 1024x1536 is not supported"))
    assert is_unsupported_image_size_error(
        RuntimeError("Bad Request: unsupported resolution for this model")
    )
    assert not is_unsupported_image_size_error(RuntimeError("rate limit exceeded"))
    assert not is_unsupported_image_size_error(
        RuntimeError("content_policy_violation: size mention alone is not enough")
    )


class SizeRejectThenOkImage(FakeImage):
    """Portrait size rejected once; square fallback succeeds."""

    def __init__(self):
        super().__init__()
        self.page_sizes: list[str | None] = []

    async def generate_single_image(self, prompt, reference_image_paths=None, size=None, **kw):
        self.calls += 1
        if "Finished readable manga/comic page" in prompt:
            self.page_sizes.append(size)
            if size == "1024x1536":
                raise RuntimeError("Agnes image error: invalid size 1024x1536 is not supported")
        return FakeImageOutput()


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_finished_page_falls_back_to_square_when_size_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "finished_page")
    monkeypatch.delenv("INKSTONE_PAGE_SIZE", raising=False)
    src = "第一章\n福贵在村口。"
    img = SizeRejectThenOkImage()
    proj = asyncio.run(creative_comic(src, output_dir=str(tmp_path), chat=FakeChat(), image=img))

    assert img.page_sizes == ["1024x1536", "1024x1024"]
    assert "c0000:u1_p0001" in proj.state.pages_done
    assert Path(proj.state.generated.pages["c0000:u1_p0001"].local).exists()


class FailOncePageImage(FakeImage):
    """First finished-page image call fails generically; second succeeds."""

    def __init__(self):
        super().__init__()
        self.page_attempts = 0
        self.prompts: list[str] = []

    async def generate_single_image(self, prompt, reference_image_paths=None, size=None, **kw):
        self.calls += 1
        if "Finished readable manga/comic page" in prompt:
            self.page_attempts += 1
            self.prompts.append(prompt)
            if self.page_attempts == 1:
                raise RuntimeError("transient image provider failure")
        return FakeImageOutput()


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_finished_page_generic_failure_retries_once_with_stricter_prompt(tmp_path, monkeypatch):
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "finished_page")
    src = "第一章\n福贵在村口。"
    img = FailOncePageImage()
    proj = asyncio.run(creative_comic(src, output_dir=str(tmp_path), chat=FakeChat(), image=img))

    assert img.page_attempts == 2
    assert len(img.prompts) == 2
    assert "STRICT" in img.prompts[1]
    assert "STRICT" not in img.prompts[0]

    assert "c0000:u1_p0001" in proj.state.pages_done
    assert "c0000:u1_p0001" not in proj.state.skipped_pages
    assert Path(proj.state.generated.pages["c0000:u1_p0001"].local).exists()


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


class CollidingPageIdChat(FakeChat):
    """Both chunks plan the same page_id — state keys must be chunk-namespaced."""

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
                        "page_id": "p0001",
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


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_finished_page_cross_chunk_page_id_collision(tmp_path, monkeypatch):
    """Two chunks with the same page_id must both generate distinct pages."""
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "finished_page")
    src = "第一章\n福贵在村口。\n第二章\n福贵在读书。"
    img = FakeImage()
    proj = asyncio.run(
        creative_comic(src, output_dir=str(tmp_path), chat=CollidingPageIdChat(), image=img)
    )

    page_files = sorted((tmp_path / "pages").glob("page_c*_p*.png"))
    assert len(page_files) == 2
    assert img.calls == 3  # 1 portrait + 2 pages (same page_id, different chunks)

    assert set(proj.state.generated.pages) == {"c0000:p0001", "c0001:p0001"}
    assert set(proj.state.pages_done) == {"c0000:p0001", "c0001:p0001"}
    assert len(proj.pages) == 2
    assert proj.pdf and Path(proj.pdf).exists()


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_finished_page_webtoon_stacks_page_images(tmp_path, monkeypatch):
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "finished_page")
    src = "第一章\n福贵在村口。\n第二章\n福贵在读书。"
    proj = asyncio.run(
        creative_comic(
            src,
            output_dir=str(tmp_path),
            chat=FakeChat(),
            image=FakeImage(),
            output_format="webtoon",
        )
    )

    assert proj.pdf is None
    assert proj.webtoon and Path(proj.webtoon).exists()
    assert proj.webtoon.endswith("webtoon.png")
    assert proj.pages == [proj.webtoon]
    assert len(list((tmp_path / "pages").glob("page_c*_p*.png"))) == 2


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_legacy_completed_project_bootstraps_visual_bible(tmp_path, monkeypatch):
    """Completed chunks without a bible must still reconcile and soft-invalidate render."""
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "finished_page")
    src = "第一章\n福贵在村口。"
    chat, img = FakeChat(), FakeImage()
    proj = asyncio.run(creative_comic(src, output_dir=str(tmp_path), chat=chat, image=img))
    assert proj.state.visual_bible is not None
    assert proj.state.pages_done

    state = ProjectState.load(tmp_path / "state.json")
    state.visual_bible = None
    state.save(tmp_path / "state.json")

    chat2, img2 = FakeChat(), FakeImage()
    proj2 = asyncio.run(creative_comic(src, output_dir=str(tmp_path), chat=chat2, image=img2))
    assert proj2.state.visual_bible is not None
    assert chat2.calls >= 1  # reconcile on resume
    assert img2.calls >= 1  # pages re-rendered after first bible bootstrap


class RecordingRefsImage(FakeImage):
    def __init__(self):
        super().__init__()
        self.page_refs: list[list[str]] = []
        self.portrait_refs: list[list[str]] = []

    async def generate_single_image(self, prompt, reference_image_paths=None, size=None, **kw):
        refs = list(reference_image_paths or [])
        if "Finished readable manga/comic page" in prompt:
            self.page_refs.append(refs)
        else:
            self.portrait_refs.append(refs)
        return await super().generate_single_image(prompt, reference_image_paths, size, **kw)


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_finished_pages_pass_portrait_and_cross_chunk_blank_refs(tmp_path, monkeypatch):
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "finished_page")
    src = "第一章\n福贵在村口。\n第二章\n福贵在读书。"
    image = RecordingRefsImage()
    asyncio.run(creative_comic(src, output_dir=str(tmp_path), chat=FakeChat(), image=image))
    assert len(image.page_refs) == 2
    assert image.page_refs[0], "first page should i2i from the character portrait"
    assert image.page_refs[1], "second chunk should keep portrait refs"
    first_refs = set(image.page_refs[0])
    second_refs = set(image.page_refs[1])
    assert second_refs - first_refs, "second chunk first page should add previous blank"
