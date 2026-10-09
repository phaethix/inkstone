"""Phase 2: objects and manifests under a project, checked by verify."""

import inspect
import subprocess
import sys
from pathlib import Path

from PIL import Image

from core.comic.cas import (
    Manifest,
    ManifestOutput,
    action_key,
    is_hit,
    letter_action_key,
    load_manifest,
    put_bytes,
    read_bytes,
    record_ok,
    rewrite_manifest,
    save_manifest,
    verify,
)
from core.pipelines.creative_comic import (
    _export_pdf_with_manifest,
    _letter_page_from_blank,
    letter_finished_page,
)
from core.schemas import ComicPagePlan


def test_put_bytes_is_addressed_by_content_and_is_not_rewritten(tmp_path):
    first = put_bytes(tmp_path, b"page-bytes")
    second = put_bytes(tmp_path, b"page-bytes")
    assert first == second
    assert read_bytes(tmp_path, first) == b"page-bytes"
    stored = list((tmp_path / "cas").rglob("*.bin"))
    assert len(stored) == 1


def test_letter_key_follows_the_blank_and_the_env():
    blank = b"blank"
    plan = '{"page":1}'
    base = letter_action_key(blank=blank, plan_json=plan, env="env-a", stage_src="src")
    assert letter_action_key(blank=blank, plan_json=plan, env="env-a", stage_src="src") == base
    assert letter_action_key(blank=b"other", plan_json=plan, env="env-a", stage_src="src") != base
    assert letter_action_key(blank=blank, plan_json=plan, env="env-b", stage_src="src") != base
    assert "page_size" not in letter_action_key.__code__.co_varnames
    assert "panel_continuity" not in letter_action_key.__code__.co_varnames
    assert "l3_enabled" not in letter_action_key.__code__.co_varnames
    assert action_key(stage="extract", stage_src="src", inputs=[], params={}, env="env-b") != base


def test_rewrite_keeps_the_key_and_records_what_it_replaced(tmp_path):
    content = put_bytes(tmp_path, b"lettered")
    key = letter_action_key(blank=b"blank", plan_json="{}", env="e", stage_src="s")
    save_manifest(
        tmp_path,
        Manifest(
            key=key,
            stage="letter",
            outcome="awaiting_human",
            reason="sample",
            outputs=[ManifestOutput(name="page.png", content=content)],
        ),
    )
    released = rewrite_manifest(tmp_path, key, outcome="ok", reason=None)
    assert released.key == key
    assert load_manifest(tmp_path, key).key == key
    assert content in released.supersedes
    assert is_hit(released)
    assert verify(tmp_path) == []


def test_verify_reports_a_missing_object_and_does_not_reconcile(tmp_path):
    content = put_bytes(tmp_path, b"lettered")
    key = "sha256:" + "ab" * 32
    save_manifest(
        tmp_path,
        Manifest(
            key=key,
            stage="letter",
            outputs=[ManifestOutput(name="page.png", content=content)],
        ),
    )
    blob = next((tmp_path / "cas").rglob("*.bin"))
    blob.unlink()
    problems = verify(tmp_path)
    assert problems
    assert any("missing" in problem for problem in problems)
    assert blob.parent.exists()
    assert "_reconcile_state" not in inspect.getsource(verify)


def test_verify_requires_a_reason_on_a_rejection(tmp_path):
    key = letter_action_key(blank=b"b", plan_json="{}", env="e", stage_src="s")
    save_manifest(tmp_path, Manifest(key=key, stage="letter", outcome="rejected", reason=None))
    assert verify(tmp_path)
    released = rewrite_manifest(
        tmp_path, key, outcome="rejected", reason="content_policy", outputs=[]
    )
    assert released.key == key
    assert is_hit(released)
    assert verify(tmp_path) == []


def _blank(path, color):
    Image.new("RGB", (20, 30), color).save(path)


def test_a_second_letter_copies_the_stored_bytes(tmp_path, monkeypatch):
    blank = tmp_path / "blank.png"
    local = tmp_path / "page.png"
    _blank(blank, (10, 20, 30))
    plan = ComicPagePlan(page_id="p")
    calls = {"n": 0}
    real = letter_finished_page

    def counting(image, page_plan, **kwargs):
        calls["n"] += 1
        return real(image, page_plan, **kwargs)

    monkeypatch.setattr("core.pipelines.creative_comic.letter_finished_page", counting)
    _letter_page_from_blank(blank, local, plan, source_text="福贵", output_dir=tmp_path)
    first = local.read_bytes()
    local.unlink()
    _letter_page_from_blank(blank, local, plan, source_text="福贵", output_dir=tmp_path)
    assert local.read_bytes() == first
    assert calls["n"] == 1
    assert verify(tmp_path) == []

    _blank(blank, (90, 10, 10))
    _letter_page_from_blank(blank, local, plan, source_text="福贵", output_dir=tmp_path)
    assert calls["n"] == 2


def test_a_second_export_copies_the_stored_pdf(tmp_path, monkeypatch):
    pages = tmp_path / "pages"
    pages.mkdir()
    Image.new("RGB", (8, 8), (1, 2, 3)).save(pages / "page_c0000_p0000.png")
    calls = {"n": 0}

    def fake(self, page_dir, out="comic.pdf", layout="TwoPageRight", direction="R2L"):
        calls["n"] += 1
        Path(out).write_bytes(b"%PDF-1.4 fake")
        return out

    monkeypatch.setattr("core.pipelines.creative_comic.ExportEngine.export_pdf", fake)
    first = _export_pdf_with_manifest(pages, tmp_path)
    assert calls["n"] == 1
    Path(first).unlink()
    second = _export_pdf_with_manifest(pages, tmp_path)
    assert Path(second).read_bytes() == b"%PDF-1.4 fake"
    assert calls["n"] == 1

    Image.new("RGB", (8, 8), (9, 9, 9)).save(pages / "page_c0000_p0000.png")
    _export_pdf_with_manifest(pages, tmp_path)
    assert calls["n"] == 2
    assert verify(tmp_path) == []


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "core.cli", *args],
        capture_output=True,
        text=True,
    )


def _programmatic_fixture(root: Path) -> Path:
    """A few KB of synthetic bytes and one manifest. No example book is stored."""
    data = b"synthetic-page" * 256
    key = action_key(stage="letter", stage_src="src", inputs=[], params={}, env="env")
    record_ok(
        root,
        key=key,
        stage="letter",
        stage_src="src",
        env="env",
        inputs=[],
        name="page.bin",
        data=data,
    )
    stored = list((root / "cas").rglob("*.bin"))
    assert len(stored) == 1
    return stored[0]


def test_cli_verify_accepts_a_programmatic_fixture(tmp_path):
    _programmatic_fixture(tmp_path)
    completed = _run_cli("verify", "--out", str(tmp_path))
    assert completed.returncode == 0
    assert completed.stdout.strip() == "ok"


def test_cli_verify_fails_when_one_object_is_deleted(tmp_path):
    blob = _programmatic_fixture(tmp_path)
    blob.unlink()
    completed = _run_cli("verify", "--out", str(tmp_path))
    assert completed.returncode == 1
    assert "missing" in completed.stderr
