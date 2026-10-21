"""``inkstone prompt`` renders the exact stage request without a model call."""

import json

from core.cli import _build_parser, _run_harness, _run_prompt
from core.screenwriter import build_extract_request


def _args(*argv: str):
    return _build_parser().parse_args(["prompt", *argv])


def test_prompt_prints_the_effective_extract_request(capsys):
    args = _args("--stage", "extract", "--text", "第一回 福贵在村口。")
    assert _run_prompt(args) == 0
    out = capsys.readouterr().out
    assert "stage: extract" in out
    assert "tool: extract_story_elements" in out
    assert "comic scriptwriter" in out
    assert "第一回 福贵在村口。" in out


def test_prompt_json_carries_the_same_digest(capsys):
    text = "第一回 福贵在村口。"
    args = _args("--stage", "extract", "--text", text, "--json", "--model", "m")
    assert _run_prompt(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["stage"] == "extract"
    assert payload["model"] == "m"
    assert payload["tool_choice"]["function"]["name"] == "extract_story_elements"
    assert payload["digest"] == build_extract_request(text, model="m").digest()
    assert len(payload["messages"]) == 2
    assert payload["tools"]


def test_prompt_page_script_requires_a_stored_storyboard(capsys):
    args = _args("--stage", "page_script", "--text", "第一回")
    assert _run_prompt(args) == 1
    assert "needs a stored storyboard" in capsys.readouterr().err


def test_prompt_genre_adds_domain_notes(capsys):
    args = _args("--stage", "page_plan", "--text", "第一回", "--genre", "wuxia")
    assert _run_prompt(args) == 0
    out = capsys.readouterr().out
    assert "Domain notes for this genre (wuxia)" in out
    assert "Wuxia" in out


def test_harness_validate_and_list(capsys):
    args = _build_parser().parse_args(["harness", "validate"])
    assert _run_harness(args) == 0
    assert capsys.readouterr().out.startswith("ok sha256:")

    args = _build_parser().parse_args(["harness", "list"])
    assert _run_harness(args) == 0
    out = capsys.readouterr().out
    assert "system" in out
    assert "genre.wuxia" in out
    assert "wuxia" in out


def test_prompt_harness_dir_applies_to_extract(tmp_path, capsys):
    import shutil

    from core.harness.spec import DATA_DIR

    candidate = tmp_path / "candidate"
    shutil.copytree(DATA_DIR, candidate)
    system = candidate / "common" / "system.md"
    system.write_text(system.read_text(encoding="utf-8") + "\nEXTRACT HARNESS MARKER\n")

    args = _args(
        "--stage",
        "extract",
        "--text",
        "第一回",
        "--harness-dir",
        str(candidate),
    )
    assert _run_prompt(args) == 0
    assert "EXTRACT HARNESS MARKER" in capsys.readouterr().out


def test_prompt_reads_the_tier_the_project_was_planned_with(tmp_path, capsys):
    """The command promises the request a stage *would* send, so the budget it
    prints has to be the stored one. It used to hard-code tier B, which showed
    about 8 panels and "a target, not a cap" even for a tier C project."""
    from core.schemas import ProjectState

    ProjectState(
        project_id="p1",
        density="C",
        panels_per_chunk=14,
    ).save(tmp_path / "state.json")

    args = _args("--stage", "page_plan", "--from-run", str(tmp_path))
    assert _run_prompt(args) == 0
    out = capsys.readouterr().out

    assert "about 14 panels" in out
    assert "do not exceed this budget" in out


def test_prompt_tier_b_keeps_the_target_wording(tmp_path, capsys):
    """Tier B does not clip, so its sentence must not promise a ceiling."""
    from core.schemas import ProjectState

    ProjectState(project_id="p1", density="B", panels_per_chunk=8).save(tmp_path / "state.json")

    args = _args("--stage", "page_plan", "--from-run", str(tmp_path))
    assert _run_prompt(args) == 0
    out = capsys.readouterr().out

    assert "about 8 panels" in out
    assert "this number is a target, not a cap" in out
