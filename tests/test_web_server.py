"""Web server configuration, project helpers, and artifact-boundary tests."""

import json
import os
import shutil
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path

from core.comic.ledger import ConsistencyLedger, LedgerEntry, ReferenceVersion
from core.harness.spec import DATA_DIR
from core.schemas import (
    CharacterAliasSuggestion,
    CharacterAsset,
    ChunkCache,
    ComicPagePlan,
    ComicPagePlanSet,
    PagePanelSpec,
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
    assert [(s.new_name, s.candidate) for s in loaded.merged_aliases] == [("鸿渐", "方鸿渐")]


def test_finished_page_merge_marks_stale_pages_and_regen_accepts_them(tmp_path, monkeypatch):
    """Default finished_page mode: an alias merge must surface redrawable pages.

    ``merge_character_alias`` records finished-page invalidation in
    ``stale_pages``, not ``stale_panels``. The review snapshot and the
    ``stale=True`` regen endpoint must understand both, or "redraw affected"
    is unreachable in the default render mode.
    """
    monkeypatch.setattr(server, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(server, "_start_job", lambda *a, **k: ("job1", "proj_page"))
    project_id = "proj_page"
    out = tmp_path / project_id
    out.mkdir()
    (out / "source.txt").write_text("第一章\n福贵。", encoding="utf-8")
    page = ComicPagePlan(
        page_id="u1_p0001",
        reference_characters=["福贵"],
        panels=[PagePanelSpec(panel_id="p1", characters=["福贵"])],
    )
    state = ProjectState(
        project_id=project_id,
        render_mode="finished_page",
        characters={
            "徐福贵": CharacterAsset(name="徐福贵"),
            "福贵": CharacterAsset(name="福贵"),
        },
        page_cache={"0": ComicPagePlanSet(unit_id="u1", pages=[page])},
        pages_done=["c0000-p0000"],
        needs_review=[
            CharacterAliasSuggestion(
                new_name="福贵",
                candidate="徐福贵",
                reason="name variant",
                suggested=True,
            )
        ],
    )
    state.save(out / "state.json")

    snapshot = server.apply_review(project_id, "merge", "福贵", "徐福贵")
    assert "c0000-p0000" in snapshot["stale_pages"]
    assert snapshot["stale_panels"] == []

    job_id, _pid = server.start_regen_job(project_id, stale=True)
    assert job_id == "job1"


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
        render_mode="panel_compose",
        chunk_cache={"0": ChunkCache(storyboard=board)},
        panels_done=[f"c0000-p{i:04d}" for i in range(5)],
    )
    state.save(out / "state.json")
    progress, stage = server._seed_job_progress(project_id)
    assert stage == "resume"
    assert progress > 0.3


def test_seed_job_progress_survives_a_corrupt_cache_file(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "OUTPUT_DIR", tmp_path)
    project_id = "resume-cache-bad"
    out = tmp_path / project_id
    out.mkdir()
    board = Storyboard(
        chapter_id="0",
        panels=[Panel(panel_id=str(i), action="a") for i in range(10)],
    )
    state = ProjectState(
        project_id=project_id,
        render_mode="panel_compose",
        chunk_cache={"0": ChunkCache(storyboard=board)},
        panels_done=[f"c0000-p{i:04d}" for i in range(5)],
    )
    state.save(out / "state.json")
    (out / "cache.json").write_text("{not json", encoding="utf-8")

    # The seeding reader falls back to the projection-only load: the estimate
    # gets rougher but a corrupt cache file must not reset the bar to zero.
    progress, stage = server._seed_job_progress(project_id)
    assert stage == "resume"
    assert progress > 0.3


def test_list_projects_survives_a_corrupt_cache_file(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "OUTPUT_DIR", tmp_path)
    project_id = "listing-cache-bad"
    out = tmp_path / project_id
    out.mkdir()
    ProjectState(project_id=project_id).save(out / "state.json")
    (out / "cache.json").write_text("{not json", encoding="utf-8")

    projects = server.list_projects()
    assert [p["id"] for p in projects] == [project_id]


def test_start_job_seeds_resume_fields_from_checkpoint(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(
        server.threading,
        "Thread",
        lambda *args, **kwargs: type("NoopThread", (), {"start": lambda self: None})(),
    )
    project_id = "resume2"
    out = tmp_path / project_id
    out.mkdir()
    ProjectState(
        project_id=project_id,
        render_mode="panel_compose",
        pages_done=["p0001", "p0002"],
        skipped_pages=["p0003"],
    ).save(out / "state.json")
    job_id, _ = server._start_job("text", "page", None, project_id=project_id)
    try:
        job = server.JOBS[job_id]
        assert job["render_mode"] == "panel_compose"
        assert job["pages_done"] == ["p0001", "p0002"]
        assert job["skipped_pages"] == ["p0003"]
        assert job["stage"] == "resume"
    finally:
        with server.JOBS_LOCK:
            server.JOBS.pop(job_id, None)


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
        "render_mode": "finished_page",
        "pages_done": [],
        "skipped_pages": [],
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
            server.JOB_TOMBSTONES.pop(job_id, None)


def test_request_stop_sets_cancel_event_and_flag():
    with _register_job("jobstop1") as job:
        ok, error = server.request_stop("jobstop1")
        assert ok is True
        assert error is None
        assert job["cancel_event"].is_set()
        assert job["cancel_requested"] is True


def test_request_stop_unknown_job_returns_false():
    ok, error = server.request_stop("does-not-exist-xyz")
    assert ok is False
    assert error == "unknown"


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


def _request(
    port: int,
    path: str,
    method: str,
    *,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict]:
    body = data if data is not None else (b"{}" if method == "POST" else None)
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=body, method=method)
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_post_stop_unknown_job_returns_404():
    with _running_server() as httpd:
        status, payload = _request(httpd.server_port, "/api/job/does-not-exist-xyz/stop", "POST")
    assert status == 404
    assert "unknown" in payload["error"].lower() or "not found" in payload["error"].lower()


def test_post_rejects_cross_site_origin():
    with _running_server() as httpd:
        status, payload = _request(
            httpd.server_port,
            "/api/job/x/stop",
            "POST",
            headers={"Origin": "https://evil.example"},
        )
    assert status == 403
    assert "origin" in payload["error"].lower() or "cross" in payload["error"].lower()


def test_post_rejects_sec_fetch_site_cross_site():
    with _running_server() as httpd:
        status, payload = _request(
            httpd.server_port,
            "/api/job/x/stop",
            "POST",
            headers={"Sec-Fetch-Site": "cross-site"},
        )
    assert status == 403


def test_post_allows_missing_origin_for_local_tools():
    """curl / scripts omit Origin — still allowed on loopback UI."""
    with _running_server() as httpd:
        status, payload = _request(httpd.server_port, "/api/job/x/stop", "POST")
    assert status == 404


def test_post_rejects_oversized_body(monkeypatch):
    monkeypatch.setattr(server, "MAX_JSON_BODY_BYTES", 64)
    with _running_server() as httpd:
        status, payload = _request(
            httpd.server_port,
            "/api/job/x/stop",
            "POST",
            data=b'{"x":"' + b"a" * 200 + b'"}',
            headers={"Content-Type": "application/json"},
        )
    assert status == 413
    assert "too large" in payload["error"].lower() or "body" in payload["error"].lower()


def test_load_dotenv_still_loads_other_keys_when_agnes_key_set(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "AGNES_API_KEY=from-file\nINKSTONE_FONT_PATH=/tmp/font.ttf\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(server, "ROOT", tmp_path)
    monkeypatch.setenv("AGNES_API_KEY", "already-set")
    monkeypatch.delenv("INKSTONE_FONT_PATH", raising=False)
    server._load_dotenv()
    assert os.environ["AGNES_API_KEY"] == "already-set"
    assert os.environ["INKSTONE_FONT_PATH"] == "/tmp/font.ttf"


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


def test_append_job_log_caps_stored_lines():
    job = {"log": []}
    for i in range(600):
        server._append_job_log(job, f"line-{i}")
    assert len(job["log"]) == server._JOB_LOG_MAX_LINES
    assert job["log"][0] == "line-100"
    assert job["log"][-1] == "line-599"


def test_finished_job_expired_returns_410(monkeypatch):
    monkeypatch.setenv("INKSTONE_JOB_TTL_SECONDS", "3600")
    with _register_job(
        "jobexp1",
        status="done",
        finished_at=time.time() - 5000,
    ):
        with _running_server() as httpd:
            status, payload = _request(httpd.server_port, "/api/job/jobexp1", "GET")
    assert status == 410
    assert "expired" in payload["error"].lower()


def test_finished_job_within_ttl_returns_200(monkeypatch):
    monkeypatch.setenv("INKSTONE_JOB_TTL_SECONDS", "3600")
    with _register_job(
        "jobok1",
        status="done",
        finished_at=time.time() - 60,
    ):
        with _running_server() as httpd:
            status, payload = _request(httpd.server_port, "/api/job/jobok1", "GET")
    assert status == 200
    assert payload["status"] == "done"


def test_start_job_purges_expired_jobs(monkeypatch):
    monkeypatch.setenv("INKSTONE_JOB_TTL_SECONDS", "3600")
    monkeypatch.setattr(
        server.threading,
        "Thread",
        lambda *args, **kwargs: type("NoopThread", (), {"start": lambda self: None})(),
    )
    with _register_job(
        "jobpurge1",
        status="done",
        finished_at=time.time() - 5000,
    ):
        assert "jobpurge1" in server.JOBS
        server._start_job("text", "page", None, project_id="purgeproj")
        assert "jobpurge1" not in server.JOBS
        assert "jobpurge1" in server.JOB_TOMBSTONES
    with server.JOBS_LOCK:
        server.JOBS.clear()
        server.JOB_TOMBSTONES.clear()


def test_purged_job_returns_410_via_tombstone(monkeypatch):
    monkeypatch.setenv("INKSTONE_JOB_TTL_SECONDS", "3600")
    with _register_job(
        "jobtomb1",
        status="done",
        finished_at=time.time() - 5000,
    ):
        with server.JOBS_LOCK:
            server._purge_expired_jobs_locked()
        assert "jobtomb1" not in server.JOBS
        assert "jobtomb1" in server.JOB_TOMBSTONES
        with _running_server() as httpd:
            status, payload = _request(httpd.server_port, "/api/job/jobtomb1", "GET")
    assert status == 410
    assert "expired" in payload["error"].lower()


def test_purged_job_returns_404_after_tombstone_ttl(monkeypatch):
    monkeypatch.setenv("INKSTONE_JOB_TTL_SECONDS", "1")
    with _register_job(
        "jobtomb2",
        status="done",
        finished_at=time.time() - 10,
    ):
        with server.JOBS_LOCK:
            server._purge_expired_jobs_locked()
            server.JOB_TOMBSTONES["jobtomb2"] = time.time() - 10
        with _running_server() as httpd:
            status, payload = _request(httpd.server_port, "/api/job/jobtomb2", "GET")
    assert status == 404
    assert "unknown" in payload["error"].lower()


def test_list_projects_from_disk(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "OUTPUT_DIR", tmp_path)
    for project_id, stage, panels in [
        ("alpha", "panels", ["c0000-p0000"]),
        ("beta", "extract", []),
    ]:
        out = tmp_path / project_id
        out.mkdir()
        kwargs = {
            "project_id": project_id,
            "stage": stage,
            "panels_done": panels,
        }
        if project_id == "alpha":
            kwargs["pages_done"] = ["p0001"]
            kwargs["skipped_pages"] = ["p0002"]
        ProjectState(**kwargs).save(out / "state.json")
    (tmp_path / "alpha" / "timing.json").write_text(
        '{"active_elapsed_seconds": 10}', encoding="utf-8"
    )
    (tmp_path / "no_state").mkdir()

    projects = server.list_projects()
    assert len(projects) == 2
    by_id = {p["id"]: p for p in projects}
    assert by_id["alpha"]["stage"] == "panels"
    assert by_id["alpha"]["panels_done"] == ["c0000-p0000"]
    assert by_id["alpha"]["render_mode"] == "finished_page"
    assert by_id["alpha"]["pages_done"] == ["p0001"]
    assert by_id["alpha"]["skipped_pages"] == ["p0002"]
    assert by_id["alpha"]["has_timing"] is True
    assert by_id["beta"]["has_timing"] is False


def test_get_projects_endpoint(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "OUTPUT_DIR", tmp_path)
    out = tmp_path / "webproj"
    out.mkdir()
    ProjectState(
        project_id="webproj",
        stage="export",
        render_mode="finished_page",
        panels_done=["c0000-p0001"],
        pages_done=["p0001"],
    ).save(out / "state.json")
    with _running_server() as httpd:
        status, payload = _request(httpd.server_port, "/api/projects", "GET")
    assert status == 200
    assert payload == [
        {
            "id": "webproj",
            "stage": "export",
            "panels_done": ["c0000-p0001"],
            "render_mode": "finished_page",
            "pages_done": ["p0001"],
            "skipped_pages": [],
            "has_timing": False,
        }
    ]


def test_opening_the_reader_writes_it_from_stored_pages(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "OUTPUT_DIR", tmp_path)
    pages = tmp_path / "demo" / "pages"
    pages.mkdir(parents=True)
    (pages / "page_c0000_p0001.png").write_bytes(b"\x89PNG")
    (pages / "page_c0000_p0000.png").write_bytes(b"\x89PNG")
    (pages / "webtoon.png").write_bytes(b"\x89PNG")
    assert server._reader_url("demo") == "/files/demo/reader.html"
    assert not (tmp_path / "demo" / "reader.html").exists()
    with _running_server() as httpd:
        req = urllib.request.Request(f"http://127.0.0.1:{httpd.server_port}/files/demo/reader.html")
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = resp.read().decode("utf-8")
            status = resp.status
    assert status == 200
    assert body.index("pages/page_c0000_p0000.png") < body.index("pages/page_c0000_p0001.png")
    assert "webtoon.png" not in body
    empty = tmp_path / "empty"
    empty.mkdir()
    assert server._reader_url("empty") is None
    with _running_server() as httpd:
        req = urllib.request.Request(
            f"http://127.0.0.1:{httpd.server_port}/files/empty/reader.html"
        )
        try:
            urllib.request.urlopen(req, timeout=5)
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
        else:
            raise AssertionError("missing pages should not serve a reader")
    assert not (empty / "reader.html").exists()


def _seed_review_project(tmp_path, project_id="demo"):
    out = tmp_path / project_id
    out.mkdir()
    ProjectState(project_id=project_id).save(out / "state.json")
    ledger = ConsistencyLedger(
        characters={
            "福贵": LedgerEntry(
                pages=["c0000-p0000", "c0000-p0001"],
                reference=ReferenceVersion(path="assets/portraits/福贵.png", version=2),
                pending_pages=["c0000-p0000", "c0000-p0001"],
                reviewed_version=1,
            )
        }
    )
    ledger.save(out / "consistency.json")
    return out


def test_snapshot_carries_the_ledger_escalation_threshold(tmp_path):
    out = _seed_review_project(tmp_path)
    state = ProjectState.load(out / "state.json")
    snapshot = server._state_snapshot(state, out)
    assert snapshot["ledger_escalation_threshold"] == 30
    assert snapshot["ledger_pending"] == {"福贵": ["c0000-p0000", "c0000-p0001"]}


def test_post_ledger_confirm_clears_pending(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "OUTPUT_DIR", tmp_path)
    out = _seed_review_project(tmp_path)
    with _running_server() as httpd:
        status, payload = _request(
            httpd.server_port,
            "/api/project/demo/ledger/confirm",
            "POST",
            data=json.dumps({"character": "福贵"}).encode(),
        )
    assert status == 200
    assert payload["ledger_pending"] == {}
    persisted = ConsistencyLedger.load(out / "consistency.json")
    assert persisted.characters["福贵"].reviewed_version == 2
    assert persisted.characters["福贵"].pending_pages == []


def test_post_ledger_confirm_unknown_character_404(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "OUTPUT_DIR", tmp_path)
    _seed_review_project(tmp_path)
    with _running_server() as httpd:
        status, payload = _request(
            httpd.server_port,
            "/api/project/demo/ledger/confirm",
            "POST",
            data=json.dumps({"character": "nobody"}).encode(),
        )
    assert status == 404
    assert "unknown character" in payload["error"]


def test_index_html_has_the_continuity_panel():
    html = (Path(server.__file__).resolve().parent / "index.html").read_text(encoding="utf-8")
    assert 'id="ledgerWrap"' in html
    assert 'id="ledgerList"' in html
    assert "/ledger/confirm" in html


def _make_experiment(root: Path, name: str = "exp1") -> Path:
    experiment = root / name
    for anon in ("A", "B"):
        candidate = experiment / "candidates" / anon
        candidate.mkdir(parents=True)
        (candidate / "plan.json").write_text(
            json.dumps({"unit_id": "0", "pages": [{"page_id": f"p{anon}"}]}),
            encoding="utf-8",
        )
        (candidate / "structure_report.json").write_text(
            json.dumps({"ok": True, "checks": []}), encoding="utf-8"
        )
    (experiment / "experiment.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "status": "awaiting_selection",
                "created_at": "2026-10-20T00:00:00+00:00",
                "chunk_index": 0,
                "genre": "wuxia",
                "candidates": {
                    "A": {
                        "label": "base",
                        "harness_digest": "base",
                        "chat_calls": 1,
                        "prompt_digest": "sha256:a",
                    },
                    "B": {
                        "label": "candidate",
                        "harness_digest": "sha256:b",
                        "chat_calls": 1,
                        "prompt_digest": "sha256:b",
                    },
                },
                "cost": {"chat_calls": 4, "image_calls": 0},
            }
        ),
        encoding="utf-8",
    )
    return experiment


