"""Tests for cached vision-placement orchestration (Task 1)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from PIL import Image

from core.comic import vision_layout as vl
from core.schemas import ComicPagePlan


def _plan() -> ComicPagePlan:
    return ComicPagePlan.model_validate(
        {
            "page_id": "p1",
            "panels": [
                {"panel_id": "1", "dialogue": "这首歌，倒是比人坦诚。"},
            ],
        }
    )


def _blank() -> Image.Image:
    return Image.new("RGB", (832, 1248), (60, 80, 110))


def test_placement_cache_key_is_stable_and_content_sensitive():
    b = b"blank-bytes"
    items = [("dialogue", "你好")]
    k = vl.placement_cache_key(b, items, "agnes-3.0-flash", "place_v2")
    assert k == vl.placement_cache_key(b, items, "agnes-3.0-flash", "place_v2")
    assert k != vl.placement_cache_key(b"other", items, "agnes-3.0-flash", "place_v2")
    assert k != vl.placement_cache_key(b, [("dialogue", "别的")], "agnes-3.0-flash", "place_v2")
    assert k != vl.placement_cache_key(b, items, "other-model", "place_v2")
    assert k != vl.placement_cache_key(b, items, "agnes-3.0-flash", "place_v3")


def test_vision_placement_boxes_uses_cache_without_calling_model(tmp_path, monkeypatch):
    blank = _blank()
    plan = _plan()

    # Pre-seed a cache entry, then make any model call explode.
    def _boom(*_a, **_k):
        raise AssertionError("model must not be called on a cache hit")

    monkeypatch.setattr(vl, "_get_chat", _boom)
    src = "原文"
    items = [(k, t) for _p, k, t, _f in vl.resolve_lettering_jobs(plan, source_text=src)]
    key = vl.placement_cache_key(
        vl._blank_bytes(blank), items, vl.MODEL, vl.PLACEMENT_PROMPT_VERSION
    )
    cached_boxes = [[100, 100, 120, 40]]
    (tmp_path / f"{key}.json").write_text(json.dumps({"boxes": cached_boxes}))

    out = asyncio.run(vl.vision_placement_boxes(blank, plan, src, tmp_path))
    assert out == [tuple(cached_boxes[0])]


def test_vision_placement_boxes_returns_none_on_model_failure(tmp_path, monkeypatch):
    async def _fail(*_a, **_k):
        raise RuntimeError("model down")

    class _BadChat:
        model = "x"

        async def chat_function_call(self, *_a, **_k):
            raise RuntimeError("model down")

    monkeypatch.setattr(vl, "_get_chat", lambda: _BadChat())
    out = asyncio.run(vl.vision_placement_boxes(_blank(), _plan(), "原文", tmp_path))
    assert out is None


def test_vision_placement_boxes_shrinks_boxes_to_text(tmp_path, monkeypatch):
    class _FakeChat:
        model = "fake"

        async def chat_function_call(self, messages, tools, tool_choice, **_k):
            # Model returns one oversized box.
            return {"placements": [{"text": "x", "x": 0.05, "y": 0.05, "w": 0.5, "h": 0.3}]}

    monkeypatch.setattr(vl, "_get_chat", lambda: _FakeChat())
    out = asyncio.run(vl.vision_placement_boxes(_blank(), _plan(), "原文", tmp_path))
    assert out is not None and len(out) == 1
    x, y, w, h = out[0]
    assert w < 0.5 * 832 and h < 0.3 * 1248, "box was not shrunk to text size"
    assert 0 <= x and 0 <= y and x + w <= 832 and y + h <= 1248
    # A cache file was written for reuse.
    assert list(Path(tmp_path).glob("*.json"))
