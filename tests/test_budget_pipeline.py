"""Phase 5a: the pipeline stops at a page boundary when the budget is spent.

The accounting rules live in tests/test_budget.py. These tests pin the
integration: a render reservation of zero never calls the page image API,
a resume at the same boundary does not append a second stop or issue the
call, and the unattended supervisor treats the pause as terminal for this
run rather than as a transient error to retry.
"""

import asyncio
import json
from unittest.mock import patch

import pytest

from core.comic.budget import BudgetPaused, BudgetSpec
from core.pipelines.creative_comic import creative_comic
from core.pipelines.run_until_complete import PausedRun, run_until_complete
from core.schemas import ProjectState
from tests.test_finished_page_pipeline import FakeChat, FakeImage, _fake_export_pdf

_PATCH = "core.pipelines.creative_comic.ExportEngine.export_pdf"
_SRC = "第一章\n福贵在村口。"


def _spec(*, render: int) -> BudgetSpec:
    chat = {
        "extract": 10,
        "bible": 10,
        "beats": 10,
        "page_plan": 10,
        "portrait": 10,
        "render": render,
    }
    return BudgetSpec(total=sum(chat.values()) + 1, recovery_reserve=1, reservations=chat)


def _run(tmp_path, image, *, spec):
    return asyncio.run(
        creative_comic(
            _SRC,
            output_dir=str(tmp_path / "out"),
            chat=FakeChat(),
            image=image,
            budget=spec,
            data_dir=tmp_path / "data",
        )
    )


def _stops(tmp_path):
    path = tmp_path / "out" / "runs.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _quota_stages(tmp_path):
    path = tmp_path / "data" / "quota.jsonl"
    return [
        json.loads(line)["stage"] for line in path.read_text(encoding="utf-8").splitlines() if line
    ]


def test_a_zero_render_budget_pauses_before_the_page_image(tmp_path, monkeypatch):
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "finished_page")
    image = FakeImage()
    with pytest.raises(BudgetPaused) as raised:
        _run(tmp_path, image, spec=_spec(render=0))

    assert raised.value.stage == "render"
    assert raised.value.key == "c0000-p0000"
    assert raised.value.carry_over is False
    state = ProjectState.load(tmp_path / "out" / "state.json")
    assert state.pages_done == []
    assert _stops(tmp_path)[0]["key"] == "c0000-p0000"
    # The portrait is a different stage and may have been spent. The page was not.
    assert "render" not in _quota_stages(tmp_path)


def test_resume_does_not_reissue_the_paused_page_or_a_second_stop(tmp_path, monkeypatch):
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "finished_page")
    with pytest.raises(BudgetPaused):
        _run(tmp_path, FakeImage(), spec=_spec(render=0))

    image = FakeImage()
    with pytest.raises(BudgetPaused):
        _run(tmp_path, image, spec=_spec(render=0))

    assert image.calls == 0
    assert len(_stops(tmp_path)) == 1


@patch(_PATCH, _fake_export_pdf)
def test_a_budget_that_covers_the_page_records_each_call_once(tmp_path, monkeypatch):
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "finished_page")
    image = FakeImage()
    proj = _run(tmp_path, image, spec=_spec(render=1))

    assert proj.state.pages_done == ["c0000-p0000"]
    assert image.calls == 2  # one portrait, one page
    assert _quota_stages(tmp_path) == [
        "extract",
        "bible",
        "portrait",
        "beats",
        "page_plan",
        "render",
    ]


def test_supervisor_returns_a_paused_run_without_retrying(tmp_path, monkeypatch):
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "finished_page")
    result = asyncio.run(
        run_until_complete(
            _SRC,
            output_dir=str(tmp_path / "out"),
            chat=FakeChat(),
            image=FakeImage(),
            budget=_spec(render=0),
            data_dir=tmp_path / "data",
        )
    )
    assert isinstance(result, PausedRun)
    assert "budget exhausted" in result.reason
    assert len(_stops(tmp_path)) == 1