def test_experiment_list_hides_labels_until_selection(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "EXPERIMENTS_DIR", tmp_path)
    _make_experiment(tmp_path)
    with _running_server() as httpd:
        status, payload = _request(httpd.server_port, "/api/experiments", "GET")
        detail_status, detail = _request(httpd.server_port, "/api/experiment/exp1", "GET")
    assert status == 200
    assert payload[0]["id"] == "exp1"
    assert payload[0]["candidates"] == ["A", "B"]
    assert "chosen_label" not in payload[0] or payload[0]["chosen_label"] is None
    assert detail_status == 200
    assert set(detail["candidates"]) == {"A", "B"}
    assert all("label" not in item for item in detail["candidates"].values())
    assert detail["candidates"]["A"]["plan"]["pages"]


def test_experiment_pick_records_selection_and_reveals_labels(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "EXPERIMENTS_DIR", tmp_path)
    _make_experiment(tmp_path)
    body = json.dumps({"choice": "A", "why": "更清楚的空间建立"}).encode()
    with _running_server() as httpd:
        status, selection = _request(
            httpd.server_port, "/api/experiment/exp1/pick", "POST", data=body
        )
        detail_status, detail = _request(httpd.server_port, "/api/experiment/exp1", "GET")
    assert status == 200
    assert selection["chosen"] == "A"
    assert selection["chosen_label"] == "base"
    assert detail_status == 200
    assert detail["candidates"]["A"]["label"] == "base"
    assert detail["selection"]["why"] == "更清楚的空间建立"


