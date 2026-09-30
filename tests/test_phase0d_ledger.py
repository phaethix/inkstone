"""Phase 0d: the consistency ledger schema and persistence (§12)."""

from pathlib import Path

from core.comic.ledger import ConsistencyLedger, LedgerEntry, ReferenceVersion


def test_empty_ledger_round_trips(tmp_path: Path):
    led = ConsistencyLedger()
    path = tmp_path / "consistency.json"
    led.save(path)
    loaded = ConsistencyLedger.load(path)
    assert loaded == led
    assert loaded.schema_version == 1


def test_entry_round_trips(tmp_path: Path):
    led = ConsistencyLedger(
        characters={
            "方鸿渐": LedgerEntry(
                pages=["c0000-p0002", "c0001-p0005"],
                reference=ReferenceVersion(
                    path="assets/portraits/方鸿渐.png", content_hash=None, version=1
                ),
                pending_pages=["c0001-p0005"],
            )
        }
    )
    path = tmp_path / "consistency.json"
    led.save(path)
    loaded = ConsistencyLedger.load(path)
    assert loaded == led
    assert loaded.characters["方鸿渐"].reference.version == 1
    assert loaded.characters["方鸿渐"].reference.content_hash is None


def test_load_missing_file_returns_empty(tmp_path: Path):
    loaded = ConsistencyLedger.load(tmp_path / "absent.json")
    assert loaded.characters == {}