"""Runtime regressions for the bugs reproduced against the shipped tree."""

import argparse
import asyncio
import inspect
import json
import re
from pathlib import Path

import pytest
import requests

from core import cli
from core.api.error_collector import collect_error
from core.api.image_provider import OpenAICompatProvider
from core.comic.page_prompt import (
    render_finished_page_negative_prompt,
    render_finished_page_prompt,
    render_page_panel_prompt,
)
from core.comic.segmentation import segment_text
from core.comic.structure_report import chapter_validated, structural_report
from core.pipelines import run_until_complete as supervisor
from core.pipelines.run_until_complete import run_until_complete
from core.schemas import (
    CharacterAsset,
    CharacterCanon,
    CharacterStage,
    ColorBible,
    ColorSwatch,
    ComicPagePlan,
    PagePanelSpec,
    ProjectState,
    Setting,
    VisualBible,
)
from web import server


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


def test_supervisor_defaults_to_art_only_export():
    assert inspect.signature(run_until_complete).parameters["export_mode"].default == "art_only"
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


# Positive-only image prompt redlines.

NEGATIVE_WORD_RE = re.compile(
    r"(?i)\b(?:no|not|never|avoid|without|unless|forbidden|nothing|do not|does not)\b"
)
AUDIT_QUOTE_RE = re.compile(r"(?i)\bsource:\s*")
CHROME_WORD_RE = re.compile(
    r"(?i)\b(?:caption|dialogue|sfx|lettering|speech bubble|thought bubble|"
    r"chrome|design sheet|model sheet|turnaround|line art|readable text|glyph|"
    r"pseudo-script|comic panel|text)\b"
)
EXACT_LETTERING_RE = re.compile(r"(?i)(?:CAPTION|DIALOGUE|SFX)\s*\(exact\)|^\s*lettering:")


def assert_positive_image_prompt(prompt: str) -> None:
    """Assert that an image prompt obeys the Agnes i2i positive-only redlines."""
    assert isinstance(prompt, str)
    assert prompt
    assert not NEGATIVE_WORD_RE.search(prompt), prompt
    assert not AUDIT_QUOTE_RE.search(prompt), prompt
    assert not CHROME_WORD_RE.search(prompt), prompt
    assert not EXACT_LETTERING_RE.search(prompt), prompt
    assert "source: 倚着栏杆" not in prompt
    assert "CAPTION (exact):" not in prompt
    assert "DIALOGUE (exact):" not in prompt
    assert "SFX (exact):" not in prompt


FORBIDDEN_INPUT = (
    'source: "倚着栏杆"',
    "speech bubble",
    "dialogue",
    "caption",
    "SFX",
    "chrome",
    "design sheet",
    "line art",
)


def _visual_bible() -> VisualBible:
    return VisualBible(
        version="bible_v3",
        style_guide="manhua muted European period",
        era="Vienna c.1900-1910",
        era_forbidden_wardrobe=["hoodie", "sports shoes"],
        color=ColorBible(
            palette=[
                ColorSwatch(name="Muted Plum", hex="#7A5C6E", usage="scene accent"),
            ],
            lighting="soft overcast",
            forbidden=["neon", "chrome"],
        ),
        characters={
            "虎妞": CharacterCanon(
                canonical_name="虎妞",
                gender="female",
                face_lock="sturdy woman, calm eyes",
                stages=[
                    CharacterStage(
                        stage="adult",
                        outfit_lock="traditional embroidered jacket",
                        hair_lock="black hair in a low bun",
                        portrait_key="虎妞@adult",
                    )
                ],
            )
        },
        content_hash="redline",
    )


def _characters() -> dict[str, CharacterAsset]:
    return {
        name: CharacterAsset(
            name=name,
            l1_prompt=l1,
            portrait_local=f"/tmp/{index}_trimmed.png",
        )
        for index, (name, l1) in enumerate(
            (
                ("虎妞", "虎妞, sturdy woman in traditional clothes"),
                ("R", "adult man, calm dark eyes, dark tailored suit"),
                ("少年", "young man, short black hair, plain tunic"),
            ),
            start=1,
        )
    }


def _settings() -> dict[str, Setting]:
    return {
        "亭子": Setting(
            name="亭子",
            scene_prompt="lakeside pavilion at dusk, warm lantern light, wooden railing",
        )
    }


