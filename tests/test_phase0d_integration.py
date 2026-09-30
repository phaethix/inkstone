"""Phase 0d: ledger integration with alias merge, pipeline, and the web snapshot."""

import asyncio
from unittest.mock import patch

from core.comic.identity import merge_character_alias
from core.comic.ledger import ConsistencyLedger, LedgerEntry
from core.pipelines.creative_comic import creative_comic
from core.schemas import CharacterAsset, ProjectState
from tests.test_finished_page_pipeline import FakeChat, FakeImage, _fake_export_pdf


def test_alias_merge_lists_shared_pages_from_ledger():
    state = ProjectState(project_id="t")
    state.characters["鸿渐"] = CharacterAsset(name="鸿渐")
    state.characters["方鸿渐"] = CharacterAsset(name="方鸿渐")
    led = ConsistencyLedger(
        characters={
            "鸿渐": LedgerEntry(pages=["c0000-p0003"]),
            "方鸿渐": LedgerEntry(pages=["c0001-p0000"]),
        }
    )
    stale = merge_character_alias(state, "鸿渐", "方鸿渐", ledger=led)
    assert stale == []
    assert "c0000-p0003" in state.stale_pages
    assert "鸿渐" not in led.characters
    assert led.characters["方鸿渐"].pages == ["c0001-p0000", "c0000-p0003"]


def test_alias_merge_without_ledger_keeps_cache_walk():
    state = ProjectState(project_id="t")
    state.characters["a"] = CharacterAsset(name="a")
    state.characters["b"] = CharacterAsset(name="b")
    # No page_cache entry -> nothing to invalidate; must not raise.
    assert merge_character_alias(state, "a", "b") == []


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_pipeline_writes_ledger_with_positional_pages(tmp_path, monkeypatch):
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "finished_page")
    src = "第一章\n福贵在村口。"
    asyncio.run(
        creative_comic(src, output_dir=str(tmp_path), chat=FakeChat(), image=FakeImage())
    )
    led = ConsistencyLedger.load(tmp_path / "consistency.json")
    assert "福贵" in led.characters
    pages = led.characters["福贵"].pages
    assert pages and all(p.startswith("c") and "-p" in p for p in pages)
    # First generation records version 1 and does NOT flag every page pending.
    assert led.characters["福贵"].reference.version == 1
    assert led.characters["福贵"].pending_pages == []


def test_state_snapshot_includes_ledger_pending(monkeypatch, tmp_path):
    from web import server

    monkeypatch.setattr(server, "OUTPUT_DIR", tmp_path)
    state = ProjectState(project_id="p1")
    snap = server._state_snapshot(state)
    assert snap["ledger_pending"] == {}


def test_identity_view_prints_ledger(tmp_path, capsys):
    from core import cli

    out = tmp_path / "proj"
    out.mkdir()
    ProjectState(project_id="p1").save(out / "state.json")
    ConsistencyLedger(characters={"福贵": LedgerEntry(pages=["c0000-p0000"])}).save(
        out / "consistency.json"
    )
    code = cli._run_identity(type("A", (), {"out": str(out), "view": True})())
    assert code == 0
    assert "福贵" in capsys.readouterr().out