"""Phase 5b: a render batch larger than the sample gate needs a human.

Design §12. The initial sample is 30 pages and must span two chunks when the
book has two. Pages beyond that sample are not rendered until the sample is
accepted, or until an explicit ``--yes`` is audited. The gate never enters a
fingerprint. The size moves by the acceptance-rate rule and cannot become
"the whole book" by default.
"""

import json

import pytest

from core.comic.gate import (
    SAMPLE_GATE_SIZE,
    GateSession,
    adjust_sample_size,
    select_sample,
)
from core.pipelines.creative_comic import _input_fingerprint, _render_fingerprint


def _keys(chunk: int, count: int) -> list[str]:
    return [f"c{chunk:04d}-p{index:04d}" for index in range(count)]


def _open(tmp_path, *, size=30, yes=False):
    return GateSession.open(
        data_dir=tmp_path / "data",
        output_dir=tmp_path / "out",
        project_id="p",
        yes=yes,
        sample_size=size,
    )


def _lines(path):
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_default_sample_is_thirty_and_not_the_whole_book():
    assert SAMPLE_GATE_SIZE == 30
    assert SAMPLE_GATE_SIZE < 300


def test_sample_spans_two_chunks_when_the_book_does():
    keys = _keys(0, 40) + _keys(1, 10)
    sample = select_sample(keys, 30)
    assert len(sample) == 30
    assert {key.split("-", 1)[0] for key in sample} == {"c0000", "c0001"}


def test_a_single_chunk_sample_takes_the_first_pages():
    assert select_sample(_keys(0, 40), 30) == _keys(0, 30)


def test_incremental_admission_matches_select_sample(tmp_path):
    keys = _keys(0, 40) + _keys(1, 5)
    gate = _open(tmp_path, size=30)
    admitted = [key for key in keys if gate.allow(key, int(key[1:5]), total_chunks=2)]
    assert admitted == select_sample(keys, 30)


def test_pages_beyond_the_sample_pause_once(tmp_path):
    gate = _open(tmp_path, size=1)
    assert gate.allow("c0000-p0000", 0, total_chunks=1) is True
    assert gate.allow("c0000-p0001", 0, total_chunks=1) is False
    assert gate.allow("c0000-p0002", 0, total_chunks=1) is False
    stops = _lines(tmp_path / "out" / "runs.jsonl")
    assert len(stops) == 1
    assert stops[0]["reason"] == "awaiting_human"
    assert stops[0]["key"] == "c0000-p0001"
    assert gate.awaiting


def test_yes_is_audited_and_releases_the_remainder(tmp_path):
    gate = _open(tmp_path, size=1, yes=True)
    assert gate.allow("c0000-p0000", 0, total_chunks=1) is True
    assert gate.allow("c0000-p0001", 0, total_chunks=1) is True
    assert gate.awaiting is False
    audit = _lines(tmp_path / "out" / "runs.jsonl")
    assert audit[0]["kind"] == "gate"
    assert audit[0]["yes"] is True
    # Opening again with the same switch does not append a second audit line.
    _open(tmp_path, size=1, yes=True)
    assert len(_lines(tmp_path / "out" / "runs.jsonl")) == 1


def test_acceptance_rate_moves_the_size_and_stays_a_sample():
    grown = adjust_sample_size(30, 0.95)
    assert grown == round(30 * 1.618)
    assert grown < 300
    assert adjust_sample_size(30, 0.95, chapter_pages=40) == 40
    assert adjust_sample_size(30, 0.7) == 30
    assert adjust_sample_size(30, 0.4) == 15


def test_an_accepted_sample_releases_the_rest_and_records_the_rate(tmp_path):
    gate = _open(tmp_path, size=1)
    assert gate.allow("c0000-p0000", 0, total_chunks=1) is True
    assert gate.allow("c0000-p0001", 0, total_chunks=1) is False
    released = gate.record("c0000-p0000", "accept")
    assert released.released is True
    assert released.sample_size == round(1 * 1.618)
    assert gate.allow("c0000-p0001", 0, total_chunks=1) is True
    kinds = [row["kind"] for row in _lines(tmp_path / "out" / "runs.jsonl")]
    assert "gate" in kinds


def test_a_known_chapter_caps_the_grown_sample(tmp_path):
    gate = _open(tmp_path, size=30)
    for index in range(30):
        assert gate.allow(f"c0000-p{index:04d}", 0, total_chunks=1) is True
    for index in range(30):
        gate.record(f"c0000-p{index:04d}", "accept", chapter_pages=40)
    assert gate.project.released is True
    assert gate.project.sample_size == 40


def test_accept_and_flag_counts_as_accepted(tmp_path):
    gate = _open(tmp_path, size=1)
    assert gate.allow("c0000-p0000", 0, total_chunks=1) is True
    gate.record("c0000-p0000", "accept-and-flag", reason_tier="page")
    assert gate.project.released is True


def test_a_mixed_sample_locks_the_size_and_records_the_bible_tier(tmp_path):
    gate = _open(tmp_path, size=2)
    assert gate.allow("c0000-p0000", 0, total_chunks=1) is True
    assert gate.allow("c0000-p0001", 0, total_chunks=1) is True
    gate.record("c0000-p0000", "accept")
    gate.record("c0000-p0001", "redraw", reason_tier="bible")
    assert gate.project.released is False
    assert gate.project.sample_size == 2
    logged = [row for row in _lines(tmp_path / "out" / "runs.jsonl") if row["kind"] == "gate"]
    assert logged[-1]["acceptance_rate"] == 0.5
    assert logged[-1]["reason_tiers"] == {"bible": 1}


def test_redraw_marks_the_page_stale(tmp_path):
    from core.schemas import ProjectState

    gate = _open(tmp_path, size=1)
    gate.allow("c0000-p0000", 0, total_chunks=1)
    state = ProjectState(project_id="p", pages_done=["c0000-p0000"])
    gate.record("c0000-p0000", "redraw", reason_tier="page", state=state)
    assert state.stale_pages == ["c0000-p0000"]


def test_a_redraw_keeps_the_remainder_closed(tmp_path):
    gate = _open(tmp_path, size=1)
    gate.allow("c0000-p0000", 0, total_chunks=1)
    gate.allow("c0000-p0001", 0, total_chunks=1)
    gate.record("c0000-p0000", "redraw", reason_tier="bible")
    assert gate.allow("c0000-p0000", 0, total_chunks=1) is True
    assert gate.allow("c0000-p0001", 0, total_chunks=1) is False
    assert gate.project.released is False


def test_unknown_decision_is_rejected(tmp_path):
    gate = _open(tmp_path, size=1)
    with pytest.raises(ValueError):
        gate.record("c0000-p0000", "maybe")


def test_gate_does_not_enter_fingerprint_inputs():
    for fn in (_input_fingerprint, _render_fingerprint):
        names = fn.__code__.co_varnames
        assert "gate" not in names
        assert "sample_gate_size" not in names
        assert "gate_yes" not in names