def _finished_plan(*, cast: list[str], timeline: str = "") -> ComicPagePlan:
    return ComicPagePlan.model_validate(
        {
            "page_id": "p1",
            "purpose": "a quiet staged memory",
            "layout_intent": "Wide top archive; diagonal window attack",
            "timeline": timeline,
            "covers_beats": ["village_gate", "quiet_reply"],
            "panels": [
                {
                    "panel_id": "1",
                    "action": "R reads a letter at the desk",
                    "characters": cast,
                    "setting_ref": "亭子",
                    "caption": "MIDNIGHT AT THE SKY ARCHIVE",
                    "dialogue": "This map is moving.",
                    "sfx": "soft rustle",
                    "lettering_notes": "bubble near face - leave empty",
                    "timeline": timeline,
                }
            ],
            "lettering_boxes": [
                {"kind": "dialogue", "panel_id": "1", "x": 0.2, "y": 0.3, "w": 0.3, "h": 0.1}
            ],
            "reference_characters": cast,
            "setting_refs": ["亭子"],
        }
    )


def _panel_plan(*, cast: list[str], action: str) -> tuple[ComicPagePlan, PagePanelSpec]:
    plan = ComicPagePlan.model_validate(
        {
            "page_id": "p2",
            "layout_intent": "environmental_wide",
            "panels": [
                {
                    "panel_id": "g1",
                    "action": action,
                    "characters": cast,
                    "setting_ref": "亭子",
                }
            ],
            "reference_characters": cast,
            "setting_refs": ["亭子"],
        }
    )
    return plan, plan.panels[0]


@pytest.mark.parametrize("lettering", ("deferred", "in_image"))
@pytest.mark.parametrize("strict", (False, True))
@pytest.mark.parametrize("timeline", ("", "past", "liminal"))
def test_finished_page_prompts_follow_positive_redlines(
    lettering: str, strict: bool, timeline: str
) -> None:
    plan = _finished_plan(cast=["虎妞", "R"], timeline=timeline)
    prompt = render_finished_page_prompt(
        plan,
        characters_by_name=_characters(),
        settings_by_name=_settings(),
        style_guide="manhua, " + ", ".join(FORBIDDEN_INPUT),
        strict=strict,
        lettering=lettering,
        visual_bible=_visual_bible(),
        reference_images=["/tmp/1_trimmed.png", "/tmp/2_trimmed.png"],
    )
    assert_positive_image_prompt(prompt)
    lowered = prompt.lower()
    assert "edge-to-edge" in lowered
    assert "painted artwork" in lowered or "fully colored" in lowered
    assert "locked identity" in lowered or "identity" in lowered
    assert "locked outfit" in lowered or "exact outfit" in lowered
    assert "stable age" in lowered or "same age" in lowered
    assert "虎妞" in prompt
    assert "Wide top archive" in prompt
    assert "MIDNIGHT AT THE SKY ARCHIVE" not in prompt
    assert "This map is moving." not in prompt
    assert "soft rustle" not in prompt
    assert "倚着栏杆" not in prompt
    assert render_finished_page_negative_prompt(strict=strict, lettering=lettering) == ""


@pytest.mark.parametrize("cast_count", (0, 1, 2))
@pytest.mark.parametrize("strict", (False, True))
def test_panel_prompts_follow_positive_redlines(cast_count: int, strict: bool) -> None:
    cast = ["虎妞", "R"][:cast_count]
    injected = "; ".join(FORBIDDEN_INPUT)
    plan, panel = _panel_plan(
        cast=cast,
        action=f"R leans over the railing; {injected}; a quiet beat lands",
    )
    characters = _characters()
    prompt = render_page_panel_prompt(
        plan,
        panel,
        characters_by_name=characters,
        settings_by_name=_settings(),
        style_guide=f"manhua; {injected}",
        visual_bible=_visual_bible(),
        reference_images=[f"/tmp/{index}_trimmed.png" for index in range(1, cast_count + 1)],
        strict=strict,
    )
    assert_positive_image_prompt(prompt)
    lowered = prompt.lower()
    assert "edge-to-edge" in lowered
    assert "painted artwork" in lowered
    assert "fully colored" in lowered
    assert "locked identity" in lowered or "identity" in lowered
    assert "locked outfit" in lowered or "exact outfit" in lowered
    if cast_count:
        assert "stable age" in lowered or "same age" in lowered
    assert (
        "scenery only" in lowered if cast_count == 0 else "the only people in the image" in lowered
    )
    if cast_count:
        assert "only once" in lowered
        assert "pose follows the action" in lowered


