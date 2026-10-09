"""Phase 5a: a run never issues more billable calls than its quota budget.

Design §12. Budget is three tiers — an append-only shared call log, a
per-stage reservation that cannot borrow, and a recovery reserve withheld
from the run. Exhaustion pauses at the item boundary. ``--no-carry-over``
is the default, so a later calendar day does not silently spend a fresh
allowance. Budget state enters no fingerprint (§12 invariant 10).
"""

import json

import pytest

from core.comic.budget import (
    BudgetPaused,
    BudgetSession,
    BudgetSpec,
    allocate,
)
from core.pipelines.creative_comic import _input_fingerprint, _render_fingerprint


def _spec(**overrides) -> BudgetSpec:
    fields = {
        "total": 5,
        "recovery_reserve": 1,
        "carry_over": False,
        "reservations": {"extract": 1, "render": 3},
    }
    fields.update(overrides)
    return BudgetSpec(**fields)


def _open(tmp_path, spec, *, project_id="p", today="2026-10-09"):
    return BudgetSession.open(
        data_dir=tmp_path / "data",
        project_id=project_id,
        output_dir=tmp_path / "out",
        spec=spec,
        today=today,
    )


def _lines(path):
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_allocate_withholds_a_reserve_and_leaves_the_rest_for_render():
    """Default split: 1 call held back once the budget can spare it; render
    gets whatever the chat/portrait floors do not."""
    spec = allocate(12)
    assert spec.recovery_reserve == 1
    assert spec.carry_over is False
    assert spec.reservations["extract"] == 1
    assert spec.reservations["bible"] == 1
    assert spec.reservations["beats"] == 1
    assert spec.reservations["page_plan"] == 2
    assert spec.reservations["portrait"] == 1
    assert spec.reservations["render"] == 5
    assert sum(spec.reservations.values()) == spec.total - spec.recovery_reserve


def test_allocate_of_a_single_call_withholds_nothing():
    spec = allocate(1)
    assert spec.recovery_reserve == 0
    assert spec.reservations["extract"] == 1
    assert spec.reservations["render"] == 0


def test_allocate_panel_compose_funds_storyboard_not_page_plan():
    spec = allocate(12, render_mode="panel_compose")
    assert "page_plan" not in spec.reservations
    assert spec.reservations["storyboard"] == 1
    assert spec.reservations["page_script"] == 1
    assert spec.reservations["render"] == 6


def test_reservations_may_not_exceed_the_spendable_budget():
    with pytest.raises(ValueError):
        BudgetSpec(total=3, recovery_reserve=1, reservations={"render": 3})


def test_a_stage_cannot_borrow_another_stages_reservation(tmp_path):
    session = _open(tmp_path, _spec())
    session.charge("extract", "c0000")
    with pytest.raises(BudgetPaused) as raised:
        session.charge("extract", "c0000")
    assert raised.value.key == "c0000"
    assert raised.value.stage == "extract"
    # render still has its own reservation; the failed extract did not take it.
    session.charge("render", "c0000-p0000")
    quota = _lines(tmp_path / "data" / "quota.jsonl")
    assert [row["stage"] for row in quota] == ["extract", "render"]


def test_the_recovery_reserve_cannot_be_spent_by_a_normal_charge(tmp_path):
    """Spendable is total - reserve. The last withheld call is not issued."""
    spec = BudgetSpec(total=2, recovery_reserve=1, reservations={"render": 1})
    session = _open(tmp_path, spec)
    session.charge("render", "c0000-p0000")
    with pytest.raises(BudgetPaused):
        session.charge("render", "c0000-p0001")
    assert _lines(tmp_path / "data" / "quota.jsonl") == [
        {
            "ts": _lines(tmp_path / "data" / "quota.jsonl")[0]["ts"],
            "project_id": "p",
            "stage": "render",
            "key": "c0000-p0000",
        }
    ]


def test_pausing_twice_on_the_same_boundary_appends_one_stop(tmp_path):
    spec = BudgetSpec(total=2, recovery_reserve=1, reservations={"render": 1})
    first = _open(tmp_path, spec)
    first.charge("render", "c0000-p0000")
    with pytest.raises(BudgetPaused):
        first.charge("render", "c0000-p0001")

    second = _open(tmp_path, spec)
    with pytest.raises(BudgetPaused):
        second.charge("render", "c0000-p0001")

    stops = _lines(tmp_path / "out" / "runs.jsonl")
    assert len(stops) == 1
    assert stops[0]["kind"] == "stop"
    assert stops[0]["key"] == "c0000-p0001"
    assert stops[0]["stage"] == "render"
    assert stops[0]["reason"] == "budget"
    assert stops[0]["carry_over"] is False


def test_no_carry_over_does_not_rearm_on_the_next_day(tmp_path):
    spec = BudgetSpec(total=2, recovery_reserve=1, reservations={"render": 1})
    _open(tmp_path, spec).charge("render", "c0000-p0000")
    later = _open(tmp_path, spec, today="2026-10-10")
    with pytest.raises(BudgetPaused):
        later.charge("render", "c0000-p0001")
    assert len(_lines(tmp_path / "data" / "quota.jsonl")) == 1
    assert len(_lines(tmp_path / "out" / "runs.jsonl")) == 1


def test_carry_over_rearms_only_on_a_new_day(tmp_path):
    spec = BudgetSpec(total=2, recovery_reserve=1, carry_over=True, reservations={"render": 1})
    _open(tmp_path, spec).charge("render", "c0000-p0000")

    same_day = _open(tmp_path, spec, today="2026-10-09")
    with pytest.raises(BudgetPaused):
        same_day.charge("render", "c0000-p0001")

    next_day = _open(tmp_path, spec, today="2026-10-10")
    next_day.charge("render", "c0000-p0001")
    assert len(_lines(tmp_path / "data" / "quota.jsonl")) == 2
    stops = _lines(tmp_path / "out" / "runs.jsonl")
    assert len(stops) == 1
    assert stops[0]["carry_over"] is True


def test_a_larger_same_day_grant_continues_without_forgetting_spent(tmp_path):
    small = BudgetSpec(total=2, recovery_reserve=0, reservations={"render": 1})
    _open(tmp_path, small).charge("render", "c0000-p0000")
    larger = BudgetSpec(total=3, recovery_reserve=0, reservations={"render": 2})
    session = _open(tmp_path, larger)
    session.charge("render", "c0000-p0001")
    with pytest.raises(BudgetPaused):
        session.charge("render", "c0000-p0002")
    assert len(_lines(tmp_path / "data" / "quota.jsonl")) == 2


def test_projects_do_not_share_a_reservation(tmp_path):
    spec = BudgetSpec(total=2, recovery_reserve=1, reservations={"render": 1})
    _open(tmp_path, spec, project_id="a").charge("render", "c0000-p0000")
    _open(tmp_path, spec, project_id="b").charge("render", "c0000-p0000")
    rows = _lines(tmp_path / "data" / "quota.jsonl")
    assert [row["project_id"] for row in rows] == ["a", "b"]


def test_budget_does_not_enter_fingerprint_inputs():
    for fn in (_input_fingerprint, _render_fingerprint):
        names = fn.__code__.co_varnames
        assert "budget" not in names
        assert "carry_over" not in names