def test_experiment_pick_requires_why(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "EXPERIMENTS_DIR", tmp_path)
    _make_experiment(tmp_path)
    body = json.dumps({"choice": "A"}).encode()
    with _running_server() as httpd:
        status, payload = _request(
            httpd.server_port, "/api/experiment/exp1/pick", "POST", data=body
        )
    assert status == 400
    assert "choice/why" in payload["error"]


def test_experiment_id_traversal_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "EXPERIMENTS_DIR", tmp_path)
    with _running_server() as httpd:
        status, payload = _request(httpd.server_port, "/api/experiment/..%2Fsecret", "GET")
    assert status in (400, 404)


def test_experiment_compose_after_pick(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "EXPERIMENTS_DIR", tmp_path)
    _make_experiment(tmp_path)
    with _running_server() as httpd:
        before, payload = _request(
            httpd.server_port, "/api/experiment/exp1/compose", "POST", data=b"{}"
        )
        assert before == 400
        assert "pick a candidate" in payload["error"]

        pick = json.dumps({"choice": "A", "why": "更清楚的空间"}).encode()
        _request(httpd.server_port, "/api/experiment/exp1/pick", "POST", data=pick)
        status, record = _request(
            httpd.server_port, "/api/experiment/exp1/compose", "POST", data=b"{}"
        )
        detail_status, detail = _request(httpd.server_port, "/api/experiment/exp1", "GET")
        again, payload = _request(
            httpd.server_port, "/api/experiment/exp1/compose", "POST", data=b"{}"
        )
    assert status == 200
    assert record["harness_digest"].startswith("sha256:")
    assert (tmp_path / "exp1" / "adopted" / "manifest.json").is_file()
    assert detail_status == 200
    assert detail["composed"]["harness_digest"] == record["harness_digest"]
    assert again == 409
    assert "already exists" in payload["error"]