def test_prompt_redlines_scrub_every_forbidden_input_channel() -> None:
    injected = "; ".join(FORBIDDEN_INPUT)
    plan = _finished_plan(cast=["R"])
    plan.purpose = injected
    plan.layout_intent = injected
    plan.panels[0].action = injected
    characters = {
        "R": CharacterAsset(
            name="R",
            l1_prompt=f"adult man, calm eyes; {injected}",
        )
    }
    settings = {"亭子": Setting(name="亭子", scene_prompt=f"lakeside pavilion; {injected}")}
    prompt = render_finished_page_prompt(
        plan,
        characters_by_name=characters,
        settings_by_name=settings,
        style_guide=injected,
        visual_bible=_visual_bible(),
        lettering="in_image",
        strict=True,
    )
    assert_positive_image_prompt(prompt)
    assert "倚着栏杆" not in prompt
    assert "MIDNIGHT AT THE SKY ARCHIVE" not in prompt
    assert "This map is moving." not in prompt
    assert "soft rustle" not in prompt

    panel_plan, panel = _panel_plan(cast=["R"], action=injected)
    panel_prompt = render_page_panel_prompt(
        panel_plan,
        panel,
        characters_by_name=characters,
        settings_by_name=settings,
        style_guide=injected,
        visual_bible=_visual_bible(),
    )
    assert_positive_image_prompt(panel_prompt)
    assert "倚着栏杆" not in panel_prompt


def test_cli_no_lettering_is_a_per_run_value(monkeypatch):
    args = cli._build_parser().parse_args(["generate", "--no-lettering"])
    import core.cli as cli_module
    import core.cli_generate as cli_generate_module
    import core.config as config_module

    captured: dict = {}
    monkeypatch.setattr(
        config_module,
        "set_lettering_env",
        lambda *_: (_ for _ in ()).throw(AssertionError("global env must not be mutated")),
    )
    monkeypatch.setattr(
        cli_generate_module,
        "run_generate",
        lambda *args, **kwargs: captured.update(kwargs) or 0,
    )
    try:
        cli_module._run_generate(args)
    except SystemExit:
        pass
    assert captured["lettering_enabled"] is False


def test_run_until_complete_forwards_per_run_lettering(monkeypatch):
    captured: dict = {}

    async def fake_creative(*_args, **kwargs):
        captured.update(kwargs)
        return "done"

    monkeypatch.setattr(supervisor, "creative_comic", fake_creative)
    result = asyncio.run(
        run_until_complete(
            "text",
            output_dir="/in-memory-project",
            lettering_enabled=False,
            acquire_project_lock=False,
        )
    )
    assert result == "done"
    assert captured["lettering_enabled_override"] is False
    assert captured["acquire_lock"] is False


def test_run_until_complete_holds_project_lock_across_retry_sleep(monkeypatch):
    lock_events: list[str] = []

    class FakeLock:
        def __enter__(self):
            lock_events.append("enter")
            return self

        def __exit__(self, *_args):
            lock_events.append("exit")

    def fake_project_lock(_path):
        return FakeLock()

    calls = {"n": 0}

    async def fake_creative(*_args, **_kwargs):
        calls["n"] += 1
        if calls["n"] < 2:
            raise requests.ReadTimeout("read timed out")
        return "done"

    async def fake_sleep(_delay):
        assert lock_events == ["enter"]
        return None

    monkeypatch.setattr(supervisor, "_project_lock", fake_project_lock)
    monkeypatch.setattr(supervisor, "creative_comic", fake_creative)
    monkeypatch.setattr(supervisor.asyncio, "sleep", fake_sleep)
    result = asyncio.run(
        run_until_complete(
            "text",
            output_dir="/in-memory-project",
            deadline_hours=1.0,
            backoff_base=0.01,
            backoff_cap=0.01,
        )
    )
    assert result == "done"
    assert calls["n"] == 2
    assert lock_events == ["enter", "exit"]


def test_web_run_job_does_not_bypass_the_sample_gate():
    source = inspect.getsource(server._run_job)
    assert "gate_yes=True" not in source
    assert "gate_yes" not in source
    assert "lettering_enabled=lettering_enabled_override" in source


