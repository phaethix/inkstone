"""P0 state-integrity regressions: ledger merge/prune/load, sample-size bounds.

These cover D-01..D-04 in
``docs/architecture/2026-10-19-content-addressed-pipeline-implementation-review.md``.
They are prerequisites for any automated harness iteration: a learning loop
writes human decisions, so a silent merge or overwrite must be impossible.
"""

import json
from pathlib import Path

import pytest

from core.comic.gate import adjust_sample_size
from core.comic.ledger import ConsistencyLedger, LedgerEntry, ReferenceVersion
from core.schemas import (
    STATE_SCHEMA_VERSION,
    ComicPagePlan,
    ComicPagePlanSet,
    PagePanelSpec,
    ProjectState,
)


def _plan(names: list[str], page_id: str = "p") -> ComicPagePlan:
    return ComicPagePlan(
        page_id=page_id,
        reference_characters=list(names),
        panels=[PagePanelSpec(panel_id="pan1", characters=list(names))],
    )


def _state_with(names: list[str]) -> ProjectState:
    state = ProjectState(project_id="t")
    state.page_cache["0"] = ComicPagePlanSet(unit_id="0", pages=[_plan(names)])
    return state


def _entry(pages: list[str], *, version: int, reviewed: int) -> LedgerEntry:
    entry = LedgerEntry(pages=list(pages), pending_pages=list(pages))
    entry.reference = ReferenceVersion(path="p.png", version=version)
    entry.reviewed_version = reviewed
    return entry


# D-01: a merge must not put reviewed_version ahead of reference.version
def test_rename_character_never_leaves_reviewed_ahead_of_reference():
    led = ConsistencyLedger(
        characters={
            "target": _entry(["c0000-p0000"], version=3, reviewed=1),
            "alias": _entry(["c0001-p0000"], version=7, reviewed=7),
        }
    )

    led.rename_character("alias", "target")

    entry = led.characters["target"]
    assert entry.reference.version == 7
    assert entry.reviewed_version <= entry.reference.version
    # The merged pages must not be silently treated as reviewed.
    assert entry.pending_pages == entry.pages
    assert led.escalated(threshold=1) == {"target": entry.pages}


def test_rename_character_keeps_a_recorded_pending_union_at_equal_versions():
    led = ConsistencyLedger(
        characters={
            "A": LedgerEntry(pages=["c0001-p0000"]),
            "别名": LedgerEntry(pages=["c0000-p0000"], pending_pages=["c0000-p0000"]),
        }
    )

    led.rename_character("别名", "A")

    assert led.characters["A"].pages == ["c0001-p0000", "c0000-p0000"]
    assert led.characters["A"].pending_pages == ["c0000-p0000"]


# D-02: a character that no longer appears must not block forever
def test_rebuild_clears_stale_pages_but_keeps_the_human_record():
    state = _state_with(["A"])
    stale_pages = [f"c0000-p{index:04d}" for index in range(31)]
    led = ConsistencyLedger(characters={"Gone": _entry(stale_pages, version=2, reviewed=1)})

    assert led.rebuild_from_state(state) is True

    assert "Gone" in led.characters  # reference/reviewed record is retained
    assert led.characters["Gone"].pages == []
    assert led.characters["Gone"].pending_pages == []
    assert led.escalated() == {}


def test_escalated_ignores_an_entry_with_no_pages_even_if_pending_is_stale():
    led = ConsistencyLedger(
        characters={
            "Gone": LedgerEntry(pages=[], pending_pages=[f"c0000-p{i:04d}" for i in range(40)])
        }
    )
    assert led.escalated() == {}


# D-03: a transient read error must not be swallowed and overwritten
def test_load_or_rebuild_propagates_os_error(tmp_path: Path, monkeypatch):
    path = tmp_path / "consistency.json"
    path.write_text("{}", encoding="utf-8")

    def _raise(cls, _path):
        raise OSError("EIO")

    monkeypatch.setattr(ConsistencyLedger, "load", classmethod(_raise))

    with pytest.raises(OSError):
        ConsistencyLedger.load_or_rebuild(path, _state_with(["A"]))


def test_load_or_rebuild_still_recovers_from_corruption(tmp_path: Path):
    path = tmp_path / "consistency.json"
    path.write_text("{ not json", encoding="utf-8")

    led = ConsistencyLedger.load_or_rebuild(path, _state_with(["A"]))

    assert led.characters["A"].pages == ["c0000-p0000"]


# D-04: the sample size can never grow to zero
def test_sample_size_growth_has_a_lower_bound():
    assert adjust_sample_size(1, 0.95, chapter_pages=0) >= 1
    assert adjust_sample_size(0, 0.95) >= 1
    assert adjust_sample_size(1, 0.95, chapter_pages=1) == 1
    assert adjust_sample_size(1, 0.4) >= 1


# --------------------------------------------------------------------------- #
# state.json versioning: an unknown field must survive a round trip
# --------------------------------------------------------------------------- #
def _write_state(path: Path, **overrides) -> None:
    """Write a state.json whose raw document can be edited before loading."""
    ProjectState(project_id="t").save(path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw.update(overrides)
    path.write_text(json.dumps(raw), encoding="utf-8")


def test_save_stamps_the_current_schema_version(tmp_path):
    path = tmp_path / "state.json"
    _write_state(path)

    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == STATE_SCHEMA_VERSION


def test_an_unversioned_file_loads_as_version_one_and_is_upgraded(tmp_path):
    """Every pre-existing ``state.json`` has no version field and must still load."""
    path = tmp_path / "state.json"
    ProjectState(project_id="t").save(path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    del raw["schema_version"]
    path.write_text(json.dumps(raw), encoding="utf-8")

    state = ProjectState.load(path)

    assert state.schema_version == 1
    state.save(path)
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == STATE_SCHEMA_VERSION


def test_unknown_top_level_fields_survive_a_load_save_round_trip(tmp_path, caplog):
    """Opening a newer file once must not delete the fields it added.

    ``extra="ignore"`` is correct for a model reply and wrong for a checkpoint: a
    field written by a later build would be dropped at load and then permanently
    removed by the next full dump.
    """
    path = tmp_path / "state.json"
    _write_state(path, future_field={"nested": [1, 2]}, future_flag=True)

    with caplog.at_level("WARNING", logger="core.schemas"):
        state = ProjectState.load(path)
    assert "future_field" in caplog.text
    state.save(path)

    after = json.loads(path.read_text(encoding="utf-8"))
    assert after["future_field"] == {"nested": [1, 2]}
    assert after["future_flag"] is True


def test_a_newer_schema_version_is_not_downgraded_on_save(tmp_path, caplog):
    """Writing back v2 over a v3 file would hide that a downgrade happened."""
    path = tmp_path / "state.json"
    newer = STATE_SCHEMA_VERSION + 1
    _write_state(path, schema_version=newer)

    with caplog.at_level("WARNING", logger="core.schemas"):
        state = ProjectState.load(path)
    state.save(path)

    assert f"schema v{newer}" in caplog.text
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == newer
