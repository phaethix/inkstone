"""Phase 0b: pre-0b ``c{ci:04d}:{page_id}`` keys migrate to positional keys."""

from core.schemas import (
    ComicPagePlan,
    ComicPagePlanSet,
    GeneratedAssets,
    GeneratedPage,
    ProjectState,
    Tombstone,
)


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