def test_generate_uses_a_per_run_lettering_value(monkeypatch):
    captured: dict = {}

    def fake_start(*_args, **kwargs):
        captured.update(kwargs)
        return "job1", "lettering-project"

    monkeypatch.setattr(server, "_start_job", fake_start)
    monkeypatch.setattr(server, "_providers_configured", lambda: True)
    body = json.dumps({"text": "a novel", "format": "cbz", "lettering_enabled": False}).encode()
    handler = _handler_for("/api/generate", "POST", body)
    handler._post_generate()
    assert handler.status == 200
    assert json.loads(handler.body)["project_id"] == "lettering-project"
    assert captured["lettering_enabled"] is False


def test_web_gate_api_records_decisions_and_audited_approval(monkeypatch):
    project_id = "gate-project"
    session = _FakeGateSession(project_id)
    sessions: dict[str, _FakeGateSession] = {project_id: session}
    monkeypatch.setattr(
        server,
        "_gate_session",
        lambda pid, **kwargs: _gate_session_fake(pid, sessions, kwargs),
    )

    handler = _handler_for(f"/api/project/{project_id}/gate", "GET")
    handler.do_GET()
    assert handler.status == 200
    assert json.loads(handler.body)["pending_keys"] == ["c0000-p0000"]

    decision = json.dumps(
        {"key": "c0000-p0000", "decision": "accept", "reason_tier": "page"}
    ).encode()
    handler = _handler_for(f"/api/project/{project_id}/gate", "POST", decision)
    handler.do_POST()
    assert handler.status == 200
    assert json.loads(handler.body)["decisions"]["c0000-p0000"]["choice"] == "accept"

    handler = _handler_for(
        f"/api/project/{project_id}/gate",
        "POST",
        json.dumps({"approve": True}).encode(),
    )
    handler.do_POST()
    assert handler.status == 200
    payload = json.loads(handler.body)
    assert payload["yes"] is True
    assert payload["awaiting"] is False
    assert session.yes_calls == 1


class _FakeDecision:
    def __init__(self, choice: str, reason_tier: str | None = None) -> None:
        self.choice = choice
        self.reason_tier = reason_tier


class _FakeGateProject:
    def __init__(self) -> None:
        self.sample_keys = ["c0000-p0000"]
        self.decisions: dict[str, _FakeDecision] = {}
        self.released = False
        self.yes = False
        self.paused_key = "c0000-p0001"
        self.sample_size = 1


class _FakeGateSession:
    def __init__(self, project_id: str) -> None:
        self.project_id = project_id
        self.project = _FakeGateProject()
        self.yes_calls = 0

    @property
    def awaiting(self) -> bool:
        return (
            self.project.paused_key is not None
            and not self.project.released
            and not self.project.yes
        )

    def record(self, key, choice, *, reason_tier=None, state=None, **_kwargs):
        if key not in self.project.sample_keys:
            raise ValueError(f"{key} is not in the sample")
        self.project.decisions[key] = _FakeDecision(choice.replace("-", "_"), reason_tier)
        return self.project


def _gate_session_fake(project_id, sessions, kwargs):
    session = sessions[project_id]
    if kwargs.get("yes"):
        session.yes_calls += 1
        session.project.yes = True
        session.project.paused_key = None
    return session


class _FakeHandler:
    def __init__(self, path: str, method: str, body: bytes | None = None) -> None:
        self.path = path
        self.command = method
        self._body = body or b""
        self.headers = {"Content-Length": str(len(self._body))}
        if self._body:
            self.headers["Content-Type"] = "application/json"
        self.status = 200
        self.body = b""
        self.wfile = self

    def _send_json(self, payload, status=200):
        self.status = status
        self.body = json.dumps(payload, ensure_ascii=False).encode()

    def write(self, _body):
        return None

    def _read_json(self):
        return json.loads(self._body or b"{}")

    def _reject_sensitive(self):
        return False

    def do_GET(self):
        server.Handler.do_GET(self)

    def do_POST(self):
        server.Handler.do_POST(self)

    def _post_generate(self):
        server.Handler._post_generate(self)

    def _post_gate(self):
        server.Handler._post_gate(self)


def _handler_for(path, method, body=None):
    return _FakeHandler(path, method, body)


def test_index_html_has_gate_and_lettering_controls():
    html = (Path("web/index.html")).read_text(encoding="utf-8")
    assert 'id="letteringEnabled"' in html
    assert 'id="gateApprove"' in html
    assert "/api/project/" in html and '/gate"' in html


