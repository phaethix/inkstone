"""The skip/redo rule is asymmetric, and these pin the asymmetry.

``panel_needs_generation`` and ``page_needs_generation`` were four copies of the
same three-way branch inside the pipeline body, reachable only through a full
run. Two of the rules are easy to get backwards and expensive when wrong:

- stale must beat done, or an alias merge stops redrawing anything;
- skipped must not be retried, or every resume re-spends quota on a page the
  provider already rejected for its source text.
"""

from __future__ import annotations

from core.pipelines.resume import (
    drop_pages_outside_plan,
    page_needs_generation,
    panel_needs_generation,
    stamp_action_key,
)
from core.schemas import GeneratedPage, ProjectState


def test_a_stale_panel_is_redrawn_even_though_it_is_recorded_done():
    state = ProjectState(project_id="t")
    state.panels_done = ["c0000-p0000"]
    state.stale_panels = ["c0000-p0000"]

    assert panel_needs_generation(state, "c0000-p0000") is True


def test_a_done_panel_is_skipped():
    state = ProjectState(project_id="t")
    state.panels_done = ["c0000-p0000"]

    assert panel_needs_generation(state, "c0000-p0000") is False


def test_a_policy_skipped_panel_is_not_retried():
    state = ProjectState(project_id="t")
    state.skipped = ["c0000-p0000"]

    assert panel_needs_generation(state, "c0000-p0000") is False


def test_an_unrecorded_panel_is_drawn():
    assert panel_needs_generation(ProjectState(project_id="t"), "c0000-p0000") is True


def test_the_page_rule_matches_the_panel_rule():
    state = ProjectState(project_id="t")
    state.pages_done = ["c0000-p0000"]
    state.skipped_pages = ["c0000-p0001"]
    state.stale_pages = ["c0000-p0002"]

    assert page_needs_generation(state, "c0000-p0000") is False
    assert page_needs_generation(state, "c0000-p0001") is False
    assert page_needs_generation(state, "c0000-p0002") is True
    assert page_needs_generation(state, "c0000-p0003") is True


def test_a_shorter_plan_drops_only_its_own_out_of_range_pages():
    """Another chunk's records and a human decision must survive the trim."""
    state = ProjectState(project_id="t")
    for key in ("c0000-p0000", "c0000-p0001", "c0000-p0002", "c0001-p0007"):
        state.generated.pages[key] = GeneratedPage(local=f"pages/{key}.png")
    state.pages_done = list(state.generated.pages)
    state.stale_pages = ["c0000-p0002"]

    drop_pages_outside_plan(state, chunk_index=0, page_count=2)

    assert "c0000-p0002" not in state.generated.pages
    assert "c0000-p0002" not in state.pages_done
    assert "c0000-p0001" in state.generated.pages
    # A different chunk is not this chunk's plan.
    assert "c0001-p0007" in state.generated.pages


def test_dropping_a_page_deletes_its_lettered_file_and_blank(tmp_path):
    pages = tmp_path / "pages"
    blank_dir = pages / "blank"
    blank_dir.mkdir(parents=True)
    lettered = pages / "page_c0000_p0002.png"
    blank = blank_dir / "page_c0000_p0002.png"
    kept = pages / "page_c0000_p0000.png"
    lettered.write_bytes(b"a")
    blank.write_bytes(b"b")
    kept.write_bytes(b"c")
    state = ProjectState(project_id="t")
    state.generated.pages["c0000-p0002"] = GeneratedPage(
        local=str(lettered), blank_local=str(blank)
    )
    state.generated.pages["c0000-p0000"] = GeneratedPage(local=str(kept))
    state.pages_done = ["c0000-p0000", "c0000-p0002"]

    drop_pages_outside_plan(state, chunk_index=0, page_count=1, pages_dir=pages)

    assert not lettered.exists()
    assert not blank.exists()
    assert kept.exists()
    assert "c0000-p0002" not in state.generated.pages
    assert "c0000-p0000" in state.generated.pages


def test_stamping_a_missing_key_persists_immediately(tmp_path):
    state = ProjectState(project_id="t")
    state_path = tmp_path / "state.json"

    class Target:
        action_key = ""

    target = Target()
    assert stamp_action_key(state, state_path, target, "sha256:abc") is True
    assert target.action_key == "sha256:abc"
    # The stamp must survive a pause or a crash, so the write happens here.
    assert state_path.is_file()

    # A stamp that already exists is not rewritten.
    assert stamp_action_key(state, state_path, target, "sha256:def") is False
    assert target.action_key == "sha256:abc"
