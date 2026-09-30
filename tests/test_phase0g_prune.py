"""Phase 0g — minimal reclaimer (prune)."""

from __future__ import annotations

import os
import time
from datetime import timedelta
from pathlib import Path

import pytest

from core.comic.ledger import ConsistencyLedger, LedgerEntry
from core.comic.prune import collect_live_refs, parse_older_than, plan_prune
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
    assert plan.total_bytes == 6


def test_plan_prune_missing_state_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        plan_prune(tmp_path, parse_older_than("7d"))