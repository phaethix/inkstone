"""``migrate --check`` must predict cost from what the files can prove.

The forecast exists because a prompt edit has two different costs: a chat stage
whose stage source changed re-calls, while page art repaints only when the plan
bytes differ. A forecast that reported "reusable" for everything would look
correct on a healthy project and be useless exactly when it is needed, so these
tests pin both the stale and the fresh answer.
"""

from __future__ import annotations

import json

from core.comic.cas import Manifest, save_manifest
from core.comic.migrate import check_recompute
from core.pipelines import creative_comic
from core.schemas import ProjectState


def _manifest(root, *, stage: str, stage_src: str, suffix: str = "a") -> str:
    key = "sha256:" + suffix * 64
    save_manifest(
        root,
        Manifest(
            key=key,
            stage=stage,
            outcome="ok",
            stage_src=stage_src,
            env="chat-model",
            inputs=[],
        ),
    )
    return key


def test_a_matching_stage_source_is_reusable(tmp_path):
    current = creative_comic._EXTRACT_STAGE_SRC
    _manifest(tmp_path, stage="extract", stage_src=current)

    forecast = check_recompute(tmp_path)
    extract = next(check for check in forecast.stages if check.stage == "extract")

    assert extract.current == 1
    assert extract.stale == 0
    assert forecast.chat_calls == 0


def test_a_changed_stage_source_is_stale(tmp_path):
    """A prompt edit changes the hash, so the stored manifest cannot be reused."""
    _manifest(tmp_path, stage="extract", stage_src="a-hash-from-before-the-edit")

    forecast = check_recompute(tmp_path)
    extract = next(check for check in forecast.stages if check.stage == "extract")

    assert extract.stale == 1
    assert extract.current == 0
    assert forecast.chat_calls == 1


def test_stale_manifests_are_counted_per_stage(tmp_path):
    _manifest(tmp_path, stage="extract", stage_src="old", suffix="a")
    _manifest(tmp_path, stage="page_plan", stage_src="old", suffix="b")
    _manifest(tmp_path, stage="bible", stage_src=creative_comic._BIBLE_STAGE_SRC, suffix="c")

    forecast = check_recompute(tmp_path)
    by_stage = {check.stage: check for check in forecast.stages}

    assert by_stage["extract"].stale == 1
    assert by_stage["page_plan"].stale == 1
    assert by_stage["bible"].current == 1
    assert forecast.chat_calls == 2


def test_a_manifest_without_a_stage_source_is_not_called_stale(tmp_path):
    """An older manifest may carry no ``stage_src``; absence is not evidence of change."""
    _manifest(tmp_path, stage="extract", stage_src="")

    forecast = check_recompute(tmp_path)
    extract = next(check for check in forecast.stages if check.stage == "extract")

    assert extract.stale == 0
    assert extract.current == 1


def test_page_art_is_reported_separately_from_chat_calls(tmp_path):
    """A chat re-plan does not force a repaint unless the plan bytes change."""
    from core.schemas import GeneratedPage

    _manifest(tmp_path, stage="extract", stage_src="old")
    state = ProjectState(project_id="p1")
    state.generated.pages["c0000-p0000"] = GeneratedPage(local="pages/p0000.png")
    state.pages_done = ["c0000-p0000"]
    state.save(tmp_path / "state.json")

    forecast = check_recompute(tmp_path)

    assert forecast.chat_calls == 1
    assert forecast.pages == 1
    assert forecast.pages_reusable == 1
    assert forecast.repaints == 0


def test_a_done_page_is_reusable_and_an_undone_one_is_not(tmp_path):
    from core.schemas import GeneratedPage

    state = ProjectState(project_id="p1")
    state.generated.pages["c0000-p0000"] = GeneratedPage(local="pages/a.png")
    state.generated.pages["c0000-p0001"] = GeneratedPage(local="pages/b.png")
    state.pages_done = ["c0000-p0000"]
    state.save(tmp_path / "state.json")

    forecast = check_recompute(tmp_path)

    assert forecast.pages == 2
    assert forecast.pages_reusable == 1
    assert forecast.repaints == 1


def test_a_plan_without_a_stored_key_is_counted_as_missing(tmp_path):
    from core.schemas import ComicPagePlanSet

    state = ProjectState(project_id="p1")
    state.page_cache["chunk-1"] = ComicPagePlanSet(unit_id="chunk-1", pages=[])
    state.save(tmp_path / "state.json")

    forecast = check_recompute(tmp_path)
    page_plan = next(check for check in forecast.stages if check.stage == "page_plan")

    assert page_plan.missing == 1


def test_check_does_not_need_a_provider(tmp_path, monkeypatch):
    """The whole point is forecasting without spending quota."""
    import core.api

    def _explode(*_args, **_kwargs):
        raise AssertionError("migrate --check must not build a provider")

    monkeypatch.setattr(core.api, "get_chat_provider", _explode)
    monkeypatch.setattr(core.api, "get_image_provider", _explode)

    _manifest(tmp_path, stage="extract", stage_src="old")
    assert check_recompute(tmp_path).chat_calls == 1


def test_a_stale_local_stage_costs_time_not_quota(tmp_path):
    """A letter manifest whose source changed is redone locally, not billed."""
    _manifest(tmp_path, stage="letter", stage_src="old-letter")

    forecast = check_recompute(tmp_path)

    assert forecast.local_redos == 1
    assert forecast.chat_calls == 0


def test_the_cli_reports_a_project_less_directory(tmp_path, capsys):
    from core.cli import _build_parser, _run_migrate

    args = _build_parser().parse_args(["migrate", "--check", "--out", str(tmp_path)])
    assert _run_migrate(args) == 1
    assert "不是项目目录" in capsys.readouterr().out


def test_the_cli_prints_json_for_automation(tmp_path, capsys):
    from core.cli import _build_parser, _run_migrate

    _manifest(tmp_path, stage="extract", stage_src="old")
    args = _build_parser().parse_args(["migrate", "--check", "--json", "--out", str(tmp_path)])
    assert _run_migrate(args) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["chat_calls"] == 1
    assert any(row["stage"] == "extract" and row["stale"] == 1 for row in payload["stages"])
    assert payload["unattributed"] == []


def test_a_stage_outside_the_table_is_reported_not_dropped(tmp_path):
    """A stage added to the registry but not to the forecast used to vanish.

    Dropping it made the report look cheaper for exactly the stage a developer
    had just introduced — the one case the forecast exists to catch.
    """
    _manifest(tmp_path, stage="render.unknown", stage_src="whatever", suffix="b")

    forecast = check_recompute(tmp_path)

    assert forecast.unattributed == ["render.unknown"]
    assert all(check.stage != "render.unknown" for check in forecast.stages)


def test_a_known_stage_without_a_source_hash_counts_as_stale(tmp_path, monkeypatch):
    """Unprovable reuse must overstate a redo, never hide one."""
    monkeypatch.delattr(creative_comic, "_LETTER_STAGE_SRC", raising=False)
    _manifest(tmp_path, stage="letter", stage_src="a-hash", suffix="c")

    forecast = check_recompute(tmp_path)
    letter = next(check for check in forecast.stages if check.stage == "letter")

    assert letter.stale == 1
    assert letter.current == 0
