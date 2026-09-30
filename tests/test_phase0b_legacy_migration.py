"""Phase 0b: pre-0b ``c{ci:04d}:{page_id}`` keys migrate to positional keys."""

import asyncio
import json
from unittest.mock import patch

import web.server as server
from core.pipelines.creative_comic import creative_comic
from core.schemas import (
    ComicPagePlan,
    ComicPagePlanSet,
    GeneratedAssets,
    GeneratedPage,
    ProjectState,
    Tombstone,
)
from tests.test_finished_page_pipeline import FakeChat, FakeImage, _fake_export_pdf


def _legacy_state() -> ProjectState:
    pages = ComicPagePlanSet(
        unit_id="1",
        pages=[
            ComicPagePlan.model_validate(
                {"page_id": "u1_p0001", "panels": [{"panel_id": "1", "action": "a"}]}
            ),
            ComicPagePlan.model_validate(
                {"page_id": "u1_p0002", "panels": [{"panel_id": "2", "action": "b"}]}
            ),
        ],
    )
    return ProjectState(
        project_id="t",
        render_mode="finished_page",
        page_cache={"0": pages},
        pages_done=["c0000:u1_p0001"],
        stale_pages=["c0000:u1_p0002"],
        skipped_pages=["c0000:u1_p0002"],
        generated=GeneratedAssets(
            pages={"c0000:u1_p0001": GeneratedPage(local="/tmp/a.png", page_id="u1_p0001")}
        ),
        tombstones={
            "c0000:u1_p0002": Tombstone(
                outcome="rejected", reason="content_policy", stage="render.page"
            )
        },
    )


def test_migration_rewrites_all_five_key_maps():
    state = _legacy_state()
    changed = state.migrate_legacy_page_keys()

    assert changed is True
    assert state.pages_done == ["c0000-p0000"]
    assert state.stale_pages == ["c0000-p0001"]
    assert state.skipped_pages == ["c0000-p0001"]
    assert set(state.generated.pages) == {"c0000-p0000"}
    assert state.generated.pages["c0000-p0000"].page_id == "u1_p0001"
    assert set(state.tombstones) == {"c0000-p0001"}


def test_migration_is_idempotent():
    state = _legacy_state()
    assert state.migrate_legacy_page_keys() is True
    assert state.migrate_legacy_page_keys() is False
    assert state.pages_done == ["c0000-p0000"]


def test_migration_leaves_new_format_and_unknown_keys_untouched():
    state = ProjectState(
        project_id="t",
        pages_done=["c0000-p0000"],
        stale_pages=["c0009:not_in_cache"],
    )

    assert state.migrate_legacy_page_keys() is False
    assert state.pages_done == ["c0000-p0000"]
    assert state.stale_pages == ["c0009:not_in_cache"]


def test_web_load_project_state_migrates_legacy_keys(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "OUTPUT_DIR", tmp_path)
    out = tmp_path / "legacy1"
    out.mkdir()
    (out / "state.json").write_text(_legacy_state().model_dump_json(), encoding="utf-8")

    _out_dir, loaded = server._load_project_state("legacy1")

    assert loaded.pages_done == ["c0000-p0000"]
    assert all(":" not in k for k in loaded.generated.pages)


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_resume_of_legacy_state_does_not_repaint(tmp_path, monkeypatch):
    """0b quota risk is none: a pre-0b checkpoint must not trigger a repaint."""
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "finished_page")
    src = "第一章\n福贵在村口。"

    first = FakeImage()
    asyncio.run(creative_comic(src, output_dir=str(tmp_path), chat=FakeChat(), image=first))
    assert first.calls == 2  # 1 portrait + 1 page

    # Rewrite the checkpoint to the pre-0b colon format with a renamed page_id,
    # simulating a project created before 0b and replanned since.
    raw = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    raw["pages_done"] = ["c0000:u1_p0001"]
    raw["generated"]["pages"] = {"c0000:u1_p0001": raw["generated"]["pages"]["c0000-p0000"]}
    (tmp_path / "state.json").write_text(json.dumps(raw), encoding="utf-8")

    second = FakeImage()
    resumed = asyncio.run(
        creative_comic(src, output_dir=str(tmp_path), chat=FakeChat(), image=second)
    )

    assert second.calls == 0  # page kept, no repaint
    assert resumed.state.pages_done == ["c0000-p0000"]