def test_experiment_compose_can_merge_page_plan_from_other_candidate(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "EXPERIMENTS_DIR", tmp_path)
    experiment = _make_experiment(tmp_path)
    candidate = tmp_path / "candidate-harness"
    shutil.copytree(DATA_DIR, candidate)
    page_plan = candidate / "stages" / "page_plan.md"
    page_plan.write_text(page_plan.read_text(encoding="utf-8") + "\nWEB MERGE MARKER\n")
    manifest = json.loads((experiment / "experiment.json").read_text(encoding="utf-8"))
    manifest["candidates"]["B"]["label"] = str(candidate)
    (experiment / "experiment.json").write_text(json.dumps(manifest), encoding="utf-8")

    with _running_server() as httpd:
        pick = json.dumps({"choice": "A", "why": "base is safer"}).encode()
        _request(httpd.server_port, "/api/experiment/exp1/pick", "POST", data=pick)
        body = json.dumps({"merge_from": "B"}).encode()
        status, record = _request(
            httpd.server_port, "/api/experiment/exp1/compose", "POST", data=body
        )
    assert status == 200
    assert record["components"] == ["stage.page_plan"]
    adopted_plan = (tmp_path / "exp1" / "adopted" / "stages" / "page_plan.md").read_text()
    assert "WEB MERGE MARKER" in adopted_plan


