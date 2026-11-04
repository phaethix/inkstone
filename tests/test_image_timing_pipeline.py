"""Every image intent gets a result row with a sane duration."""

import asyncio
import json
from unittest.mock import patch

from core.pipelines.creative_comic import creative_comic
from tests.test_creative_comic import FakeChat, FakeImage
from tests.test_finished_page_pipeline import _fake_export_pdf


def _rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@patch("core.comic.export.ExportEngine.export_pdf", _fake_export_pdf)
def test_every_image_intent_has_a_result(tmp_path):
    src = "第一章\n方鸿渐在甲板上。\n第二章\n方鸿渐在读书。"
    asyncio.run(creative_comic(src, output_dir=str(tmp_path), chat=FakeChat(), image=FakeImage()))
    rows = _rows(tmp_path / "runs.jsonl")
    intents = [r for r in rows if r["kind"] == "intent" and r["stage"] in {"portrait", "render"}]
    results = [r for r in rows if r["kind"] == "result"]
    assert intents, "expected image intents"
    assert len(results) == len(intents)
    assert all(r["status"] == "ok" for r in results)
    assert all(isinstance(r["duration_ms"], int) and r["duration_ms"] >= 0 for r in results)
    assert {(r["stage"], r["key"]) for r in results} == {(r["stage"], r["key"]) for r in intents}
