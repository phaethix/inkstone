"""Phase 5b: a book larger than the sample renders the sample and then pauses.

Chat planning of a later chunk still runs, so the reserved second-chunk seat
can be filled. The image call for a held page is not issued, and a resume at
the same boundary does not append a second stop or issue the call.
"""

import asyncio
import json
from unittest.mock import patch

import pytest

from core.comic.gate import GatePaused
from core.pipelines.creative_comic import creative_comic
from core.pipelines.run_until_complete import PausedRun, run_until_complete
from core.schemas import ProjectState
from tests.test_finished_page_pipeline import FakeChat, FakeImage, _fake_export_pdf

_PATCH = "core.pipelines.creative_comic.ExportEngine.export_pdf"
_SRC = "第一章\n福贵在村口。\n第二章\n福贵在读书。"


def _run(tmp_path, image, *, yes=False, size=1):
    return asyncio.run(
        creative_comic(
            _SRC,
            output_dir=str(tmp_path / "out"),
            chat=FakeChat(),
            image=image,
            data_dir=tmp_path / "data",
            gate_yes=yes,
            sample_gate_size=size,
        )
    )


def _lines(tmp_path):
    path = tmp_path / "out" / "runs.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


@patch(_PATCH, _fake_export_pdf)
def test_a_second_chunk_page_is_held_after_the_sample(tmp_path, monkeypatch):
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "finished_page")
    image = FakeImage()
    with pytest.raises(GatePaused) as raised:
        _run(tmp_path, image)

    assert "awaiting_human" in str(raised.value)
    assert raised.value.key == "c0001-p0000"
    state = ProjectState.load(tmp_path / "out" / "state.json")
    assert state.pages_done == ["c0000-p0000"]
    stops = [row for row in _lines(tmp_path) if row["kind"] == "stop"]
    assert len(stops) == 1
    assert stops[0]["reason"] == "awaiting_human"
    assert stops[0]["key"] == "c0001-p0000"


@patch(_PATCH, _fake_export_pdf)
def test_resume_does_not_reissue_the_held_page_or_a_second_stop(tmp_path, monkeypatch):
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "finished_page")
    with pytest.raises(GatePaused):
        _run(tmp_path, FakeImage())

    image = FakeImage()
    with pytest.raises(GatePaused):
        _run(tmp_path, image)

    assert image.calls == 0
    stops = [row for row in _lines(tmp_path) if row["kind"] == "stop"]
    assert len(stops) == 1


@patch(_PATCH, _fake_export_pdf)
def test_yes_renders_the_remainder_and_is_audited_once(tmp_path, monkeypatch):
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "finished_page")
    image = FakeImage()
    proj = _run(tmp_path, image, yes=True)

    assert proj.state.pages_done == ["c0000-p0000", "c0001-p0000"]
    audit = [row for row in _lines(tmp_path) if row.get("yes") is True]
    assert len(audit) == 1

    again = FakeImage()
    _run(tmp_path, again, yes=True)
    assert again.calls == 0
    assert len([row for row in _lines(tmp_path) if row.get("yes") is True]) == 1


def test_supervisor_returns_a_paused_run_without_retrying(tmp_path, monkeypatch):
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "finished_page")
    with patch(_PATCH, _fake_export_pdf):
        result = asyncio.run(
            run_until_complete(
                _SRC,
                output_dir=str(tmp_path / "out"),
                chat=FakeChat(),
                image=FakeImage(),
                data_dir=tmp_path / "data",
                sample_gate_size=1,
            )
        )

    assert isinstance(result, PausedRun)
    assert "awaiting_human" in result.reason
    stops = [row for row in _lines(tmp_path) if row["kind"] == "stop"]
    assert len(stops) == 1
