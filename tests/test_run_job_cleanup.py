"""tests/test_run_job_cleanup.py — a failing job must not leak its handler.

Review finding: ``_run_job`` used a bare ``log_level.__enter__()`` and released
it in ``finally``, but the handler was attached one line earlier. Any failure
between the two (or an exception raised inside the context manager itself)
would leave the root logger at INFO and the job handler attached for the whole
process, since nothing guarantees the paired ``__exit__``.
"""

import logging

from web import server


def test_run_job_releases_handler_and_level_on_cancel(monkeypatch):
    """A job whose pipeline raises must restore the root level and drop the handler."""
    root = logging.getLogger()
    previous_level = root.level
    previous_handlers = list(root.handlers)

    monkeypatch.setattr(server, "JOBS", {"job1": _job()})

    def boom(*args, **kwargs):
        raise RuntimeError("pipeline exploded")

    monkeypatch.setattr(server, "run_until_complete", boom)
    monkeypatch.setattr(server, "_persist_active_elapsed", lambda *a, **k: None)

    import asyncio

    def fake_run(coro):
        coro.close()
        return boom()

    monkeypatch.setattr(asyncio, "run", fake_run)

    server._run_job("job1", "text", "webtoon", None, "proj1")

    assert root.level == previous_level
    assert [h for h in root.handlers if isinstance(h, server._JobLogHandler)] == []
    assert root.handlers == previous_handlers


def test_run_job_reports_pipeline_failure_in_job(monkeypatch):
    """The failure must still surface to the UI even after the cleanup change."""
    monkeypatch.setattr(server, "JOBS", {"job2": _job()})

    def boom(*args, **kwargs):
        raise RuntimeError("pipeline exploded")

    monkeypatch.setattr(server, "run_until_complete", boom)
    monkeypatch.setattr(server, "_persist_active_elapsed", lambda *a, **k: None)

    import asyncio

    def fake_run(coro):
        coro.close()
        return boom()

    monkeypatch.setattr(asyncio, "run", fake_run)

    server._run_job("job2", "text", "webtoon", None, "proj2")

    job = server.JOBS["job2"]
    assert job["status"] == "error"
    assert "pipeline exploded" in job["error"]


def _job() -> dict:
    """Minimal job record shaped like ``_start_job`` creates."""
    import threading

    return {
        "status": "running",
        "log": [],
        "panels": [],
        "webtoon": None,
        "pdf": None,
        "reader": None,
        "skipped": [],
        "skipped_chunks": [],
        "needs_review": [],
        "stale_panels": [],
        "render_mode": "finished_page",
        "pages_done": [],
        "skipped_pages": [],
        "project_id": "proj",
        "pause_reason": None,
        "base_elapsed": 0.0,
        "session_started_at": None,
        "elapsed_seconds": 0.0,
        "remaining_seconds": None,
        "progress": 0.0,
        "stage": "init",
        "error": None,
        "cancel_event": threading.Event(),
        "cancel_requested": False,
    }
