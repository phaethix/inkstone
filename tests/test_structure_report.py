"""Structural checks read stored records. Absent is not a pass."""

import argparse
import json
from pathlib import Path

import pytest

from core.cli import _run_structure
from core.comic.segmentation import source_units
from core.comic.structure_report import chapter_validated, structural_report
from core.schemas import (
    AdaptationPlan,
    ChunkCache,
    NarrativeBeat,
    Panel,
    ProjectState,
    Storyboard,
    Tombstone,
)


def _status(report, name: str) -> str:
    return next(check.status for check in report.checks if check.name == name)


def test_an_empty_project_marks_missing_records_absent():
    report = structural_report(ProjectState(project_id="p"), "")
    assert _status(report, "source") == "absent"
    assert _status(report, "beats") == "absent"
    assert _status(report, "visible_text") == "absent"
    assert _status(report, "causal_order") == "absent"
    assert _status(report, "blocked") == "pass"
    assert report.ok


def test_units_that_partition_the_source_pass_and_a_gap_fails():
    source = "第一章\n甲。"
    state = ProjectState(project_id="p", source_units=source_units(source))
    assert _status(structural_report(state, source), "source") == "pass"
    state.source_units[0].end -= 1
    assert _status(structural_report(state, source), "source") == "fail"


def test_a_required_beat_must_be_named_by_a_panel():
    state = ProjectState(
        project_id="p",
        adaptation_plans={
            "u0000": AdaptationPlan(
                unit_id="u0000",
                beats=[NarrativeBeat(beat_id="hatch", unit_id="u0000", status="required")],
            )
        },
    )
    assert _status(structural_report(state, ""), "beats") == "fail"
    state.chunk_cache["0"] = ChunkCache(
        storyboard=Storyboard(
            chapter_id="c",
            panels=[Panel(panel_id="p1", beat_ids=["hatch"], caption="甲。")],
        )
    )
    report = structural_report(state, "甲。")
    assert _status(report, "beats") == "pass"
    assert _status(report, "visible_text") == "pass"


def test_lettering_outside_the_source_fails():
    state = ProjectState(
        project_id="p",
        chunk_cache={
            "0": ChunkCache(
                storyboard=Storyboard(
                    chapter_id="c",
                    panels=[Panel(panel_id="p1", dialogue="我回来了。")],
                )
            )
        },
    )
    report = structural_report(state, "甲板上。")
    assert _status(report, "visible_text") == "fail"
    assert "我回来了。" in next(
        check.detail for check in report.checks if check.name == "visible_text"
    )


def test_a_dependency_must_name_an_earlier_beat_in_the_same_unit():
    plan = AdaptationPlan(
        unit_id="u0000",
        beats=[
            NarrativeBeat(beat_id="hatch", unit_id="u0000", status="required"),
            NarrativeBeat(
                beat_id="walk",
                unit_id="u0000",
                status="optional",
                depends_on=["hatch"],
            ),
        ],
    )
    state = ProjectState(project_id="p", adaptation_plans={"u0000": plan})
    assert _status(structural_report(state, ""), "causal_order") == "pass"
    plan.beats[0].depends_on = ["walk"]
    assert _status(structural_report(state, ""), "causal_order") == "fail"


def test_a_skipped_chunk_fails_even_when_nothing_else_was_drawn():
    state = ProjectState(project_id="p", skipped_chunks=["1"])
    report = structural_report(state, "")
    assert _status(report, "blocked") == "fail"
    assert "chunk 1 skipped" in next(c.detail for c in report.checks if c.name == "blocked")
    state.pages_done.append("c0001-p0000")
    detail = next(c.detail for c in structural_report(state, "").checks if c.name == "blocked")
    assert "marked done" in detail
    state.skipped_chunks = []
    state.pages_done = []
    state.tombstones["c0000-p0000"] = Tombstone(outcome="rejected")
    assert _status(structural_report(state, ""), "blocked") == "fail"


def test_structure_command_prints_absent_and_strict_exits_on_a_skip(tmp_path, capsys):
    source = tmp_path / "source.txt"
    source.write_text("甲。", encoding="utf-8")
    ProjectState(project_id="p", skipped_chunks=["0"]).save(tmp_path / "state.json")
    args = argparse.Namespace(out=str(tmp_path), source=None, format="text", strict=False)
    _run_structure(args)
    out = capsys.readouterr().out
    assert "Structural record check" in out
    assert "not a readability or quality gate" in out
    assert "absent" in out
    assert "chunk 0 skipped" in out
    saved = json.loads((tmp_path / "structure_report.json").read_text(encoding="utf-8"))
    assert saved["ok"] is False
    assert saved["chapter_validated"] is False
    assert "chapter : not validated" in out

    args.strict = True
    with pytest.raises(SystemExit) as raised:
        _run_structure(args)
    assert raised.value.code == 1


def _ready_chapter(source: str) -> ProjectState:
    return ProjectState(
        project_id="p",
        source_units=source_units(source),
        adaptation_plans={
            "u0000": AdaptationPlan(
                unit_id="u0000",
                beats=[
                    NarrativeBeat(beat_id="hatch", unit_id="u0000", status="required"),
                    NarrativeBeat(
                        beat_id="walk",
                        unit_id="u0000",
                        status="optional",
                        depends_on=["hatch"],
                    ),
                ],
            )
        },
        chunk_cache={
            "0": ChunkCache(
                storyboard=Storyboard(
                    chapter_id="c",
                    panels=[Panel(panel_id="p1", beat_ids=["hatch"], caption="甲。")],
                )
            )
        },
        pages_done=["c0000-p0000"],
    )


def test_the_public_excerpt_is_one_unit_and_is_not_a_validated_chapter():
    path = Path(__file__).resolve().parents[1] / "examples/showcase/journey-west-ch1/source.txt"
    source = path.read_text(encoding="utf-8")
    state = ProjectState(project_id="journey", source_units=source_units(source))
    report = structural_report(state, source)
    assert len(state.source_units) == 1
    assert _status(report, "source") == "pass"
    assert _status(report, "beats") == "absent"
    assert _status(report, "visible_text") == "absent"
    assert chapter_validated(report, state) is False


def test_a_chapter_is_validated_only_when_every_check_passes_and_a_page_is_stored():
    source = "第一章\n甲。"
    state = _ready_chapter(source)
    report = structural_report(state, source)
    assert chapter_validated(report, state) is True
    state.pages_done = []
    assert chapter_validated(report, state) is False
