"""Structured per-image-call timing records in runs.jsonl."""

import json
from pathlib import Path
from unittest.mock import patch

from core.comic.runs import image_result


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_image_result_writes_a_structured_row(tmp_path):
    image_result(tmp_path, stage="render", key="c0000-p0000", duration_ms=42380, status="ok")
    rows = _rows(tmp_path / "runs.jsonl")
    assert len(rows) == 1
    assert rows[0]["kind"] == "result"
    assert rows[0]["stage"] == "render"
    assert rows[0]["key"] == "c0000-p0000"
    assert rows[0]["duration_ms"] == 42380
    assert rows[0]["duration_ms"] >= 0
    assert rows[0]["status"] == "ok"
    assert rows[0]["ts"]


def test_image_result_writes_no_prompt_or_error_text(tmp_path):
    image_result(tmp_path, stage="portrait", key="沈知意", duration_ms=0, status="error")
    text = (tmp_path / "runs.jsonl").read_text(encoding="utf-8")
    assert "secret provider text" not in text
    assert "draw this" not in text
    row = json.loads(text.splitlines()[0])
    assert set(row) == {"kind", "stage", "key", "duration_ms", "status", "ts"}


def test_image_result_appends_without_overwriting(tmp_path):
    image_result(tmp_path, stage="render", key="p0", duration_ms=1, status="ok")
    image_result(tmp_path, stage="render", key="p1", duration_ms=2, status="error")
    rows = _rows(tmp_path / "runs.jsonl")
    assert [r["key"] for r in rows] == ["p0", "p1"]
    assert [r["status"] for r in rows] == ["ok", "error"]


def test_image_result_swallows_append_errors(tmp_path):
    with patch("core.comic.runs.append_run", side_effect=OSError("disk full")):
        image_result(tmp_path, stage="render", key="p0", duration_ms=5, status="ok")
    assert not (tmp_path / "runs.jsonl").exists()
