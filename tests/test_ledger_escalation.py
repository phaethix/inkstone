"""Continuity escalation policy: threshold, confirmation, and the stop record (§4)."""

import json
import math
from pathlib import Path

from core.comic.gate import SAMPLE_GATE_SIZE
from core.comic.ledger import (
    LEDGER_ESCALATION_THRESHOLD,
    ConsistencyLedger,
    ContinuityReviewPaused,
    LedgerEntry,
    ReferenceVersion,
)
from core.comic.runs import append_run


def _entry(pages: int, *, version: int = 2, reviewed: int = 1) -> LedgerEntry:
    entry = LedgerEntry(pages=[f"c0000-p{i:04d}" for i in range(pages)])
    entry.reference = ReferenceVersion(path="p.png", content_hash=None, version=version)
    entry.reviewed_version = reviewed
    entry.pending_pages = list(entry.pages)
    return entry


def _rows(tmp_path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in (tmp_path / "runs.jsonl").read_text(encoding="utf-8").splitlines()
    ]


def test_escalation_threshold_is_held():
    # §4 invariant 11: "by default the same order as sample_gate_size, held by a test".
    assert LEDGER_ESCALATION_THRESHOLD == 30
    assert LEDGER_ESCALATION_THRESHOLD < 300
    assert 10 ** int(math.log10(LEDGER_ESCALATION_THRESHOLD)) == 10 ** int(
        math.log10(SAMPLE_GATE_SIZE)
    )


def test_escalated_is_strict_at_the_boundary():
    led = ConsistencyLedger(characters={"at": _entry(30), "over": _entry(31)})
    escalated = led.escalated()
    assert set(escalated) == {"over"}
    assert len(escalated["over"]) == 31


def test_escalated_accepts_a_smaller_threshold():
    led = ConsistencyLedger(characters={"a": _entry(2)})
    assert led.escalated() == {}
    assert set(led.escalated(threshold=1)) == {"a"}


def test_escalated_ignores_characters_without_pending():
    led = ConsistencyLedger(characters={"quiet": _entry(0)})
    assert led.escalated() == {}


def test_confirm_review_clears_pending_and_catches_up():
    entry = _entry(40)
    led = ConsistencyLedger(characters={"A": entry})
    assert led.confirm_review("A") is True
    assert entry.pending_pages == []
    assert entry.reviewed_version == entry.reference.version == 2
    assert led.escalated() == {}
    assert led.confirm_review("A") is False  # idempotent: nothing left to change


def test_confirm_review_unknown_character_is_a_noop():
    led = ConsistencyLedger()
    assert led.confirm_review("nobody") is False


def test_confirm_review_leaves_other_characters_alone():
    led = ConsistencyLedger(characters={"A": _entry(40), "B": _entry(40)})
    led.confirm_review("A")
    assert led.characters["B"].pending_pages == led.characters["B"].pages
    assert led.characters["B"].reviewed_version == 1
    assert set(led.escalated()) == {"B"}


def test_confirm_review_persists(tmp_path: Path):
    path = tmp_path / "consistency.json"
    led = ConsistencyLedger(characters={"A": _entry(40)})
    led.confirm_review("A")
    led.save(path)
    loaded = ConsistencyLedger.load(path)
    assert loaded.characters["A"].pending_pages == []
    assert loaded.characters["A"].reviewed_version == 2
    assert loaded.escalated() == {}


def test_record_escalation_stop_writes_once_and_dedupes(tmp_path: Path):
    led = ConsistencyLedger(characters={"A": _entry(40)})
    assert led.record_escalation_stop(tmp_path) is True
    assert led.record_escalation_stop(tmp_path) is False  # unchanged boundary
    rows = _rows(tmp_path)
    assert len(rows) == 1
    assert rows[0]["kind"] == "stop"
    assert rows[0]["stage"] == "ledger"
    assert rows[0]["reason"] == "continuity_review"
    assert rows[0]["characters"] == {"A": {"pending": 40, "version": 2}}


def test_record_escalation_stop_records_a_changed_boundary(tmp_path: Path):
    led = ConsistencyLedger(characters={"A": _entry(40)})
    assert led.record_escalation_stop(tmp_path) is True
    led.characters["A"].reference = ReferenceVersion(path="p.png", version=3)
    assert led.record_escalation_stop(tmp_path) is True
    assert len(_rows(tmp_path)) == 2


def test_record_escalation_stop_below_threshold_writes_nothing(tmp_path: Path):
    led = ConsistencyLedger(characters={"A": _entry(30)})
    assert led.record_escalation_stop(tmp_path) is False
    assert not (tmp_path / "runs.jsonl").exists()


def test_record_escalation_stop_scans_past_other_stops(tmp_path: Path):
    append_run(tmp_path, {"kind": "stop", "stage": "budget", "reason": "budget", "key": "x"})
    led = ConsistencyLedger(characters={"A": _entry(40)})
    assert led.record_escalation_stop(tmp_path) is True
    assert led.record_escalation_stop(tmp_path) is False
    stages = [row["stage"] for row in _rows(tmp_path)]
    assert stages == ["budget", "ledger"]


def test_continuity_review_paused_names_the_characters():
    exc = ContinuityReviewPaused({"A": ["p1", "p2"], "B": ["p3"]})
    assert "awaiting_human" in str(exc)
    assert "A (2 pages)" in str(exc)
    assert "B (1 page)" in str(exc)
    assert exc.escalated == {"A": ["p1", "p2"], "B": ["p3"]}
