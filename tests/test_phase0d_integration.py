"""Phase 0d: ledger integration with alias merge, pipeline, and the web snapshot."""

import asyncio
import os
import time
from pathlib import Path
from unittest.mock import patch

from core.comic.cas import object_path
from core.comic.gc import plan_gc
from core.comic.identity import merge_character_alias
from core.comic.ledger import ConsistencyLedger, LedgerEntry, ReferenceVersion
from core.comic.prune import parse_older_than
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
    asyncio.run(creative_comic(src, output_dir=str(tmp_path), chat=FakeChat(), image=FakeImage()))
    led = ConsistencyLedger.load(tmp_path / "consistency.json")
    assert "福贵" in led.characters
    pages = led.characters["福贵"].pages
    assert pages and all(p.startswith("c") and "-p" in p for p in pages)
    # First generation records version 1 and does NOT flag every page pending.
    assert led.characters["福贵"].reference.version == 1
    assert led.characters["福贵"].pending_pages == []
    ref = led.characters["福贵"].reference
    assert ref.content_hash and ref.content_hash.startswith("sha256:")
    blob = object_path(tmp_path, ref.content_hash)
    assert blob.read_bytes() == Path(ref.path).read_bytes()
    stamp = time.time() - 100 * 86400
    os.utime(blob, (stamp, stamp))
    plan = plan_gc(tmp_path, parse_older_than("7d"))
    assert blob.resolve() not in {candidate.path.resolve() for candidate in plan.candidates}


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_an_older_portrait_gains_a_hash_without_a_new_image(tmp_path, monkeypatch):
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "finished_page")
    src = "第一章\n福贵在村口。"
    image = FakeImage()
    asyncio.run(creative_comic(src, output_dir=str(tmp_path), chat=FakeChat(), image=image))
    led = ConsistencyLedger.load(tmp_path / "consistency.json")
    ref = led.characters["福贵"].reference
    version = ref.version
    pending = list(led.characters["福贵"].pending_pages)
    calls = image.calls
    object_path(tmp_path, ref.content_hash).unlink()
    ref.content_hash = None
    led.save(tmp_path / "consistency.json")

    asyncio.run(creative_comic(src, output_dir=str(tmp_path), chat=FakeChat(), image=image))

    restored = ConsistencyLedger.load(tmp_path / "consistency.json").characters["福贵"]
    assert restored.reference.version == version
    assert restored.pending_pages == pending
    assert restored.reference.content_hash and restored.reference.content_hash.startswith("sha256:")
    blob = object_path(tmp_path, restored.reference.content_hash)
    assert blob.read_bytes() == Path(restored.reference.path).read_bytes()
    assert image.calls == calls


def test_a_relative_portrait_is_bound_without_bumping_the_version(tmp_path):
    from core.pipelines.creative_comic import _bind_missing_portrait_hashes

    portrait = tmp_path / "assets" / "portraits" / "甲.png"
    portrait.parent.mkdir(parents=True)
    portrait.write_bytes(b"portrait-bytes")
    ledger = ConsistencyLedger(
        characters={
            "甲": LedgerEntry(
                reference=ReferenceVersion(path="assets/portraits/甲.png", version=4),
                pending_pages=["c0000-p0002"],
            )
        }
    )

    assert _bind_missing_portrait_hashes(ledger, tmp_path) is True

    ref = ledger.characters["甲"].reference
    assert ref.version == 4
    assert ledger.characters["甲"].pending_pages == ["c0000-p0002"]
    assert ref.content_hash and ref.content_hash.startswith("sha256:")
    assert object_path(tmp_path, ref.content_hash).read_bytes() == b"portrait-bytes"
    assert _bind_missing_portrait_hashes(ledger, tmp_path) is False


def test_a_missing_or_outside_portrait_stays_unhashed(tmp_path):
    from core.pipelines.creative_comic import _bind_missing_portrait_hashes

    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside-portrait.png"
    outside.write_bytes(b"outside")
    inside = project / "assets" / "portraits"
    inside.mkdir(parents=True)
    ledger = ConsistencyLedger(
        characters={
            "甲": LedgerEntry(
                reference=ReferenceVersion(path=str(inside / "missing.png"), version=3),
                pending_pages=["c0000-p0001"],
            ),
            "乙": LedgerEntry(
                reference=ReferenceVersion(path=str(outside), version=2),
            ),
        }
    )

    assert _bind_missing_portrait_hashes(ledger, project) is False
    assert ledger.characters["甲"].reference.content_hash is None
    assert ledger.characters["甲"].reference.version == 3
    assert ledger.characters["甲"].pending_pages == ["c0000-p0001"]
    assert ledger.characters["乙"].reference.content_hash is None
    assert not (project / "cas").exists()


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


def test_ledger_contents_do_not_enter_the_render_fingerprint():
    from core.pipelines.creative_comic import _render_fingerprint
    from core.schemas import ModelSnapshot

    snapshot = ModelSnapshot()
    before = _render_fingerprint(
        "manhua", snapshot=snapshot, panel_continuity=True, l3_enabled=False
    )
    # A ledger full of content must not perturb the fingerprint inputs at all.
    ConsistencyLedger(
        characters={"A": LedgerEntry(pages=["c0000-p0000"], pending_pages=["c0000-p0000"])}
    )
    after = _render_fingerprint(
        "manhua", snapshot=snapshot, panel_continuity=True, l3_enabled=False
    )
    assert before == after