def test_default_publishing_is_art_only_without_lettering_overlays():
    """The user's standing redline: ordinary runs never draw speech bubbles."""
    from core import cli_generate, config
    from core.pipelines import creative_comic as pipeline
    from core.pipelines.export_stages import prepare_finished_page_export

    assert config.lettering_enabled() is False
    assert inspect.signature(cli_generate.run_generate).parameters["export_mode"].default == (
        "art_only"
    )
    assert (
        inspect.signature(cli_generate.run_generate).parameters["lettering_enabled"].default
        is False
    )
    assert inspect.signature(run_until_complete).parameters["export_mode"].default == "art_only"
    assert inspect.signature(run_until_complete).parameters["lettering_enabled"].default is False
    assert inspect.signature(pipeline.creative_comic).parameters["export_mode"].default == (
        "art_only"
    )
    assert (
        inspect.signature(pipeline.creative_comic).parameters["lettering_enabled_override"].default
        is False
    )
    assert (
        inspect.signature(prepare_finished_page_export).parameters["export_mode"].default
        == "art_only"
    )
    assert inspect.signature(server._run_job).parameters["export_mode"].default == "art_only"
    assert (
        inspect.signature(server._run_job).parameters["lettering_enabled_override"].default is False
    )
    assert inspect.signature(server._start_job).parameters["export_mode"].default == "art_only"
    assert inspect.signature(server._start_job).parameters["lettering_enabled"].default is False


def test_default_page_plan_request_asks_for_pure_artwork():
    from core.schemas import StoryElements
    from core.screenwriter import build_page_plan_request, build_storyboard_request

    request = build_page_plan_request(
        "第一回\n福贵在村口。", StoryElements(), lettering=False, model="chat-a"
    )
    content = request.messages[1]["content"].lower()
    tool = request.tools[0]["function"]
    assert "purely painted artwork" in content
    assert "purely painted artwork" in content
    assert "edge-to-edge painted artwork" in tool["description"].lower()
    assert (
        build_page_plan_request(
            "第一回\n福贵在村口。", StoryElements(), lettering=True, model="chat-a"
        ).digest()
        != request.digest()
    )

    storyboard = build_storyboard_request(
        "第一回\n福贵在村口。", StoryElements(), lettering=False, model="chat-a"
    )
    storyboard_blob = json.dumps(storyboard.messages, ensure_ascii=False).lower()
    assert "purely painted artwork" in storyboard_blob
    assert "split narration" not in storyboard_blob
    assert "speech into dialogue" not in storyboard_blob


def test_art_only_plans_scrub_provider_lettering_without_a_retry():
    import asyncio

    from core.schemas import StoryElements
    from core.screenwriter import plan_comic_pages, plan_storyboard

    class FakeChat:
        model = "chat-a"
        calls = 0

        async def chat_function_call(self, *_args, **_kwargs):
            self.calls += 1
            return {
                "chapter_id": "ch01",
                "pages": [
                    {
                        "page_id": "p1",
                        "panels": [
                            {
                                "panel_id": "g1",
                                "action": "福贵 leans over the rail",
                                "caption": "夜色降临",
                                "dialogue": "我回来了",
                                "sfx": "wind",
                            }
                        ],
                        "lettering_boxes": [
                            {
                                "kind": "caption",
                                "panel_id": "g1",
                                "x": 0.1,
                                "y": 0.1,
                                "w": 0.2,
                                "h": 0.1,
                            }
                        ],
                    }
                ],
            }

    chat = FakeChat()
    storyboard = asyncio.run(
        plan_storyboard("福贵在村口。", StoryElements(), chat=chat, lettering=False)
    )
    pageset = asyncio.run(
        plan_comic_pages("福贵在村口。", StoryElements(), chat=chat, lettering=False)
    )
    assert chat.calls == 2
    assert all(
        panel.dialogue is None and panel.caption is None and panel.sfx is None
        for panel in storyboard.panels
    )
    assert pageset.pages[0].lettering_boxes == []
    assert all(
        panel.caption is None and panel.dialogue is None and panel.sfx is None
        for panel in pageset.pages[0].panels
    )


