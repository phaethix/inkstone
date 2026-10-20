"""tests/test_finished_page_pipeline.py — finished-page orchestration (fakes, no network)."""

import asyncio
import hashlib
import json
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image

from core.api import ChatProvider, ImageProvider
from core.comic.budget import BudgetSpec
from core.comic.cas import beats_action_key
from core.comic.segmentation import segment_text
from core.comic.structure_report import structural_report
from core.density import PANELS_PER_CHUNK_B
from core.harness.spec import DATA_DIR, harness_from_dir
from core.pipelines import creative_comic as pipeline
from core.pipelines.creative_comic import (
    _BEATS_STAGE_SRC,
    _page_plan_key,
    _render_fingerprint,
    creative_comic,
    is_unsupported_image_size_error,
)
from core.schemas import ModelSnapshot, ProjectState, StoryElements
from core.screenwriter import (
    build_beats_request,
    build_page_plan_request,
    is_content_policy_rejection,
)


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
    assert "c0000-p0000" in proj.state.generated.pages
    assert "c0000-p0000" in proj.state.pages_done
    generated_page = proj.state.generated.pages["c0000-p0000"]
    assert generated_page.mode == "finished_lettered"
    assert generated_page.resolved_size == "1024x1536"
    assert generated_page.blank_local
    assert Path(generated_page.blank_local).exists()
    assert Path(generated_page.local).exists()

    assert proj.pdf and Path(proj.pdf).exists()
    assert proj.pages == [str(p) for p in page_files]

    assert img.calls == 2  # 1 portrait + 1 page
    assert chat.calls == 4  # extract + reconcile + extract_key_beats + plan_comic_pages

    # Two-part state: the checkpoint holds progress; the heavy plan caches the
    # resumed run needs (chunk/page/beat cache) live in the sibling cache.json.
    raw_state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    for name in (
        "chunk_cache",
        "page_cache",
        "beat_cache",
        "visual_bible",
        "source_units",
        "adaptation_plans",
    ):
        assert name not in raw_state
    raw_cache = json.loads((tmp_path / "cache.json").read_text(encoding="utf-8"))
    assert set(raw_cache) == {
        "chunk_cache",
        "page_cache",
        "beat_cache",
        "visual_bible",
        "source_units",
        "adaptation_plans",
    }

    # Resume: the page is recorded in state.json and the plan caches round-trip
    # through cache.json, so nothing regenerates.
    chat2, img2 = FakeChat(), FakeImage()
    proj2 = asyncio.run(creative_comic(src, output_dir=str(tmp_path), chat=chat2, image=img2))
    assert img2.calls == 0
    assert chat2.calls == 0
    assert proj2.state.pages_done == proj.state.pages_done
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
        ("render", "c0000-p0000"),
    ]
    assert all(row["size"] for row in intents if row["stage"] in {"portrait", "render"})
    assert all(row["prompt_hash"] for row in intents)
    logged = (tmp_path / "runs.jsonl").read_text(encoding="utf-8")
    assert "portrait of a farmer" not in logged
    assert "Finished readable" not in logged
    assert "You are a comic scriptwriter" not in logged


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_finished_page_writes_blank_and_lettered(tmp_path, monkeypatch):
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "finished_page")
    img = RecordingImage()
    proj = asyncio.run(
        creative_comic(
            "第一章\n福贵在村口。傍晚，村口。",
            output_dir=str(tmp_path),
            chat=FakeChat(),
            image=img,
        )
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
def test_a_changed_font_reletters_the_stored_blank(tmp_path, monkeypatch):
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "finished_page")
    out = str(tmp_path)
    env = {"value": "font-a"}
    monkeypatch.setattr(pipeline, "h_env", lambda **_kwargs: env["value"])
    calls = {"n": 0}
    real = pipeline.letter_finished_page

    def counting(image, page_plan, **kwargs):
        calls["n"] += 1
        return real(image, page_plan, **kwargs)

    monkeypatch.setattr(pipeline, "letter_finished_page", counting)
    asyncio.run(
        creative_comic(
            "第一章\n福贵在村口。", output_dir=out, chat=FakeChat(), image=RecordingImage()
        )
    )
    state = ProjectState.load(tmp_path / "state.json")
    key = next(iter(state.generated.pages))
    blank = Path(state.generated.pages[key].blank_local)
    blank_bytes = blank.read_bytes()
    assert calls["n"] == 1

    asyncio.run(
        creative_comic(
            "第一章\n福贵在村口。", output_dir=out, chat=FakeChat(), image=RecordingImage()
        )
    )
    assert calls["n"] == 1

    env["value"] = "font-b"
    img2 = RecordingImage()
    proj = asyncio.run(
        creative_comic("第一章\n福贵在村口。", output_dir=out, chat=FakeChat(), image=img2)
    )

    assert calls["n"] == 2
    assert img2.prompts == []
    assert blank.read_bytes() == blank_bytes
    assert Path(proj.state.generated.pages[key].local).is_file()
    letter_manifests = [
        path
        for path in (tmp_path / "index").glob("*.json")
        if '"stage": "letter"' in path.read_text(encoding="utf-8")
    ]
    assert len(letter_manifests) == 2


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_finished_page_stale_page_repaints_instead_of_reusing_blank(tmp_path, monkeypatch):
    """A stale page is invalidated for a content reason (e.g. alias merge).

    Its cached blank art predates the identity change, so the page must be sent
    to the image provider again — re-lettering the old blank would silently keep
    the wrong face. Regression: the blank-reuse shortcut used to swallow stale
    pages, making the advertised "merge -> selective redraw" a no-op.
    """
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "finished_page")
    src = "第一章\n福贵在村口。"
    asyncio.run(creative_comic(src, output_dir=str(tmp_path), chat=FakeChat(), image=FakeImage()))

    state = ProjectState.load(tmp_path / "state.json")
    key = next(iter(state.generated.pages))
    blank = Path(state.generated.pages[key].blank_local)
    assert blank.exists()

    # Simulate `merge_character_alias`: the page is stale but its blank still exists.
    state.stale_pages = [key]
    state.pages_done = [k for k in state.pages_done if k != key]
    state.save(tmp_path / "state.json")

    img2 = FakeImage()
    proj = asyncio.run(creative_comic(src, output_dir=str(tmp_path), chat=FakeChat(), image=img2))

    assert img2.calls == 1  # art repainted, not just re-lettered
    assert key not in proj.state.stale_pages  # stale flag cleared after redraw
    assert key in proj.state.pages_done


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
    assert "c0000-p0000" in proj.state.pages_done


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
    first_page = state.generated.pages["c0000-p0000"]
    second_page = state.generated.pages["c0001-p0000"]
    second_path = Path(second_page.local)
    second_bytes_before = second_path.read_bytes()

    Path(first_page.local).unlink()

    chat2, img2 = FakeChat(), FakeImage()
    proj = asyncio.run(creative_comic(src, output_dir=str(tmp_path), chat=chat2, image=img2))

    assert img2.calls == 0  # deleted lettered page is rebuilt from its position-stable blank
    assert Path(first_page.local).exists()  # regenerated at the *same* path
    assert second_path.exists()
    assert second_path.read_bytes() == second_bytes_before  # untouched, not overwritten
    assert set(proj.state.pages_done) == {"c0000-p0000", "c0001-p0000"}


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
    assert "c0000-p0000" in proj.state.skipped_pages
    assert "c0000-p0000" not in proj.state.pages_done
    assert "c0000-p0000" not in proj.state.generated.pages
    logged = (tmp_path / "runs.jsonl").read_text(encoding="utf-8")
    rows = [json.loads(line) for line in logged.splitlines()]
    assert rows[-1]["kind"] == "failure"
    assert rows[-1]["error"] == "RuntimeError"
    assert rows[-1]["stage"] == "render"
    assert rows[-1]["key"] == "c0000-p0000"
    assert any(row["stage"] == "extract" and row["kind"] == "intent" for row in rows)
    assert "page rejected" not in logged

    before = len(rows)
    asyncio.run(
        creative_comic(src, output_dir=str(tmp_path), chat=FakeChat(), image=RejectingPageImage())
    )
    after = [
        json.loads(line)
        for line in (tmp_path / "runs.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(after) == before


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
    assert "c0000-p0000" in proj.state.pages_done
    assert proj.state.generated.pages["c0000-p0000"].resolved_size == "1024x1024"
    assert Path(proj.state.generated.pages["c0000-p0000"].local).exists()


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

    assert "c0000-p0000" in proj.state.pages_done
    assert "c0000-p0000" not in proj.state.skipped_pages
    assert Path(proj.state.generated.pages["c0000-p0000"].local).exists()


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

    assert set(proj.state.generated.pages) == {"c0000-p0000", "c0001-p0000"}
    assert set(proj.state.pages_done) == {"c0000-p0000", "c0001-p0000"}
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

    # A legacy project predates the manifest backing: no bible body is recoverable
    # from a manifest. Drop the CAS index and content objects so the resume has
    # nothing to restore from and must call the provider (as a true legacy would).
    shutil.rmtree(tmp_path / "index", ignore_errors=True)
    shutil.rmtree(tmp_path / "cas", ignore_errors=True)

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


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_a_stored_beats_key_includes_the_extract_key(tmp_path, monkeypatch):
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "finished_page")
    src = "第一章\n福贵在村口。"
    proj = asyncio.run(
        creative_comic(src, output_dir=str(tmp_path), chat=FakeChat(), image=FakeImage())
    )
    chunk = segment_text(src)[0]
    extract_key = proj.state.chunk_cache["0"].extract_action_key
    key_request = build_beats_request(chunk, StoryElements(), model=proj.state.model_snapshot.chat)
    expected = beats_action_key(
        chunk=chunk,
        extract_key=extract_key,
        env=proj.state.model_snapshot.chat,
        stage_src=_BEATS_STAGE_SRC,
        request_digest=key_request.digest(),
    )
    assert proj.state.beat_cache["0"].action_key == expected


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_a_stored_page_plan_key_includes_the_beats_key(tmp_path, monkeypatch):
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "finished_page")
    src = "第一章\n福贵在村口。"
    proj = asyncio.run(
        creative_comic(src, output_dir=str(tmp_path), chat=FakeChat(), image=FakeImage())
    )
    beats = proj.state.beat_cache["0"]
    chunk = segment_text(src)[0]
    key_request = build_page_plan_request(
        chunk,
        StoryElements(),
        recent_layouts=[],
        visual_bible=None,
        key_beats=beats,
        panel_budget=PANELS_PER_CHUNK_B,
        model=proj.state.model_snapshot.chat,
    )
    expected = _page_plan_key(
        chunk,
        beats,
        proj.state.model_snapshot.chat,
        beats.action_key,
        request_digest=key_request.digest(),
    )
    assert proj.state.page_cache["0"].action_key == expected


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_resume_drops_lettering_that_is_not_in_the_source(tmp_path, monkeypatch):
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "finished_page")
    src = "第一章\n福贵在村口。"
    asyncio.run(creative_comic(src, output_dir=str(tmp_path), chat=FakeChat(), image=FakeImage()))
    state = ProjectState.load(tmp_path / "state.json")
    panel = state.page_cache["0"].pages[0].panels[0]
    panel.caption = "福贵在村口。…"
    panel.sfx = "沙沙（爬行的细沙声）"
    state.save(tmp_path / "state.json")

    image = FakeImage()
    proj = asyncio.run(creative_comic(src, output_dir=str(tmp_path), chat=FakeChat(), image=image))
    kept = proj.state.page_cache["0"].pages[0].panels[0]
    assert image.calls == 0
    assert kept.caption == "福贵在村口。"
    assert kept.sfx is None
    report = structural_report(proj.state, src)
    assert next(check.status for check in report.checks if check.name == "visible_text") == "pass"


