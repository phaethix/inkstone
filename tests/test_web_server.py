"""Web server configuration, project helpers, and artifact-boundary tests."""

import json
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from http.server import ThreadingHTTPServer

from core.schemas import (
    CharacterAliasSuggestion,
    CharacterAsset,
    ChunkCache,
    Panel,
    ProjectState,
    Storyboard,
)
from web import server


def test_compat_only_provider_configuration_is_accepted(monkeypatch):
    monkeypatch.delenv("AGNES_API_KEY", raising=False)
    monkeypatch.setenv("PROVIDER", "openai_compat")
    monkeypatch.setenv("OPENAI_COMPAT_BASE_URL", "https://images.example/v1")
    monkeypatch.setenv("OPENAI_COMPAT_API_KEY", "image-key")
    monkeypatch.setenv("OPENAI_COMPAT_CHAT_BASE_URL", "https://chat.example/v1")
    monkeypatch.setenv("OPENAI_COMPAT_CHAT_API_KEY", "chat-key")

    assert server._providers_configured() is True


def test_output_file_rejects_sibling_directory_with_shared_prefix(tmp_path, monkeypatch):
    output_dir = tmp_path / "comic_out"
    sibling_dir = tmp_path / "comic_out_backup"
    output_dir.mkdir()
    sibling_dir.mkdir()
    outside = sibling_dir / "secret.png"
    outside.write_bytes(b"not an image")
    monkeypatch.setattr(server, "OUTPUT_DIR", output_dir)

    assert server._is_output_file(str(outside)) is False


def test_path_containment_rejects_shared_prefix_sibling(tmp_path):
    root = tmp_path / "comic_out"
    sibling = tmp_path / "comic_out_backup" / "secret.png"
    root.mkdir()
    sibling.parent.mkdir()
    sibling.write_bytes(b"not an image")

    assert server._is_within(sibling, root) is False


def test_validate_project_id_rejects_traversal():
    try:
        server.validate_project_id("../evil")
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
    assert server.validate_project_id("demo_01") == "demo_01"


def test_apply_review_merge_marks_stale(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "OUTPUT_DIR", tmp_path)
    project_id = "proj1"
    out = tmp_path / project_id
    out.mkdir()
    board = Storyboard(
        chapter_id="0",
        panels=[
            Panel(
                panel_id="p1",
                characters_present=["鸿渐"],
                reference_characters=["鸿渐"],
                action="stand",
            )
        ],
    )
    state = ProjectState(
        project_id=project_id,
        characters={
            "方鸿渐": CharacterAsset(name="方鸿渐"),
            "鸿渐": CharacterAsset(name="鸿渐"),
        },
        chunk_cache={"0": ChunkCache(storyboard=board)},
        panels_done=["c0000-p0000"],
        needs_review=[
            CharacterAliasSuggestion(
                new_name="鸿渐",
                candidate="方鸿渐",
                reason="name variant (normalized/substring match)",
                suggested=True,
            )
        ],
    )
    state.save(out / "state.json")
    (out / "source.txt").write_text("第一章\n鸿渐。", encoding="utf-8")

    result = server.apply_review(project_id, "merge", "鸿渐", "方鸿渐")
    assert "c0000-p0000" in result["stale_panels"]
    assert result["needs_review"] == []
    loaded = ProjectState.load(out / "state.json")
    assert "鸿渐" not in loaded.characters
    assert "鸿渐" in loaded.characters["方鸿渐"].aliases


def test_seed_job_progress_from_checkpoint(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "OUTPUT_DIR", tmp_path)
    project_id = "resume1"
    out = tmp_path / project_id
    out.mkdir()
    board = Storyboard(
        chapter_id="0",
        panels=[Panel(panel_id=str(i), action="a") for i in range(10)],
    )
    state = ProjectState(
        project_id=project_id,
        chunk_cache={"0": ChunkCache(storyboard=board)},
        panels_done=[f"c0000-p{i:04d}" for i in range(5)],
    )
    state.save(out / "state.json")
    progress, stage = server._seed_job_progress(project_id)
    assert stage == "resume"
    assert progress > 0.3


def test_seed_job_timing_from_checkpoint(tmp_path, monkeypatch):
    import web.server as server
    from core.schemas import ProjectState

    monkeypatch.setattr(server, "OUTPUT_DIR", tmp_path)
    project_id = "timing01"
    (tmp_path / project_id).mkdir()
    state = ProjectState(project_id=project_id, active_elapsed_seconds=3600.0)
    state.save(tmp_path / project_id / "state.json")
    assert server._seed_job_timing(project_id) == 3600.0


def test_job_elapsed_adds_session(monkeypatch):

    import web.server as server

    fixed = {"t": 1000.0}
    monkeypatch.setattr(server.time, "monotonic", lambda: fixed["t"])
    job = {"base_elapsed": 100.0, "session_started_at": 1000.0, "status": "running"}
    assert server._job_elapsed(job) == 100.0
    fixed["t"] = 1005.0
    assert server._job_elapsed(job) == 105.0