def test_default_export_never_binds_a_caption_bar_even_when_blank_is_missing():
    from unittest.mock import patch

    from core.pipelines.export_stages import prepare_finished_page_export

    pages = Path("/in-memory-project/pages")
    lettered = pages / "page_c0000_p0000.png"
    exists = {lettered}
    with (
        patch("core.pipelines.resume.finished_plan_files", return_value=[lettered]),
        patch("pathlib.Path.is_file", lambda self: self in exists),
        patch("pathlib.Path.glob", lambda self, _pattern: []),
        patch("pathlib.Path.unlink"),
    ):
        files = prepare_finished_page_export(pages, state=object())
    assert files == []


def test_default_cli_and_web_never_request_lettered_pages(monkeypatch):
    import json

    args = cli._build_parser().parse_args(["generate", "book.txt"])
    assert args.export_lettered is False
    assert args.export_art_only is False
    assert args.no_lettering is False

    captured: dict = {}

    def fake_start(*_args, **kwargs):
        captured.update(kwargs)
        return "job1", "bubble-free-project"

    monkeypatch.setattr(server, "_start_job", fake_start)
    monkeypatch.setattr(server, "_providers_configured", lambda: True)
    body = json.dumps({"text": "a novel", "format": "cbz"}).encode()
    handler = _handler_for("/api/generate", "POST", body)
    handler._post_generate()
    assert handler.status == 200
    assert captured["export_mode"] == "art_only"
    assert captured["lettering_enabled"] is False

    html = (Path("web/index.html")).read_text(encoding="utf-8")
    assert 'id="letteringEnabled" type="checkbox" checked' not in html


def test_image_prompts_lock_eyes_without_copying_reference_expression():
    plan = _finished_plan(cast=["虎妞", "R"])
    prompt = render_finished_page_prompt(
        plan,
        characters_by_name=_characters(),
        settings_by_name=_settings(),
        style_guide="",
        visual_bible=_visual_bible(),
        reference_images=["/tmp/1_trimmed.png", "/tmp/2_trimmed.png"],
    )
    assert_positive_image_prompt(prompt)
    lowered = prompt.lower()
    assert "fully open eyes" in lowered
    assert "two symmetric dark pupils" in lowered
    assert "gaze follows the action" in lowered
    assert "pose follows the action" in lowered
    assert "reference portraits are fully colored painted art of one person each" in lowered
    assert "supplying a painted face" not in lowered
    assert "supplying the face" not in lowered
    assert "with the face, hair, and clothes of their own" not in lowered

    panel_plan, panel = _panel_plan(cast=["R"], action="R looks toward the lantern")
    panel_prompt = render_page_panel_prompt(
        panel_plan,
        panel,
        characters_by_name=_characters(),
        settings_by_name=_settings(),
        style_guide="",
        visual_bible=_visual_bible(),
        reference_images=["/tmp/3_trimmed.png"],
    )
    assert_positive_image_prompt(panel_prompt)
    lowered = panel_prompt.lower()
    assert "fully open eyes" in lowered
    assert "two symmetric dark pupils" in lowered
    assert "gaze follows the action" in lowered
    assert "pose follows the action" in lowered
    assert "reference portrait supplying the face" not in lowered


def test_default_portrait_prompt_asks_for_open_eyes():
    from core.comic.identity import harden_human_identity_prompt, sanitize_portrait_prompt

    prompt = harden_human_identity_prompt("少年", "young man, short black hair")
    assert "fully open eyes" in prompt
    assert "two symmetric dark pupils" in prompt

    cleaned = sanitize_portrait_prompt(
        "young man, short black hair; ⚠ no source evidence; source: “倚着栏杆”; "
        "line art; without closed eyes",
        name="少年",
    )
    lowered = cleaned.lower()
    assert "fully open eyes" in lowered
    assert "two symmetric dark pupils" in lowered
    assert not re.search(
        r"⚠|no source evidence|unverified|source:|倚着栏杆|line art|without|closed eyes",
        lowered,
    )


def test_portrait_prompt_sent_bytes_are_positive_and_invalidate_legacy_keys():
    from core.comic.cas import portrait_action_key
    from core.pipelines import creative_comic as pipeline

    asset = CharacterAsset(
        name="少年",
        l1_prompt="young man, short black hair",
        portrait_prompt="young man; ⚠ no source evidence; line art",
    )
    material = pipeline._portrait_key_material("少年", asset, "manhua")
    assert "fully open eyes" in material
    assert "two symmetric dark pupils" in material
    assert not re.search(r"⚠|no source evidence|line art", material, re.IGNORECASE)
    legacy_material = "\n".join(
        [
            "少年",
            "young man; ⚠ no source evidence; line art",
            "young man, short black hair",
            "manhua",
            pipeline.DEFAULT_PORTRAIT_STYLE,
        ]
    )
    assert portrait_action_key(
        name="少年", material=material, env="image-a", stage_src=pipeline._PORTRAIT_STAGE_SRC
    ) != portrait_action_key(
        name="少年",
        material=legacy_material,
        env="image-a",
        stage_src=pipeline._PORTRAIT_STAGE_SRC,
    )