def test_sequence_apis_list_detail_pick_and_advance(tmp_path, monkeypatch):
    import asyncio

    from core.experiment import plan_sequence_async
    from core.harness.spec import DATA_DIR
    from tests.test_finished_page_pipeline import FakeChat

    monkeypatch.setattr(server, "EXPERIMENTS_DIR", tmp_path)
    candidate = tmp_path / "candidate"
    shutil.copytree(DATA_DIR, candidate)
    sequence_dir = tmp_path / "seq"
    asyncio.run(
        plan_sequence_async(
            "第一章\n福贵在村口。",
            out_dir=sequence_dir,
            candidates=[str(candidate)],
            rounds=1,
            chat=FakeChat(),
        )
    )

    with _running_server() as httpd:
        status, rows = _request(httpd.server_port, "/api/sequences", "GET")
        detail_status, detail = _request(httpd.server_port, "/api/sequence/seq", "GET")
        assert status == 200
        assert rows[0]["id"] == "seq"
        assert rows[0]["status"] == "awaiting_pick"
        assert detail_status == 200
        assert detail["current_round"] == 1
        assert set(detail["round_detail"]["candidates"]) == {"A", "B"}
        assert all("label" not in item for item in detail["round_detail"]["candidates"].values())

        pick = json.dumps({"choice": "A", "why": "更清楚的构图"}).encode()
        pick_status, selection = _request(
            httpd.server_port, "/api/sequence/seq/pick", "POST", data=pick
        )
        assert pick_status == 200
        assert selection["chosen"] == "A"

        advance_status, after = _request(
            httpd.server_port, "/api/sequence/seq/advance", "POST", data=b"{}"
        )
    assert advance_status == 200
    assert after["status"] == "done"
    assert after["round_detail"]["checkpoint"]
    assert (sequence_dir / "rounds" / "1" / "checkpoint" / "manifest.json").is_file()


