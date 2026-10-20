"""Shared-state hardening: locks, corruption recovery, gate lookup, stamps."""

import json
from pathlib import Path

import pytest

from core.comic.budget import BudgetFile, BudgetSession, BudgetSpec
from core.comic.gate import GateSession
from core.comic.locks import exclusive_lock
from core.pipelines.creative_comic import _stamp_action_key
from core.schemas import KeyBeatSet, ProjectState


def _budget(tmp_path: Path, total: int = 10) -> BudgetSession:
    return BudgetSession.open(
        data_dir=tmp_path / "data",
        output_dir=tmp_path / "out",
        project_id="p",
        spec=BudgetSpec(total=total, reservations={"extract": total}, recovery_reserve=0),
        today="2026-10-20",
    )


def _rows(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


# D-05: two writers must not lose each other's updates
def test_exclusive_lock_rejects_a_second_nonblocking_holder(tmp_path):
    lock = tmp_path / "state.lock"
    with exclusive_lock(lock, blocking=False, busy_message="busy"):
        with pytest.raises(RuntimeError, match="busy"):
            with exclusive_lock(lock, blocking=False, busy_message="busy"):
                pass  # pragma: no cover - the second acquisition must fail


def test_two_budget_sessions_keep_both_charges(tmp_path):
    first = _budget(tmp_path)
    second = _budget(tmp_path)

    first.charge("extract", "c0000")
    second.charge("extract", "c0001")

    stored = BudgetFile.model_validate_json((tmp_path / "data" / "budget.json").read_text())
    assert stored.projects["p"].spent == {"extract": 2}
    assert second._project.spent == {"extract": 2}


def test_two_gate_sessions_keep_both_sample_keys(tmp_path):
    first = GateSession.open(
        data_dir=None, output_dir=tmp_path / "out", project_id="p", sample_size=2
    )
    second = GateSession.open(
        data_dir=None, output_dir=tmp_path / "out", project_id="p", sample_size=2
    )

    assert first.allow("c0000-p0000", 0, total_chunks=1) is True
    assert second.allow("c0000-p0001", 0, total_chunks=1) is True

    loaded = GateSession.load(data_dir=None, output_dir=tmp_path / "out", project_id="p")
    assert loaded is not None
    assert loaded.project.sample_keys == ["c0000-p0000", "c0000-p0001"]


# D-06: a corrupt shared file is quarantined, not fatal
def test_a_corrupt_budget_is_quarantined_and_rebuilt(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "budget.json").write_text("{ not json", encoding="utf-8")

    session = _budget(tmp_path)

    assert session._project.total == 10
    assert list(data.glob("budget.json.corrupt-*"))
    assert (tmp_path / "data" / "budget.json").is_file()
    assert any(row["kind"] == "recover" for row in _rows(tmp_path / "out" / "runs.jsonl"))


def test_a_corrupt_gate_is_quarantined_and_rebuilt(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "gate.json").write_text("{ not json", encoding="utf-8")

    session = GateSession.open(data_dir=None, output_dir=out, project_id="p", sample_size=1)

    assert session.project.sample_size == 1
    assert list(out.glob("gate.json.corrupt-*"))
    assert (out / "gate.json").is_file()
    assert any(row["kind"] == "recover" for row in _rows(out / "runs.jsonl"))


# D-07: the CLI's shared data dir must find a project-local gate
def test_load_finds_a_project_local_gate_from_the_shared_data_dir(tmp_path):
    out = tmp_path / "out"
    gate = GateSession.open(data_dir=None, output_dir=out, project_id="p", sample_size=1)
    assert gate.allow("c0000-p0000", 0, total_chunks=1) is True

    loaded = GateSession.load(data_dir=tmp_path / "shared", output_dir=out, project_id="p")

    assert loaded is not None
    assert loaded.project.sample_keys == ["c0000-p0000"]


# D-10: a stamp-only cache mutation is persisted immediately
def test_stamping_a_missing_action_key_saves_the_checkpoint(tmp_path):
    state = ProjectState(project_id="p")
    target = KeyBeatSet()
    state.beat_cache["0"] = target
    state_path = tmp_path / "state.json"

    assert _stamp_action_key(state, state_path, target, "sha256:abc") is True
    assert target.action_key == "sha256:abc"

    reloaded = ProjectState.load(state_path)
    assert reloaded.beat_cache["0"].action_key == "sha256:abc"
    # An existing key is never overwritten by a later stamp.
    assert _stamp_action_key(state, state_path, target, "sha256:def") is False