def test_portrait_style_is_positive_and_changes_the_portrait_cache_key(monkeypatch):
    from core.comic.consistency import DEFAULT_PORTRAIT_STYLE
    from core.pipelines import creative_comic as pipeline

    lowered = DEFAULT_PORTRAIT_STYLE.lower()
    assert "fully open eyes" in lowered
    assert "two symmetric dark pupils" in lowered
    assert "speech bubble" not in lowered
    assert "caption" not in lowered
    assert "lettering" not in lowered
    assert "watermark" not in lowered
    assert not re.search(r"\b(?:no|not|never|without)\b", lowered)

    asset = CharacterAsset(name="少年", l1_prompt="young man, short black hair")
    before = pipeline._portrait_key_material("少年", asset, "style")
    monkeypatch.setattr(
        pipeline, "DEFAULT_PORTRAIT_STYLE", DEFAULT_PORTRAIT_STYLE + ", iris detail"
    )
    after = pipeline._portrait_key_material("少年", asset, "style")
    assert before != after


def test_neutral_trimmed_reference_is_bound_to_its_character():
    from core.comic.page_prompt import _reference_bindings

    characters = {
        "少年": CharacterAsset(
            name="少年",
            l1_prompt="young man, short black hair",
            portrait_local="/tmp/boy.png",
        )
    }
    refs = ["/tmp/boy_neutral_trimmed.png"]
    assert _reference_bindings(["少年"], characters, refs) == [(1, "少年")]


def test_eye_state_and_dialogue_grid_are_rewritten_positively():
    plan = _finished_plan(cast=["R"])
    plan.purpose = "dialogue_grid: a single spoken line, eyes softening"
    plan.layout_intent = "eyes closed, half-lidded gaze"
    plan.panels[0].action = "R looks toward the candle with eyes closed"
    prompt = render_finished_page_prompt(
        plan,
        characters_by_name=_characters(),
        settings_by_name=_settings(),
        style_guide="",
        visual_bible=_visual_bible(),
    )
    assert_positive_image_prompt(prompt)
    lowered = prompt.lower()
    assert "eyes closed" not in lowered
    assert "eyes softening" not in lowered
    assert "half-lidded" not in lowered
    assert "dialogue_grid" not in lowered
    assert "single spoken line" not in lowered
    assert "fully open eyes" in lowered
    assert "two symmetric dark pupils" in lowered

    panel_plan, panel = _panel_plan(cast=["R"], action="R squints, eyes closed")
    panel_prompt = render_page_panel_prompt(
        panel_plan,
        panel,
        characters_by_name=_characters(),
        settings_by_name=_settings(),
        style_guide="",
        visual_bible=_visual_bible(),
    )
    assert_positive_image_prompt(panel_prompt)
    assert "eyes closed" not in panel_prompt.lower()
    assert "squint" not in panel_prompt.lower()
    assert "fully open eyes" in panel_prompt.lower()


def test_web_rejects_conflicting_lettering_and_export_modes():
    import json

    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(server, "_providers_configured", lambda: True)
        body = json.dumps(
            {
                "text": "a novel",
                "format": "page",
                "lettering_enabled": False,
                "export_mode": "lettered",
            }
        ).encode()
        handler = _handler_for("/api/generate", "POST", body)
        handler._post_generate()
        assert handler.status == 400
        assert "requires lettering_enabled=true" in json.loads(handler.body)["error"]
    finally:
        monkeypatch.undo()


