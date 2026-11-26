"""Regression tests for Web gate approval, per-run lettering, and supervisor locking."""

import asyncio
import inspect
import json
from pathlib import Path

import requests

from core.cli import _build_parser
from core.pipelines import run_until_complete as supervisor
from core.pipelines.run_until_complete import run_until_complete
from web import server


def test_cli_no_lettering_is_a_per_run_value(monkeypatch):
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
    args = _build_parser().parse_args(["generate", "--no-lettering"])
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


def test_web_job_does_not_bypass_the_sample_gate():
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


class _FakeHandler(server.Handler):
    def __init__(self, path: str, method: str, body: bytes | None = None) -> None:
        self.path = path
        self.command = method
        self._body = body or b""
        self.headers = {"Content-Length": str(len(self._body))}
        if self._body:
            self.headers["Content-Type"] = "application/json"
        self.status = 200
        self.body = b""

    def _send_json(self, payload, status=200):
        self.status = status
        self.body = json.dumps(payload, ensure_ascii=False).encode()

    @property
    def rfile(self):
        from io import BytesIO

        return BytesIO(self._body)

    def _reject_sensitive(self):
        return False


def _handler_for(path, method, body=None):
    return _FakeHandler(path, method, body)


def test_index_html_has_gate_and_lettering_controls():
    html = (Path(server.__file__).resolve().parent / "index.html").read_text(encoding="utf-8")
    assert 'id="letteringEnabled"' in html
    assert 'id="gateApprove"' in html
    assert "/api/project/" in html and '/gate"' in html
