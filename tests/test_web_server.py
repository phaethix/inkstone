"""Web server configuration, project helpers, and artifact-boundary tests."""

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
    loaded = ProjectState.load(tmp_path / project_id / "state.json")
    assert loaded.active_elapsed_seconds == 42.0