def test_source_watermark_never_reaches_chunks_or_image_prompts():
    import unicodedata

    from core.comic.page_prompt import render_page_panel_prompt
    from core.comic.segmentation import segment_text
    from core.schemas import ComicPagePlan, PagePanelSpec, Setting, StoryElements
    from core.screenwriter import build_extract_request, build_page_plan_request, sanitize_text

    source = Path("examples/letter-from-an-unknown-woman.txt").read_text(encoding="utf-8")
    watermark = next(
        line.strip()
        for line in source.splitlines()
        if "x" in line.lower() and "txt" in line.lower() and len(line.strip()) < 80
    )

    def normalized(text):
        return unicodedata.normalize("NFKC", text).casefold()

    def polluted(text):
        return watermark in text or "xiaoshuotxt" in normalized(text)

    cleaned = sanitize_text(source)
    assert not polluted(cleaned)
    chunks = segment_text(cleaned)
    assert all(not polluted(chunk) for chunk in chunks)
    assert build_extract_request(chunks[0]).messages[1]["content"]
    assert not polluted(build_extract_request(chunks[0]).messages[1]["content"])
    assert not polluted(build_page_plan_request(chunks[0], StoryElements()).messages[1]["content"])
    plan = ComicPagePlan(page_id="p1", panels=[PagePanelSpec(panel_id="g1", action=watermark)])
    image_prompt = render_page_panel_prompt(
        plan,
        plan.panels[0],
        characters_by_name={},
        settings_by_name={"s": Setting(name="s", scene_prompt=watermark)},
        style_guide="",
    )
    assert not polluted(image_prompt)


def test_scenery_and_landmark_assets_are_not_people():
    from pydantic import ValidationError

    from core.comic.segmentation import merge_characters
    from core.schemas import CharacterAsset, NonCharacterAsset, StoryElements

    landmark = {
        "name": "雾中灯塔",
        "role": "背景意象",
        "l1_prompt": "n/a — non-character",
        "portrait_prompt": "n/a — inanimate background element",
    }
    elements = StoryElements.model_validate(
        {"characters": [{"name": "沈知意", "role": "protagonist"}, landmark]}
    )
    assert [character.name for character in elements.characters] == ["沈知意"]
    with pytest.raises((ValidationError, NonCharacterAsset)):
        CharacterAsset.model_validate(landmark)
    merged, created = merge_characters({}, [])
    assert created == []
    assert merged == {}


def test_action_clause_scrubbing_preserves_visible_action():
    from core.comic.page_prompt import render_page_panel_prompt
    from core.schemas import ComicPagePlan, PagePanelSpec

    plan = ComicPagePlan(
        page_id="p1",
        panels=[
            PagePanelSpec(panel_id="g1", action="沈知意 pours tea; a speech bubble floats above")
        ],
    )
    prompt = render_page_panel_prompt(
        plan, plan.panels[0], characters_by_name={}, settings_by_name={}, style_guide=""
    )
    assert "pours tea" in prompt
    assert "speech bubble" not in prompt.lower()


def test_legacy_landmark_identity_is_removed_from_resumed_state():
    import json

    from core.comic.visual_bible import sanitize_visual_bible_state
    from core.schemas import ProjectState

    raw = json.loads(Path("comic_out/state.json").read_text(encoding="utf-8"))
    raw.update(json.loads(Path("comic_out/cache.json").read_text(encoding="utf-8")))
    state = ProjectState.model_validate(raw)
    assert "雾中灯塔" not in state.characters
    sanitize_visual_bible_state(state)
    assert "雾中灯塔" not in state.visual_bible.characters
    assert "雾中灯塔" not in state.page_cache["0"].pages[0].reference_characters
    assert all(
        "雾中灯塔" not in panel.characters for panel in state.page_cache["0"].pages[0].panels
    )


def test_bad_single_eye_reference_is_rejected_before_i2i():
    from PIL import Image

    from core.pipelines.creative_comic import _reference_eyes_are_symmetric

    image = Image.new("RGB", (200, 300), (235, 235, 235))
    pixels = image.load()
    for y in range(60, 85):
        for x in range(45, 70):
            pixels[x, y] = (20, 20, 20)
    assert _reference_eyes_are_symmetric(image) is False


def test_eye_locks_require_two_symmetric_painted_irises():
    from core.comic.consistency import DEFAULT_PORTRAIT_STYLE
    from core.comic.identity import EYE_IDENTITY_LOCK

    for text in (EYE_IDENTITY_LOCK, DEFAULT_PORTRAIT_STYLE):
        lowered = text.lower()
        assert "two fully open eyes" in lowered
        assert "two symmetric dark pupils" in lowered
        assert "two clearly defined irises" in lowered
        assert "fully painted in the center" in lowered
