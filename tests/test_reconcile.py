"""A resumed project must agree with the files that are actually on disk.

``reconcile_state`` used to be reachable only through a full pipeline run, so the
cases below were covered indirectly or not at all. The extraction makes them
direct: every one of these is a state mutation decided by file existence.

The subtle case is a page whose lettered file is gone but whose blank art
survives. Dropping its done record would repaint it — a billable call — while
re-lettering from the blank costs nothing.
"""

from __future__ import annotations

from pathlib import Path

from core.pipelines.reconcile import (
    is_within,
    reconcile_state,
    soft_invalidate_render,
)
from core.schemas import CharacterAsset, GeneratedPage, GeneratedPanel, ProjectState


def _state(tmp_path: Path) -> tuple[ProjectState, Path]:
    state = ProjectState(project_id="t")
    return state, tmp_path / "state.json"


def _touch(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"png")
    return str(path)


def test_a_panel_record_with_no_file_is_dropped_from_both_lists(tmp_path):
    state, state_path = _state(tmp_path)
    missing = str(tmp_path / "panels" / "gone.png")
    state.generated.panels["c0000-p0000"] = GeneratedPanel(local=missing)
    state.panels_done = ["c0000-p0000"]

    reconcile_state(state, state_path, tmp_path)

    assert "c0000-p0000" not in state.generated.panels
    assert state.panels_done == []


def test_a_panel_file_outside_the_project_is_dropped_even_when_it_exists(tmp_path):
    state, state_path = _state(tmp_path)
    outside = _touch(tmp_path.parent / "elsewhere.png")
    state.generated.panels["c0000-p0000"] = GeneratedPanel(local=outside)
    state.panels_done = ["c0000-p0000"]

    reconcile_state(state, state_path, tmp_path)

    assert state.generated.panels == {}
    assert state.panels_done == []


def test_a_page_that_lost_its_lettered_file_keeps_its_blank(tmp_path):
    """The page is not done, but re-lettering it costs no image call.

    Keeping it in ``pages_done`` would record a finished page with no readable
    file; dropping the projection entry as well would throw away the blank and
    force a repaint. Only the middle answer is right.
    """
    state, state_path = _state(tmp_path)
    blank = _touch(tmp_path / "pages" / "blank" / "page_c0000_p0000.png")
    missing = str(tmp_path / "pages" / "page_c0000_p0000.png")
    state.generated.pages["c0000-p0000"] = GeneratedPage(local=missing, blank_local=blank)
    state.pages_done = ["c0000-p0000"]

    reconcile_state(state, state_path, tmp_path)

    assert "c0000-p0000" in state.generated.pages
    assert state.generated.pages["c0000-p0000"].blank_local == blank
    assert state.pages_done == []


def test_a_page_that_lost_both_files_is_dropped_everywhere(tmp_path):
    state, state_path = _state(tmp_path)
    state.generated.pages["c0000-p0000"] = GeneratedPage(
        local=str(tmp_path / "pages" / "gone.png"),
        blank_local=str(tmp_path / "pages" / "blank" / "gone.png"),
    )
    state.pages_done = ["c0000-p0000"]

    reconcile_state(state, state_path, tmp_path)

    assert state.generated.pages == {}
    assert state.pages_done == []


def test_an_orphan_portrait_is_dropped(tmp_path):
    state, state_path = _state(tmp_path)
    state.generated.portraits["ghost"] = GeneratedPanel(local="/whatever.png")

    reconcile_state(state, state_path, tmp_path)

    assert state.generated.portraits == {}


def test_a_character_without_a_portrait_file_loses_the_path(tmp_path):
    state, state_path = _state(tmp_path)
    state.characters["A"] = CharacterAsset(
        name="A", portrait_local=str(tmp_path / "assets" / "portraits" / "gone.png")
    )

    reconcile_state(state, state_path, tmp_path)

    assert state.characters["A"].portrait_local is None


def test_nothing_changed_means_no_write(tmp_path):
    state, state_path = _state(tmp_path)
    local = _touch(tmp_path / "panels" / "ok.png")
    state.generated.panels["c0000-p0000"] = GeneratedPanel(local=local)
    state.panels_done = ["c0000-p0000"]

    reconcile_state(state, state_path, tmp_path)

    assert not state_path.exists()


def test_soft_invalidation_drops_render_work_and_keeps_plans(tmp_path):
    state, _ = _state(tmp_path)
    state.generated.pages["c0000-p0000"] = GeneratedPage(local=str(tmp_path / "p.png"))
    state.pages_done = ["c0000-p0000"]
    state.panels_done = ["c0000-p0000"]
    state.generated.portraits["A"] = GeneratedPanel(local="/a.png")
    state.characters["A"] = CharacterAsset(name="A", portrait_local="/a.png")
    state.skipped_pages = ["c0000-p0009"]

    soft_invalidate_render(state)

    assert state.generated.pages == {}
    assert state.pages_done == []
    assert state.panels_done == []
    assert state.generated.portraits == {}
    assert state.characters["A"].portrait_local is None
    # A policy rejection is not re-attempted: the source did not change.
    assert state.skipped_pages == ["c0000-p0009"]


def test_is_within_rejects_a_sibling_with_a_shared_prefix(tmp_path):
    root = tmp_path / "comic_out"
    root.mkdir()
    sibling = tmp_path / "comic_out_backup" / "x.png"

    assert is_within(sibling, root) is False
    assert is_within(root / "pages" / "x.png", root) is True