class _OrderChat(FakeChat):
    def __init__(self, log: list[str]):
        super().__init__()
        self.log = log

    async def chat_function_call(self, messages, tools, tool_choice, **kw):
        self.log.append(tool_choice["function"]["name"])
        return await super().chat_function_call(messages, tools, tool_choice, **kw)


class _OrderImage(FakeImage):
    def __init__(self, log: list[str]):
        super().__init__()
        self.log = log

    async def generate_single_image(self, prompt, reference_image_paths=None, size=None, **kw):
        kind = "page" if "Finished readable manga/comic page" in prompt else "portrait"
        self.log.append(kind)
        return await super().generate_single_image(prompt, reference_image_paths, size, **kw)


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_every_chunk_is_planned_before_any_page_is_drawn(tmp_path, monkeypatch):
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "finished_page")
    src = "第一章\n福贵在村口。\n第二章\n福贵在读书。"
    log: list[str] = []
    asyncio.run(
        creative_comic(src, output_dir=str(tmp_path), chat=_OrderChat(log), image=_OrderImage(log))
    )
    plans = [index for index, item in enumerate(log) if item == "plan_comic_pages"]
    assert len(plans) == 2
    assert plans[-1] < log.index("page")


def _custom_harness(tmp_path: Path, marker: str = "CUSTOM HARNESS MARKER") -> Path:
    target = tmp_path / "custom-harness"
    shutil.copytree(DATA_DIR, target)
    page_plan = target / "stages" / "page_plan.md"
    page_plan.write_text(page_plan.read_text(encoding="utf-8") + "\n" + marker + "\n")
    return target


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_an_explicit_harness_dir_reaches_the_chat_requests(tmp_path, monkeypatch):
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "finished_page")
    custom = _custom_harness(tmp_path)
    out = tmp_path / "out"
    asyncio.run(
        creative_comic(
            "第一章\n福贵在村口。",
            output_dir=str(out),
            chat=FakeChat(),
            image=FakeImage(),
            harness_dir=str(custom),
        )
    )
    prompt = json.loads(
        (out / "review" / "prompts" / "page_plan.c0000.json").read_text(encoding="utf-8")
    )
    assert "CUSTOM HARNESS MARKER" in prompt["messages"][-1]["content"]
    manifest = json.loads((out / "review" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["harness_digest"] == harness_from_dir(custom).digest()


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_harness_dir_env_is_used_when_no_argument_is_passed(tmp_path, monkeypatch):
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "finished_page")
    custom = _custom_harness(tmp_path, "ENV HARNESS MARKER")
    monkeypatch.setenv("INKSTONE_HARNESS_DIR", str(custom))
    out = tmp_path / "out"
    asyncio.run(
        creative_comic(
            "第一章\n福贵在村口。",
            output_dir=str(out),
            chat=FakeChat(),
            image=FakeImage(),
        )
    )
    prompt = json.loads(
        (out / "review" / "prompts" / "page_plan.c0000.json").read_text(encoding="utf-8")
    )
    assert "ENV HARNESS MARKER" in prompt["messages"][-1]["content"]


def test_a_missing_harness_dir_fails_before_any_provider_call(tmp_path):
    chat, image = FakeChat(), FakeImage()
    with pytest.raises(ValueError, match="invalid harness"):
        asyncio.run(
            creative_comic(
                "第一章\n福贵在村口。",
                output_dir=str(tmp_path / "out"),
                chat=chat,
                image=image,
                harness_dir=str(tmp_path / "missing"),
            )
        )
    assert chat.calls == 0
    assert image.calls == 0


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_harness_kill_switch_ignores_override_genre_and_lessons(tmp_path, monkeypatch):
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "finished_page")
    monkeypatch.setenv("INKSTONE_HARNESS", "0")
    monkeypatch.setenv("INKSTONE_GENRE", "wuxia")
    monkeypatch.setenv("INKSTONE_DATA_DIR", str(tmp_path / "data"))
    custom = _custom_harness(tmp_path)
    monkeypatch.setenv("INKSTONE_HARNESS_DIR", str(custom))

    from core.lessons import LessonStore

    store = LessonStore(tmp_path / "data")
    store.add(
        cue="福贵在村口时",
        lesson="先用远景建立空间",
        run="demo",
        decision="redraw",
        pages=["c0000-p0000"],
        genre="wuxia",
    )
    out = tmp_path / "out"
    asyncio.run(
        creative_comic(
            "第一章\n福贵在村口。",
            output_dir=str(out),
            chat=FakeChat(),
            image=FakeImage(),
        )
    )
    prompt = json.loads(
        (out / "review" / "prompts" / "page_plan.c0000.json").read_text(encoding="utf-8")
    )
    text = prompt["messages"][-1]["content"]
    assert "CUSTOM HARNESS MARKER" not in text
    assert "Craft notes from earlier books" not in text
    assert "Domain notes for this genre (wuxia)" not in text
    manifest = json.loads((out / "review" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["harness_fallback"] is True
    assert manifest["genre"] == "general"
    assert manifest["harness_digest"] == harness_from_dir(DATA_DIR).digest()


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_finished_page_resume_does_not_require_a_visual_bible(tmp_path, monkeypatch):
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "finished_page")
    source = "第一章\n福贵在村口。"
    first = FakeImage()
    asyncio.run(creative_comic(source, output_dir=str(tmp_path), chat=FakeChat(), image=first))
    assert first.calls == 2

    state = ProjectState.load(tmp_path / "state.json")
    state.visual_bible = None
    state.save(tmp_path / "state.json")
    # The point is the resume condition, not re-deriving a letter key that was
    # recorded against a bible: force the completeness check so only the
    # visual_bible guard can keep the run out of the resume branch.
    monkeypatch.setattr(
        "core.pipelines.creative_comic._page_chunk_complete", lambda *args, **kwargs: True
    )

    stages: list[str] = []
    second = FakeImage()
    asyncio.run(
        creative_comic(
            source,
            output_dir=str(tmp_path),
            chat=FakeChat(),
            image=second,
            progress_callback=lambda stage, _pct: stages.append(stage),
        )
    )

    assert "resume" in stages


def test_a_budget_missing_a_charged_stage_fails_before_any_call(tmp_path, monkeypatch):
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "finished_page")
    chat, image = FakeChat(), FakeImage()
    spec = BudgetSpec(total=10, reservations={"render": 10})

    with pytest.raises(ValueError, match="does not fund required stages"):
        asyncio.run(
            creative_comic(
                "第一章\n福贵在村口。",
                output_dir=str(tmp_path),
                chat=chat,
                image=image,
                budget=spec,
            )
        )

    assert chat.calls == 0
    assert image.calls == 0


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_beats_and_page_plan_manifests_survive_a_lost_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "finished_page")
    source = "第一章\n福贵在村口。"
    first = FakeChat()
    asyncio.run(creative_comic(source, output_dir=str(tmp_path), chat=first, image=FakeImage()))
    assert first.page_plan_calls == 1
    (tmp_path / "cache.json").unlink()

    second = FakeChat()
    image = FakeImage()
    asyncio.run(creative_comic(source, output_dir=str(tmp_path), chat=second, image=image))

    # extract/bible are restored from their manifests; beats/page_plan must not
    # re-plan either. The visual bible lived in cache.json, so a portrait may be
    # re-established once; the page plan itself must not be repeated.
    assert second.page_plan_calls == 0
    assert any((tmp_path / "pages").glob("page_*.png"))
