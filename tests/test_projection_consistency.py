"""The finished-page projection must agree with the checkpoint it derives from.

"This page is done" is stored twice in one document — ``pages_done`` and
``generated.pages``, written together by ``record_page_artifact`` — and a third
time as a ``render.page`` manifest. Resume reads all of them, so a disagreement
has no defined winner: one branch repaints the page for pay, another skips it.

``check_page_projection`` compares the first two, which need no key derivation.
These tests pin the three answers it is allowed to give: pass, absent (nothing to
compare), and mismatch.
"""

from __future__ import annotations

from core.cli import _build_parser, _run_verify
from core.comic.artifact_projection import (
    ABSENT,
    MISMATCH,
    PASS,
    check_page_projection,
    projection_problems,
    record_page_artifact,
)
from core.schemas import GeneratedPage, ProjectState, Tombstone


def _state_with_pages(*keys: str) -> ProjectState:
    state = ProjectState(project_id="t")
    for key in keys:
        record_page_artifact(state, key, GeneratedPage(local=f"pages/{key}.png"))
    return state


def _status(checks, name: str) -> str:
    return next(check.status for check in checks if check.name == name)


def test_a_page_recorded_through_the_projection_passes_every_check():
    checks = check_page_projection(_state_with_pages("c0000-p0000", "c0000-p0001"))

    assert {check.status for check in checks} == {PASS}
    assert projection_problems(_state_with_pages("c0000-p0000")) == []


def test_nothing_recorded_is_absent_not_pass():
    checks = check_page_projection(ProjectState(project_id="t"))

    assert _status(checks, "page projection") == ABSENT
    assert all(check.status == ABSENT for check in checks)


def test_a_page_done_without_an_artifact_is_a_mismatch():
    """One record lost out of several: the realistic crash/edit shape.

    Both fields are written in one document by ``record_page_artifact``, so a
    lone missing artifact cannot come from a crash between two writes — which is
    exactly why it is worth reporting rather than repairing.
    """
    state = _state_with_pages("c0000-p0000", "c0000-p0001")
    state.generated.pages.pop("c0000-p0001")

    problems = projection_problems(state)

    assert any("c0000-p0001" in problem for problem in problems)
    assert any("done without an artifact" in problem for problem in problems)


def test_an_artifact_without_a_done_record_is_a_mismatch():
    state = _state_with_pages("c0000-p0000")
    state.pages_done = []

    # generated.pages survives while pages_done does not: the guard must not read
    # an empty done list as "nothing recorded".
    problems = projection_problems(state)

    assert any("artifact without a done record" in problem for problem in problems)


def test_a_page_cannot_be_done_and_tombstoned():
    state = _state_with_pages("c0000-p0000")
    state.tombstones["c0000-p0000"] = Tombstone(outcome="rejected", reason="content_policy")

    assert _status(check_page_projection(state), "done vs tombstones") == MISMATCH


def test_a_page_cannot_be_done_and_queued_for_redraw():
    """``start_regen_job`` removes the done entry; a caller that does not would
    leave resume with two contradictory instructions for one page."""
    state = _state_with_pages("c0000-p0000")
    state.stale_pages = ["c0000-p0000"]

    assert _status(check_page_projection(state), "done vs stale") == MISMATCH


def test_duplicate_done_entries_are_a_mismatch():
    state = _state_with_pages("c0000-p0000")
    state.pages_done = ["c0000-p0000", "c0000-p0000"]

    assert _status(check_page_projection(state), "done records") == MISMATCH


def test_a_pre_projection_project_is_absent_not_broken():
    """Projects written before ``generated.pages`` existed keep only pages_done.

    Failing them would make ``verify`` unusable on a legacy checkout, which is
    worse than saying there is nothing to compare.
    """
    state = ProjectState(project_id="t")
    state.pages_done = ["c0000-p0000", "c0000-p0001"]

    checks = check_page_projection(state)

    assert _status(checks, "page projection") == ABSENT
    assert "predates" in next(c.detail for c in checks if c.name == "page projection")
    assert projection_problems(state) == []


def test_verify_fails_on_a_projection_mismatch(tmp_path, capsys):
    state = _state_with_pages("c0000-p0000", "c0000-p0001")
    state.generated.pages.pop("c0000-p0001")
    state.save(tmp_path / "state.json")

    args = _build_parser().parse_args(["verify", "--out", str(tmp_path)])
    assert _run_verify(args) == 1

    assert "done without an artifact" in capsys.readouterr().err


def test_verify_reports_absent_without_failing_a_clean_project(tmp_path, capsys):
    ProjectState(project_id="t").save(tmp_path / "state.json")

    args = _build_parser().parse_args(["verify", "--out", str(tmp_path)])
    assert _run_verify(args) == 0

    captured = capsys.readouterr()
    assert "absent" in captured.err
    assert captured.out.strip() == "ok"


def test_verify_survives_an_unreadable_state_json(tmp_path, capsys):
    """A corrupt checkpoint is reported, not raised: verify is a diagnostic."""
    (tmp_path / "state.json").write_text("{not json", encoding="utf-8")

    args = _build_parser().parse_args(["verify", "--out", str(tmp_path)])
    assert _run_verify(args) == 1

    assert "state.json unreadable" in capsys.readouterr().err
