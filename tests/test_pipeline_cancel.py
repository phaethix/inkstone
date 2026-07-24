"""tests/test_pipeline_cancel.py — cooperative cancel primitive + checkpoints."""

import asyncio

from core.pipelines.cancel import PipelineCancelled, check_cancel
from core.pipelines.run_until_complete import PausedRun, run_until_complete
from core.schemas import ProjectState


def test_check_cancel_raises_when_true():
    try:
        check_cancel(lambda: True)
        raise AssertionError("expected PipelineCancelled")
    except PipelineCancelled as exc:
        assert "stopped by user" in exc.reason


def test_check_cancel_noop_when_false_or_none():
    check_cancel(None)
    check_cancel(lambda: False)


def test_run_until_complete_maps_cancel_to_paused(tmp_path, monkeypatch):
    calls = {"n": 0}

    async def fake_creative(*args, **kwargs):
        kwargs.get("cancel_check")
        calls["n"] += 1
        if calls["n"] == 1:
            raise PipelineCancelled()
        raise AssertionError("should not retry after user cancel")

    monkeypatch.setattr(
        "core.pipelines.run_until_complete.creative_comic",
        fake_creative,
    )
    out = tmp_path / "p1"
    out.mkdir()
    ProjectState(project_id="p1").save(out / "state.json")

    result = asyncio.run(
        run_until_complete("hello", output_dir=str(out), project_id="p1", cancel_check=lambda: True)
    )
    assert isinstance(result, PausedRun)
    assert "stopped by user" in result.reason


def test_creative_comic_honors_cancel_at_chunk_boundary(tmp_path, monkeypatch):
    import core.pipelines.creative_comic as cc
    from core.pipelines.cancel import PipelineCancelled
    from core.pipelines.creative_comic import creative_comic

    monkeypatch.setattr(cc, "segment_text", lambda _t: ["a", "b"])
    monkeypatch.setattr(cc, "get_chat_provider", lambda: object())
    monkeypatch.setattr(cc, "get_image_provider", lambda: object())
    monkeypatch.setattr(cc, "_model_snapshot", lambda *_a, **_k: cc.ModelSnapshot())
    monkeypatch.setattr(cc, "_reconcile_state", lambda *_a, **_k: None)

    hits = {"extract": 0}

    async def boom_extract(text, *, chat=None):
        hits["extract"] += 1
        raise AssertionError("extract should not run when already cancelled")

    monkeypatch.setattr(cc, "extract_story_elements", boom_extract)

    cancel = {"go": True}  # already cancelled before first chunk work

    try:
        asyncio.run(
            creative_comic(
                "x",
                output_dir=str(tmp_path / "out"),
                project_id="c1",
                cancel_check=lambda: cancel["go"],
            )
        )
        raise AssertionError("expected cancel")
    except PipelineCancelled:
        pass
    assert hits["extract"] == 0
