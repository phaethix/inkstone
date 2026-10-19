"""Continuity escalation at the render boundary (§4 invariant 11, zero quota)."""

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from core.comic.ledger import ConsistencyLedger, ContinuityReviewPaused
from core.pipelines.creative_comic import creative_comic
from core.pipelines.run_until_complete import PausedRun, run_until_complete
from core.schemas import ProjectState
from tests.test_finished_page_pipeline import FakeChat, FakeImage, _fake_export_pdf

SRC = "第一章\n福贵在村口。"


def _page(index: int) -> dict:
    return {
        "page_id": f"u1_p{index:04d}",
        "purpose": "establish the village entrance",
        "layout_intent": "wide establishing top, inset reaction bottom-right",
        "panels": [
            {
                "panel_id": "1",
                "role": "establishing",
                "shape_hint": "wide",
                "shot": "wide",
                "action": "福贵 walks through the village entrance",
                "characters": ["福贵"],
                "setting_ref": "村口",
                "caption": "傍晚，村口。",
                "dialogue": [{"speaker": "福贵", "text": "我回来了。"}],
            }
        ],
        "reference_characters": ["福贵"],
        "setting_refs": ["村口"],
    }


class TwoPageChat(FakeChat):
    """Plans two pages for the single chunk so one character owns two pages."""

    async def chat_function_call(self, messages, tools, tool_choice, **kw):
        if tool_choice["function"]["name"] == "plan_comic_pages":
            self.calls += 1
            self.page_plan_calls += 1
            return {"unit_id": str(self.page_plan_calls), "pages": [_page(1), _page(2)]}
        return await super().chat_function_call(messages, tools, tool_choice, **kw)


def _run_book(tmp_path: Path, chat=None, image=None):
    return asyncio.run(
        creative_comic(
            SRC,
            output_dir=str(tmp_path),
            chat=chat or TwoPageChat(),
            image=image or FakeImage(),
        )
    )


def _bump_reference(tmp_path: Path) -> ConsistencyLedger:
    """Simulate a portrait regeneration: version 2, reviewed version 1."""
    state = ProjectState.load(tmp_path / "state.json")
    ledger = ConsistencyLedger.load(tmp_path / "consistency.json")
    ledger.record_reference("福贵", state.generated.portraits["福贵"])
    assert ledger.rebuild_from_state(state) is True
    ledger.save(tmp_path / "consistency.json")
    return ledger


def _mark_page_stale(tmp_path: Path) -> None:
    """Force the next run to repaint page 1 (the way a content invalidation does)."""
    state = ProjectState.load(tmp_path / "state.json")
    state.stale_pages = ["c0000-p0000"]
    state.pages_done = [k for k in state.pages_done if k != "c0000-p0000"]
    state.save(tmp_path / "state.json")


def _ledger_stops(tmp_path: Path) -> list[dict]:
    path = tmp_path / "runs.jsonl"
    if not path.is_file():
        return []
    return [
        row
        for row in (json.loads(line) for line in path.read_text(encoding="utf-8").splitlines())
        if row.get("stage") == "ledger" and row.get("reason") == "continuity_review"
    ]


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_escalated_ledger_holds_the_render_phase(tmp_path, monkeypatch):
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "finished_page")
    monkeypatch.setattr("core.comic.ledger.LEDGER_ESCALATION_THRESHOLD", 1)

    _run_book(tmp_path)
    _bump_reference(tmp_path)
    _mark_page_stale(tmp_path)

    # Above the threshold: the run stops before issuing render work.
    chat2, img2 = TwoPageChat(), FakeImage()
    with pytest.raises(ContinuityReviewPaused) as err:
        _run_book(tmp_path, chat=chat2, image=img2)
    assert "awaiting_human" in str(err.value)
    assert "福贵 (2 pages)" in str(err.value)
    assert img2.calls == 0
    assert chat2.calls == 0

    stops = _ledger_stops(tmp_path)
    assert len(stops) == 1
    assert stops[0]["characters"] == {"福贵": {"pending": 2, "version": 2}}

    # An unconfirmed resume pauses again without a second stop record (§6).
    with pytest.raises(ContinuityReviewPaused):
        _run_book(tmp_path, chat=TwoPageChat(), image=FakeImage())
    assert len(_ledger_stops(tmp_path)) == 1

    # Confirmation clears the review; the held repaint then goes ahead.
    ledger = ConsistencyLedger.load(tmp_path / "consistency.json")
    assert ledger.confirm_review("福贵") is True
    ledger.save(tmp_path / "consistency.json")

    chat3, img3 = TwoPageChat(), FakeImage()
    proj3 = _run_book(tmp_path, chat=chat3, image=img3)
    assert img3.calls == 1  # exactly the held repaint
    assert chat3.calls == 0
    assert "c0000-p0000" in proj3.state.pages_done
    assert "c0000-p0000" not in proj3.state.stale_pages
    assert len(_ledger_stops(tmp_path)) == 1


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_pending_below_the_threshold_stays_quiet(tmp_path, monkeypatch):
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "finished_page")

    _run_book(tmp_path)
    _bump_reference(tmp_path)
    _mark_page_stale(tmp_path)

    chat2, img2 = TwoPageChat(), FakeImage()
    proj = _run_book(tmp_path, chat=chat2, image=img2)
    assert img2.calls == 1  # the repaint proceeds; nothing blocks
    assert chat2.calls == 0
    assert _ledger_stops(tmp_path) == []
    # Quiet is not cleared: the panel still has the pending set to show.
    assert ConsistencyLedger.load(tmp_path / "consistency.json").pending_pages() == {
        "福贵": ["c0000-p0000", "c0000-p0001"]
    }
    assert "c0000-p0000" in proj.state.pages_done


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_run_until_complete_reports_the_review_pause(tmp_path, monkeypatch):
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "finished_page")
    monkeypatch.setattr("core.comic.ledger.LEDGER_ESCALATION_THRESHOLD", 1)

    first = asyncio.run(
        run_until_complete(
            SRC,
            output_dir=str(tmp_path),
            project_id="book",
            chat=TwoPageChat(),
            image=FakeImage(),
        )
    )
    assert not isinstance(first, PausedRun)

    _bump_reference(tmp_path)
    result = asyncio.run(
        run_until_complete(
            SRC,
            output_dir=str(tmp_path),
            project_id="book",
            chat=TwoPageChat(),
            image=FakeImage(),
        )
    )
    assert isinstance(result, PausedRun)
    assert "awaiting_human" in result.reason
    assert "continuity review" in result.reason
    assert result.state is not None
