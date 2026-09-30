"""Phase 0g — minimal reclaimer (prune)."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import timedelta
from pathlib import Path

import pytest

from core.comic.ledger import ConsistencyLedger, LedgerEntry
from core.comic.prune import (
    PruneCandidate,
    PrunePlan,
    apply_prune,
    collect_live_refs,
    parse_older_than,
    plan_prune,
)
from core.schemas import (
    CharacterAsset,
    GeneratedAssets,
    GeneratedPage,
    GeneratedPanel,
    ProjectState,
)


def test_parse_older_than_days_hours_and_bare():
    assert parse_older_than("7d") == timedelta(days=7)
    assert parse_older_than("12h") == timedelta(hours=12)
    assert parse_older_than("3") == timedelta(days=3)


@pytest.mark.parametrize("bad", ["", "d", "7x", "-1d", "0d", "0", "1.5d", "d7"])
def test_parse_older_than_rejects_garbage(bad):
    with pytest.raises(ValueError):
        parse_older_than(bad)


def _state_with_assets(tmp_path: Path) -> ProjectState:
    state = ProjectState(project_id="p")
    state.generated = GeneratedAssets()
    state.generated.panels["c0000-p0000"] = GeneratedPanel(
        local=str(tmp_path / "panels" / "id-a.png"),
        chunk_index=0,
        panel_index=0,
    )
    state.generated.portraits["甲"] = str(tmp_path / "assets" / "portraits" / "id-b.png")
    state.generated.pages["c0000-p0000"] = GeneratedPage(
        local=str(tmp_path / "pages" / "page_c0000_p0000.png"),
        blank_local=str(tmp_path / "pages" / "blank" / "page_c0000_p0000.png"),
        mode="finished_lettered",
    )
    state.characters["甲"] = CharacterAsset(
        name="甲", portrait_local=str(tmp_path / "assets" / "portraits" / "id-b.png")
    )
    return state


def test_collect_live_refs_covers_every_root_source(tmp_path):
    state = _state_with_assets(tmp_path)
    ledger = ConsistencyLedger()
    ref = tmp_path / "assets" / "portraits" / "id-ref.png"
    ledger.characters["甲"] = LedgerEntry()
    ledger.characters["甲"].reference.path = str(ref)

    refs = collect_live_refs(state, ledger)

    assert (tmp_path / "panels" / "id-a.png").resolve() in refs
    assert (tmp_path / "assets" / "portraits" / "id-b.png").resolve() in refs
    assert (tmp_path / "pages" / "page_c0000_p0000.png").resolve() in refs
    assert (tmp_path / "pages" / "blank" / "page_c0000_p0000.png").resolve() in refs
    assert ref.resolve() in refs


def test_collect_live_refs_ignores_empty_paths(tmp_path):
    state = ProjectState(project_id="p")
    state.characters["乙"] = CharacterAsset(name="乙", portrait_local=None)
    refs = collect_live_refs(state, ConsistencyLedger())
    assert refs == set()


def _age(path: Path, seconds: float) -> None:
    old = time.time() - seconds
    os.utime(path, (old, old))


def _write(path: Path, payload: bytes = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def test_plan_prune_selects_old_unreferenced_only(tmp_path):
    referenced = _write(tmp_path / "panels" / "id-live.png", b"live")
    orphan_old = _write(tmp_path / "panels" / "id-orphan.png", b"orphan")
    orphan_new = _write(tmp_path / "panels" / "id-fresh.png", b"fresh")
    for path in (referenced, orphan_old):
        _age(path, 10 * 86400)

    state = _state_with_assets(tmp_path)
    state.generated.panels["c0000-p0000"].local = str(referenced)
    ledger = ConsistencyLedger()
    (tmp_path / "state.json").write_text(state.model_dump_json())
    (tmp_path / "consistency.json").write_text(ledger.model_dump_json())

    plan = plan_prune(tmp_path, parse_older_than("7d"))
    planned = {candidate.path for candidate in plan.candidates}
    assert planned == {orphan_old.resolve()}
    assert orphan_new.resolve() not in planned
    assert plan.total_bytes == 6


def test_plan_prune_missing_state_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        plan_prune(tmp_path, parse_older_than("7d"))


def test_apply_prune_deletes_and_reports(tmp_path):
    orphan = _write(tmp_path / "panels" / "id-orphan.png", b"12345")
    _age(orphan, 10 * 86400)
    plan = PrunePlan(candidates=[PruneCandidate(path=orphan, size_bytes=5, mtime=0.0)])

    result = apply_prune(plan)

    assert result.deleted == 1
    assert result.reclaimed_bytes == 5
    assert not orphan.exists()


def test_apply_prune_tolerates_vanished_file(tmp_path):
    missing = tmp_path / "panels" / "id-gone.png"
    plan = PrunePlan(candidates=[PruneCandidate(path=missing, size_bytes=5, mtime=0.0)])
    result = apply_prune(plan)
    assert result.deleted == 1


def test_plan_prune_ignores_non_asset_files(tmp_path):
    _write(tmp_path / "panels" / "id-orphan.png", b"o")
    comic_pdf = _write(tmp_path / "comic.pdf", b"%PDF")
    webtoon = _write(tmp_path / "webtoon.png", b"w")
    for path in (comic_pdf, webtoon):
        _age(path, 100 * 86400)
    state = ProjectState(project_id="p")
    (tmp_path / "state.json").write_text(state.model_dump_json())

    plan = plan_prune(tmp_path, parse_older_than("7d"))

    planned = {candidate.path for candidate in plan.candidates}
    assert comic_pdf.resolve() not in planned
    assert webtoon.resolve() not in planned


def test_plan_prune_never_plans_a_referenced_old_file(tmp_path):
    live = _write(tmp_path / "assets" / "portraits" / "id-live.png", b"keep")
    _age(live, 100 * 86400)
    state = ProjectState(project_id="p")
    state.characters["甲"] = CharacterAsset(name="甲", portrait_local=str(live))
    (tmp_path / "state.json").write_text(state.model_dump_json())

    plan = plan_prune(tmp_path, parse_older_than("7d"))

    assert live.resolve() not in {candidate.path for candidate in plan.candidates}


def test_plan_prune_corrupt_ledger_still_protects_state_roots(tmp_path):
    live = _write(tmp_path / "panels" / "id-live.png", b"keep")
    _age(live, 100 * 86400)
    state = ProjectState(project_id="p")
    state.generated.panels["c0000-p0000"] = GeneratedPanel(
        local=str(live), chunk_index=0, panel_index=0
    )
    (tmp_path / "state.json").write_text(state.model_dump_json())
    (tmp_path / "consistency.json").write_text("{ not json")

    plan = plan_prune(tmp_path, parse_older_than("7d"))

    assert live.resolve() not in {candidate.path for candidate in plan.candidates}


def test_apply_prune_is_idempotent(tmp_path):
    orphan = _write(tmp_path / "panels" / "id-orphan.png", b"12345")
    _age(orphan, 100 * 86400)
    (tmp_path / "state.json").write_text(ProjectState(project_id="p").model_dump_json())

    first = apply_prune(plan_prune(tmp_path, parse_older_than("7d")))
    second = apply_prune(plan_prune(tmp_path, parse_older_than("7d")))

    assert first.deleted == 1
    assert second.deleted == 0
    assert second.reclaimed_bytes == 0


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "core.cli", *args],
        capture_output=True,
        text=True,
    )


def test_cli_prune_dry_run_leaves_files(tmp_path):
    orphan = _write(tmp_path / "panels" / "id-orphan.png", b"o")
    _age(orphan, 100 * 86400)
    (tmp_path / "state.json").write_text(ProjectState(project_id="p").model_dump_json())

    completed = _run_cli("prune", "--out", str(tmp_path), "--older-than", "7d")

    assert completed.returncode == 0
    assert orphan.exists()
    assert "id-orphan.png" in completed.stdout


def test_cli_prune_apply_deletes(tmp_path):
    orphan = _write(tmp_path / "panels" / "id-orphan.png", b"o")
    _age(orphan, 100 * 86400)
    (tmp_path / "state.json").write_text(ProjectState(project_id="p").model_dump_json())

    completed = _run_cli("prune", "--out", str(tmp_path), "--older-than", "7d", "--apply")

    assert completed.returncode == 0
    assert not orphan.exists()


def test_cli_prune_rejects_bad_threshold(tmp_path):
    (tmp_path / "state.json").write_text(ProjectState(project_id="p").model_dump_json())
    completed = _run_cli("prune", "--out", str(tmp_path), "--older-than", "soon")
    assert completed.returncode != 0


def test_cli_prune_missing_state_fails(tmp_path):
    completed = _run_cli("prune", "--out", str(tmp_path), "--older-than", "7d")
    assert completed.returncode != 0
