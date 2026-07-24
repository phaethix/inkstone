"""tests/test_run_until_complete.py — supervisor loop for unattended runs."""

import asyncio
from dataclasses import dataclass, field

import requests

from core.pipelines.run_until_complete import (
    PausedRun,
    is_transient_error,
    run_until_complete,
)
from core.schemas import ProjectState


@dataclass
class _FakeProject:
    project_id: str = "p"
    state: ProjectState = field(default_factory=lambda: ProjectState(project_id="p"))
    pages: list = field(default_factory=list)
    pdf: str | None = None
    webtoon: str | None = None


def test_is_transient_timeout():
    assert is_transient_error(requests.ReadTimeout("read timed out")) is True
    assert is_transient_error(requests.ConnectTimeout("connect")) is True
    assert is_transient_error(requests.ConnectionError("reset")) is True


def test_is_transient_runtime_max_retries():
    assert (
        is_transient_error(RuntimeError("AgnesImageAPI max retries (5) exceeded, last status 503"))
        is True
    )
    assert is_transient_error(RuntimeError("image queue is full")) is True


def test_is_not_transient_value_error():
    assert is_transient_error(ValueError("duplicate panel_id")) is False
    assert is_transient_error(RuntimeError("project is already running: /tmp/x")) is False


def test_run_until_complete_retries_then_succeeds(monkeypatch, tmp_path):
    calls = {"n": 0}

    async def fake_creative(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] < 3:
            raise requests.ReadTimeout("read timed out")
        return _FakeProject(project_id="ok")

    monkeypatch.setattr(
        "core.pipelines.run_until_complete.creative_comic",
        fake_creative,
    )
    sleeps: list[float] = []

    async def fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr("core.pipelines.run_until_complete.asyncio.sleep", fake_sleep)

    result = asyncio.run(
        run_until_complete(
            "text",
            output_dir=str(tmp_path),
            deadline_hours=1.0,
            backoff_base=1.0,
            backoff_cap=2.0,
        )
    )
    assert isinstance(result, _FakeProject)
    assert result.project_id == "ok"
    assert calls["n"] == 3
    assert len(sleeps) == 2


def test_run_until_complete_pauses_on_deadline(monkeypatch, tmp_path):
    async def always_fail(*args, **kwargs):
        raise requests.ReadTimeout("read timed out")

    monkeypatch.setattr(
        "core.pipelines.run_until_complete.creative_comic",
        always_fail,
    )

    # Freeze monotonic: first call start, then each check advances past deadline.
    times = iter([100.0, 100.0, 10_000.0])

    def fake_monotonic():
        return next(times, 10_000.0)

    monkeypatch.setattr(
        "core.pipelines.run_until_complete.time.monotonic",
        fake_monotonic,
    )

    async def no_sleep(_delay):
        return None

    monkeypatch.setattr("core.pipelines.run_until_complete.asyncio.sleep", no_sleep)

    # Seed a state.json so pause can load it.
    ProjectState(project_id="paused-p").save(tmp_path / "state.json")

    result = asyncio.run(
        run_until_complete(
            "text",
            output_dir=str(tmp_path),
            deadline_hours=0.001,  # tiny; second monotonic jump exceeds it
            backoff_base=0.01,
            backoff_cap=0.01,
        )
    )
    assert isinstance(result, PausedRun)
    assert "deadline" in result.reason.lower() or "wall" in result.reason.lower()
