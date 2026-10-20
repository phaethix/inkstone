"""Planning-only A/B probes and candidate harness loading."""

import asyncio
import json
import shutil
from pathlib import Path

import pytest

from core.cli import _build_parser, _run_experiment, _run_harness, _run_prompt
from core.experiment import (
    ExperimentError,
    advance_sequence_async,
    compose_harness,
    pick_experiment,
    plan_experiment,
    plan_sequence_async,
    sequence_status,
)
from core.harness.spec import DATA_DIR
from core.schemas import StoryElements
from core.screenwriter import build_page_plan_request
from tests.test_finished_page_pipeline import FakeChat


def _candidate(tmp_path: Path, marker: str = "CANDIDATE MARKER") -> Path:
    target = tmp_path / "candidate"
    shutil.copytree(DATA_DIR, target)
    page_plan = target / "stages" / "page_plan.md"
    page_plan.write_text(page_plan.read_text(encoding="utf-8") + "\n" + marker + "\n")
    return target


def test_a_candidate_harness_changes_the_page_plan_prompt_and_digest(tmp_path):
    candidate = _candidate(tmp_path)
    from core.harness.spec import harness_from_dir

    harness = harness_from_dir(candidate)
    assert harness.validate() == []
    base = build_page_plan_request("第一回", StoryElements(), model="m")
    other = build_page_plan_request("第一回", StoryElements(), harness=harness, model="m")
    assert "CANDIDATE MARKER" in other.messages[-1]["content"]
    assert "CANDIDATE MARKER" not in base.messages[-1]["content"]
    assert other.digest() != base.digest()


def test_plan_experiment_writes_an_anonymised_packet(tmp_path):
    candidate = _candidate(tmp_path)
    out = tmp_path / "experiment"
    manifest = asyncio.run(
        plan_experiment(
            "第一章\n福贵在村口。",
            out_dir=out,
            candidates=["base", str(candidate)],
            chat=FakeChat(),
        )
    )

    assert manifest["status"] == "awaiting_selection"
    assert set(manifest["candidates"]) == {"A", "B"}
    assert manifest["evidence"] == "candidate"
    assert manifest["cost"]["image_calls"] == 0
    # Each candidate runs extract + beats + page_plan.
    assert manifest["cost"]["chat_calls"] == 6
    assert (out / "task.md").is_file()
    for anon in ("A", "B"):
        assert (out / "candidates" / anon / "plan.json").is_file()
        assert (out / "candidates" / anon / "structure_report.json").is_file()
        assert (out / "candidates" / anon / "prompt.json").is_file()
        assert (out / "candidates" / anon / "prompts" / "extract.json").is_file()
        assert (out / "candidates" / anon / "prompts" / "beats.json").is_file()
    prompts = {
        anon: json.loads((out / "candidates" / anon / "prompt.json").read_text(encoding="utf-8"))[
            "messages"
        ][-1]["content"]
        for anon in ("A", "B")
    }
    assert sum("CANDIDATE MARKER" in text for text in prompts.values()) == 1