def test_sequence_pick_requires_why(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "EXPERIMENTS_DIR", tmp_path)
    sequence = tmp_path / "seq"
    (sequence / "rounds" / "1").mkdir(parents=True)
    (sequence / "sequence.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "status": "awaiting_pick",
                "rounds": 1,
                "current_round": 1,
            }
        ),
        encoding="utf-8",
    )
    with _running_server() as httpd:
        status, payload = _request(
            httpd.server_port,
            "/api/sequence/seq/pick",
            "POST",
            data=json.dumps({"choice": "A"}).encode(),
        )
    assert status == 400
    assert "choice/why" in payload["error"]


def test_apply_review_unknown_keep_is_a_value_error(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "OUTPUT_DIR", tmp_path)
    project_id = "proj1"
    out = tmp_path / project_id
    out.mkdir()
    state = ProjectState(
        project_id=project_id,
        characters={"鸿渐": CharacterAsset(name="鸿渐")},
    )
    state.save(out / "state.json")

    try:
        server.apply_review(project_id, "merge", "鸿渐", "方鸿渐")
    except ValueError as exc:
        assert "keep character not found" in str(exc)
    else:
        raise AssertionError("unknown keep name must not become a 500")


def test_post_ledger_confirm_accepts_a_page_subset(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "OUTPUT_DIR", tmp_path)
    out = _seed_review_project(tmp_path)
    with _running_server() as httpd:
        status, payload = _request(
            httpd.server_port,
            "/api/project/demo/ledger/confirm",
            "POST",
            data=json.dumps({"character": "福贵", "pages": ["c0000-p0000"]}).encode(),
        )
    assert status == 200
    assert payload["ledger_pending"] == {"福贵": ["c0000-p0001"]}
    persisted = ConsistencyLedger.load(out / "consistency.json").characters["福贵"]
    assert persisted.reviewed_pages == ["c0000-p0000"]
    assert persisted.pending_pages == ["c0000-p0001"]


def test_post_ledger_confirm_rejects_unknown_pages(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "OUTPUT_DIR", tmp_path)
    _seed_review_project(tmp_path)
    with _running_server() as httpd:
        status, payload = _request(
            httpd.server_port,
            "/api/project/demo/ledger/confirm",
            "POST",
            data=json.dumps({"character": "福贵", "pages": ["c9999-p9999"]}).encode(),
        )
    assert status == 400
    assert "belong" in payload["error"]