def test_refresh_job_timing_sets_remaining(monkeypatch):
    import web.server as server

    monkeypatch.setattr(server.time, "monotonic", lambda: 1100.0)
    job = {
        "base_elapsed": 100.0,
        "session_started_at": 1000.0,
        "progress": 0.25,
        "status": "running",
    }
    server._refresh_job_timing(job)
    assert job["elapsed_seconds"] == 200.0
    assert job["remaining_seconds"] == 600.0


def test_persist_active_elapsed(tmp_path, monkeypatch):
    import web.server as server
    from core.schemas import ProjectState

    monkeypatch.setattr(server, "OUTPUT_DIR", tmp_path)
    project_id = "timing02"
    (tmp_path / project_id).mkdir()
    ProjectState(project_id=project_id).save(tmp_path / project_id / "state.json")
    server._persist_active_elapsed(project_id, 42.0)
    # Elapsed time now lives in timing.json, owned solely by the web layer.
    data = json.loads((tmp_path / project_id / "timing.json").read_text(encoding="utf-8"))
    assert data["active_elapsed_seconds"] == 42.0
    # state.json must stay untouched (the pipeline owns it; no more racing writes).
    loaded = ProjectState.load(tmp_path / project_id / "state.json")
    assert not loaded.active_elapsed_seconds  # stays at the default (None/0.0)


def test_seed_job_timing_prefers_timing_json(tmp_path, monkeypatch):
    import web.server as server
    from core.schemas import ProjectState

    monkeypatch.setattr(server, "OUTPUT_DIR", tmp_path)
    project_id = "timing03"
    (tmp_path / project_id).mkdir()
    # Legacy checkpoint field says 3600; the newer timing.json wins.
    ProjectState(project_id=project_id, active_elapsed_seconds=3600.0).save(
        tmp_path / project_id / "state.json"
    )
    server._persist_active_elapsed(project_id, 7200.0)
    assert server._seed_job_timing(project_id) == 7200.0


def test_persist_active_elapsed_never_decreases(tmp_path, monkeypatch):
    import web.server as server

    monkeypatch.setattr(server, "OUTPUT_DIR", tmp_path)
    project_id = "timing04"
    (tmp_path / project_id).mkdir()
    server._persist_active_elapsed(project_id, 100.0)
    server._persist_active_elapsed(project_id, 50.0)
    assert server._seed_job_timing(project_id) == 100.0


def _base_job(**overrides) -> dict:
    job = {
        "status": "running",
        "log": [],
        "error": None,
        "progress": 0.1,
        "stage": "panels",
        "project_id": "p",
        "panels": [],
        "webtoon": None,
        "pdf": None,
        "skipped": [],
        "skipped_chunks": [],
        "needs_review": [],
        "stale_panels": [],
        "pause_reason": None,
        "elapsed_seconds": 1,
        "remaining_seconds": None,
        "base_elapsed": 0,
        "session_started_at": time.monotonic(),
        "cancel_event": threading.Event(),
        "cancel_requested": False,
    }
    job.update(overrides)
    return job


@contextmanager
def _register_job(job_id: str, **overrides):
    with server.JOBS_LOCK:
        server.JOBS[job_id] = _base_job(**overrides)
    try:
        yield server.JOBS[job_id]
    finally:
        with server.JOBS_LOCK:
            server.JOBS.pop(job_id, None)


def test_request_stop_sets_cancel_event_and_flag():
    with _register_job("jobstop1") as job:
        assert server.request_stop("jobstop1") is True
        assert job["cancel_event"].is_set()
        assert job["cancel_requested"] is True


def test_request_stop_unknown_job_returns_false():
    assert server.request_stop("does-not-exist-xyz") is False


@contextmanager
def _running_server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd
    finally:
        httpd.shutdown()
        thread.join(timeout=5)


def _request(port: int, path: str, method: str) -> tuple[int, dict]:
    data = b"{}" if method == "POST" else None
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_post_stop_unknown_job_returns_404():
    with _running_server() as httpd:
        status, payload = _request(httpd.server_port, "/api/job/does-not-exist-xyz/stop", "POST")
    assert status == 404
    assert "error" in payload


def test_post_stop_known_job_returns_ok_and_sets_event():
    with _register_job("jobstop2") as job:
        with _running_server() as httpd:
            status, payload = _request(httpd.server_port, "/api/job/jobstop2/stop", "POST")
        assert status == 200
        assert payload == {"ok": True}
        assert job["cancel_event"].is_set()


def test_job_status_reflects_cancel_requested_without_leaking_event():
    with _register_job("jobstop3"):
        with _running_server() as httpd:
            status, before = _request(httpd.server_port, "/api/job/jobstop3", "GET")
            assert status == 200
            assert before["cancel_requested"] is False

            post_status, _ = _request(httpd.server_port, "/api/job/jobstop3/stop", "POST")
            assert post_status == 200

            status, after = _request(httpd.server_port, "/api/job/jobstop3", "GET")
    assert after["cancel_requested"] is True
    assert "cancel_event" not in after