def test_pick_records_the_choice_and_reveals_the_label(tmp_path):
    candidate = _candidate(tmp_path)
    out = tmp_path / "experiment"
    asyncio.run(
        plan_experiment(
            "第一章\n福贵在村口。",
            out_dir=out,
            candidates=["base", str(candidate)],
            chat=FakeChat(),
        )
    )
    with pytest.raises(ExperimentError, match="needs --why"):
        pick_experiment(out, choice="A", why="")
    selection = pick_experiment(out, choice="A", why="更少的对白，先给空间")
    assert selection["chosen"] == "A"
    assert selection["human"] is True
    assert selection["chosen_label"] in {"base", str(candidate)}
    assert (out / "selection.json").is_file()
    manifest = json.loads((out / "experiment.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "selected"


def test_a_bad_candidate_is_rejected_before_any_call(tmp_path):
    with pytest.raises(ExperimentError, match="unknown genre|component|manifest|harness"):
        asyncio.run(
            plan_experiment(
                "第一章",
                out_dir=tmp_path / "e",
                candidates=["base", "missing"],
                chat=FakeChat(),
            )
        )


def test_cli_plan_and_pick_with_a_fake_provider(tmp_path, monkeypatch, capsys):
    candidate = _candidate(tmp_path)
    monkeypatch.setattr("core.experiment.get_chat_provider", lambda: FakeChat())
    out = tmp_path / "experiment"
    parser = _build_parser()
    args = parser.parse_args(
        [
            "experiment",
            "plan",
            "--book",
            str(_book(tmp_path)),
            "--out",
            str(out),
            "--candidate",
            "base",
            "--candidate",
            str(candidate),
        ]
    )
    assert _run_experiment(args) == 0
    assert "candidates: A, B" in capsys.readouterr().out

    pick = parser.parse_args(
        [
            "experiment",
            "pick",
            "--run",
            str(out),
            "--choice",
            "B",
            "--why",
            "候选的构图更清楚",
        ]
    )
    assert _run_experiment(pick) == 0
    assert "selected B" in capsys.readouterr().out

    compose = parser.parse_args(
        [
            "experiment",
            "compose",
            "--run",
            str(out),
            "--choice",
            "B",
            "--out",
            str(tmp_path / "adopted"),
        ]
    )
    assert _run_experiment(compose) == 0
    assert "composed" in capsys.readouterr().out
    assert (tmp_path / "adopted" / "manifest.json").is_file()


def _book(tmp_path: Path) -> Path:
    path = tmp_path / "book.txt"
    path.write_text("第一章\n福贵在村口。", encoding="utf-8")
    return path


def test_cli_harness_dir_validate_and_prompt(tmp_path, capsys):
    candidate = _candidate(tmp_path)
    parser = _build_parser()

    args = parser.parse_args(["harness", "validate", "--dir", str(candidate)])
    assert _run_harness(args) == 0
    assert capsys.readouterr().out.startswith("ok sha256:")

    args = parser.parse_args(
        [
            "prompt",
            "--stage",
            "page_plan",
            "--text",
            "第一回",
            "--harness-dir",
            str(candidate),
        ]
    )
    assert _run_prompt(args) == 0
    assert "CANDIDATE MARKER" in capsys.readouterr().out


def test_shared_evidence_keeps_the_cheaper_four_call_probe(tmp_path):
    candidate = _candidate(tmp_path)
    out = tmp_path / "experiment-shared"
    manifest = asyncio.run(
        plan_experiment(
            "第一章\n福贵在村口。",
            out_dir=out,
            candidates=["base", str(candidate)],
            chat=FakeChat(),
            shared_evidence=True,
        )
    )
    assert manifest["evidence"] == "shared"
    assert manifest["cost"]["chat_calls"] == 4
    assert (out / "shared" / "prompts" / "extract.json").is_file()
    assert not (out / "candidates" / "A" / "prompts" / "extract.json").exists()


def test_compose_adopts_the_picked_harness(tmp_path):
    candidate = _candidate(tmp_path)
    out = tmp_path / "experiment"
    manifest = asyncio.run(
        plan_experiment(
            "第一章\n福贵在村口。",
            out_dir=out,
            candidates=["base", str(candidate)],
            chat=FakeChat(),
            shared_evidence=True,
        )
    )
    anon = next(
        key for key, record in manifest["candidates"].items() if record["label"] == str(candidate)
    )
    with pytest.raises(ExperimentError, match="pick a candidate"):
        compose_harness(out, choice=anon, out_dir=tmp_path / "too-early")
    pick_experiment(out, choice=anon, why="更清楚的构图")
    adopted = tmp_path / "adopted"
    record = compose_harness(out, choice=anon, out_dir=adopted)

    from core.harness.spec import harness_from_dir

    assert record["harness_digest"] == harness_from_dir(candidate).digest()
    assert (adopted / "stages" / "page_plan.md").is_file()
    assert "CANDIDATE MARKER" in (adopted / "stages" / "page_plan.md").read_text()
    assert (adopted / "ADOPTION.md").is_file()
    updated = json.loads((out / "experiment.json").read_text(encoding="utf-8"))
    assert updated["status"] == "composed"
    assert updated["composed_digest"] == record["harness_digest"]


def test_compose_can_merge_one_component_from_the_other_candidate(tmp_path):
    candidate = _candidate(tmp_path)
    out = tmp_path / "experiment"
    manifest = asyncio.run(
        plan_experiment(
            "第一章\n福贵在村口。",
            out_dir=out,
            candidates=["base", str(candidate)],
            chat=FakeChat(),
            shared_evidence=True,
        )
    )
    base_anon = next(
        key for key, record in manifest["candidates"].items() if record["label"] == "base"
    )
    candidate_anon = next(
        key for key, record in manifest["candidates"].items() if record["label"] == str(candidate)
    )
    pick_experiment(out, choice=base_anon, why="base is the safer starting point")
    adopted = tmp_path / "merged"
    record = compose_harness(
        out,
        choice=base_anon,
        out_dir=adopted,
        merge_from=[candidate_anon],
        components=["stage.page_plan"],
    )
    assert record["components"] == ["stage.page_plan"]
    assert "CANDIDATE MARKER" in (adopted / "stages" / "page_plan.md").read_text()
    assert "CANDIDATE MARKER" not in (adopted / "common" / "system.md").read_text()
    with pytest.raises(ExperimentError, match="does not match"):
        compose_harness(out, choice=candidate_anon, out_dir=tmp_path / "wrong")


def test_sequence_runs_two_rounds_and_carries_the_checkpoint(tmp_path):
    candidate = _candidate(tmp_path)
    sequence_dir = tmp_path / "sequence"
    sequence = asyncio.run(
        plan_sequence_async(
            "第一章\n福贵在村口。",
            out_dir=sequence_dir,
            candidates=[str(candidate)],
            rounds=2,
            chat=FakeChat(),
        )
    )
    assert sequence["status"] == "awaiting_pick"
    assert sequence["current_round"] == 1
    assert (sequence_dir / "rounds" / "1" / "experiment.json").is_file()

    round_one = json.loads(
        (sequence_dir / "rounds" / "1" / "experiment.json").read_text(encoding="utf-8")
    )
    candidate_anon = next(
        anon
        for anon, record in round_one["candidates"].items()
        if record["label"] == str(candidate)
    )
    pick_experiment(sequence_dir / "rounds" / "1", choice=candidate_anon, why="更清楚的构图")

    sequence = asyncio.run(advance_sequence_async(sequence_dir, chat=FakeChat()))
    assert sequence["status"] == "awaiting_pick"
    assert sequence["current_round"] == 2
    assert (sequence_dir / "rounds" / "1" / "checkpoint" / "manifest.json").is_file()
    assert (sequence_dir / "rounds" / "2" / "experiment.json").is_file()

    round_two = json.loads(
        (sequence_dir / "rounds" / "2" / "experiment.json").read_text(encoding="utf-8")
    )
    # The current checkpoint is one of the two candidates in round 2.
    labels = {record["label"] for record in round_two["candidates"].values()}
    assert str(sequence_dir / "rounds" / "1" / "checkpoint") in labels
    assert str(candidate) in labels

    pick_experiment(sequence_dir / "rounds" / "2", choice="A", why="第二轮更稳定")
    sequence = asyncio.run(advance_sequence_async(sequence_dir, chat=FakeChat()))
    assert sequence["status"] == "done"
    assert sequence["completed_rounds"] == [1, 2]
    status = sequence_status(sequence_dir)
    assert status["status"] == "done"
    assert status["current_checkpoint"].endswith("rounds/2/checkpoint")


def test_sequence_advance_requires_a_pick(tmp_path):
    candidate = _candidate(tmp_path)
    sequence_dir = tmp_path / "sequence"
    asyncio.run(
        plan_sequence_async(
            "第一章\n福贵在村口。",
            out_dir=sequence_dir,
            candidates=[str(candidate)],
            rounds=1,
            chat=FakeChat(),
        )
    )
    with pytest.raises(ExperimentError, match="pick a candidate"):
        asyncio.run(advance_sequence_async(sequence_dir, chat=FakeChat()))


def test_cli_sequence_plan_status_and_advance(tmp_path, monkeypatch, capsys):
    candidate = _candidate(tmp_path)
    monkeypatch.setattr("core.experiment.get_chat_provider", lambda: FakeChat())
    parser = _build_parser()
    sequence_dir = tmp_path / "sequence"

    plan = parser.parse_args(
        [
            "experiment",
            "sequence",
            "plan",
            "--book",
            str(_book(tmp_path)),
            "--out",
            str(sequence_dir),
            "--candidate",
            str(candidate),
            "--rounds",
            "1",
        ]
    )
    assert _run_experiment(plan) == 0
    assert "wrote" in capsys.readouterr().out

    status = parser.parse_args(["experiment", "sequence", "status", "--run", str(sequence_dir)])
    assert _run_experiment(status) == 0
    assert "awaiting_pick" in capsys.readouterr().out

    # Pick the candidate, then advance the single round to completion.
    round_one = json.loads(
        (sequence_dir / "rounds" / "1" / "experiment.json").read_text(encoding="utf-8")
    )
    candidate_anon = next(
        anon
        for anon, record in round_one["candidates"].items()
        if record["label"] == str(candidate)
    )
    pick = parser.parse_args(
        [
            "experiment",
            "pick",
            "--run",
            str(sequence_dir / "rounds" / "1"),
            "--choice",
            candidate_anon,
            "--why",
            "更好的空间建立",
        ]
    )
    assert _run_experiment(pick) == 0
    capsys.readouterr()

    advance = parser.parse_args(["experiment", "sequence", "advance", "--run", str(sequence_dir)])
    assert _run_experiment(advance) == 0
    assert "status: done" in capsys.readouterr().out
