"""Runtime regressions for the bugs reproduced against the shipped tree."""

import argparse
import inspect
from pathlib import Path

from core import cli
from core.api.error_collector import collect_error
from core.api.image_provider import OpenAICompatProvider
from core.comic.page_prompt import render_finished_page_prompt
from core.comic.segmentation import segment_text
from core.comic.structure_report import chapter_validated, structural_report
from core.pipelines.run_until_complete import run_until_complete
from core.schemas import ProjectState


def test_openai_compat_base_url_covers_chat_and_image(monkeypatch):
    monkeypatch.setenv("PROVIDER", "openai_compat")
    monkeypatch.setenv("OPENAI_COMPAT_CHAT_BASE_URL", "https://old-chat.example/v1")
    monkeypatch.setenv("OPENAI_COMPAT_BASE_URL", "https://old-image.example/v1")
    args = argparse.Namespace(
        provider="openai_compat",
        base_url="https://new.example/v1",
        chat_model=None,
        image_model=None,
    )
    cli._apply_model_flags(args)
    from core.api.chat_provider import get_chat_provider
    from core.api.image_provider import get_image_provider
    from core.config import ChatConfig, ImageConfig

    assert ChatConfig().openai_compat_chat_base_url == "https://new.example/v1"
    assert ImageConfig().openai_compat_base_url == "https://new.example/v1"
    monkeypatch.setenv("OPENAI_COMPAT_API_KEY", "image-key")
    monkeypatch.setenv("OPENAI_COMPAT_CHAT_API_KEY", "chat-key")
    monkeypatch.setenv("OPENAI_COMPAT_CHAT_MODEL", "chat")
    monkeypatch.setenv("OPENAI_COMPAT_MODEL_I2I", "image")
    assert get_chat_provider().base_url == "https://new.example/v1"
    assert get_image_provider().base_url == "https://new.example/v1"


def test_error_collector_accepts_a_non_string_message(monkeypatch):
    monkeypatch.setattr("core.api.error_collector._log_path", lambda: Path("/dev/null"))
    assert collect_error("p", "m", status_code=500, error_message=object()) is not None


def test_empty_structure_is_not_ok():
    state = ProjectState(project_id="empty")
    report = structural_report(state, "")
    assert report.ok is False
    assert chapter_validated(report, state) is False
    assert report.to_json()["ok"] is False


def test_segment_chunks_are_canonical_source_slices():
    source = Path("examples/letter-from-an-unknown-woman.txt").read_text(encoding="utf-8")
    chunks = segment_text(source, max_tokens=6000)
    assert len(chunks) == 6
    assert all(chunk in source for chunk in chunks)
    assert not any("第一章\n\n著名小说家r·" in chunk for chunk in chunks)


def test_supervisor_defaults_to_lettered_export():
    assert inspect.signature(run_until_complete).parameters["export_mode"].default == "lettered"
    assert inspect.signature(run_until_complete).parameters["gate_yes"].default is False


def test_deferred_prompt_avoids_negative_wording():
    prompt = render_finished_page_prompt(
        _plan(),
        characters_by_name={},
        settings_by_name={},
        style_guide="",
        lettering="deferred",
    )
    lowered = prompt.lower()
    assert "edge-to-edge finished artwork" in lowered
    assert "no writing" not in lowered
    assert "no white borders" not in lowered


def test_compat_provider_still_supports_an_explicit_negative_channel():
    provider = OpenAICompatProvider(api_key="k", base_url="https://example.test/v1", model="m")
    payload = provider._build_payload("p", [], "1024x1024", negative_prompt="text overlays")
    assert payload["negative_prompt"] == "text overlays"


def _plan():
    from core.schemas import ComicPagePlan

    return ComicPagePlan(page_id="p1", purpose="test", layout_intent="test")
