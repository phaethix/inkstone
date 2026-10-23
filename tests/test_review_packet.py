"""The review packet is human evidence, never model context."""

import asyncio
import json
from unittest.mock import patch

from core.pipelines.creative_comic import creative_comic
from tests.test_finished_page_pipeline import FakeChat, FakeImage, _fake_export_pdf


@patch("core.comic.export.ExportEngine.export_pdf", _fake_export_pdf)
def test_a_run_writes_a_review_packet(tmp_path, monkeypatch):
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "finished_page")
    asyncio.run(
        creative_comic(
            "第一章\n福贵在村口。",
            output_dir=str(tmp_path),
            chat=FakeChat(),
            image=FakeImage(),
        )
    )
    root = tmp_path / "review"
    assert (root / "source.txt").read_text(encoding="utf-8") == "第一章\n福贵在村口。"
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "finished"
    assert manifest["genre"] == "general"
    assert manifest["harness_digest"].startswith("sha256:")
    assert manifest["project_id"] == tmp_path.name

    prompt = json.loads((root / "prompts" / "page_plan.c0000.json").read_text(encoding="utf-8"))
    assert prompt["stage"] == "page_plan"
    assert prompt["tool_choice"]["function"]["name"] == "plan_comic_pages"
    assert prompt["digest"].startswith("sha256:")
    assert prompt["messages"][0]["role"] == "system"

    plan = json.loads((root / "plans" / "page_plan.c0000.json").read_text(encoding="utf-8"))
    assert plan["pages"]
    pages = json.loads((root / "pages.json").read_text(encoding="utf-8"))
    assert pages["count"] == len(pages["pages"])
    assert pages["pages"]

    report = json.loads((root / "structure_report.json").read_text(encoding="utf-8"))
    assert "checks" in report
    assert "chapter_validated" in report


@patch("core.comic.export.ExportEngine.export_pdf", _fake_export_pdf)
def test_the_packet_can_be_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "finished_page")
    monkeypatch.setenv("INKSTONE_REVIEW_PACKET", "0")
    asyncio.run(
        creative_comic(
            "第一章\n福贵在村口。",
            output_dir=str(tmp_path),
            chat=FakeChat(),
            image=FakeImage(),
        )
    )
    assert not (tmp_path / "review").exists()
