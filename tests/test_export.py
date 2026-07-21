"""tests.test_export — PDF export fallback when manga2pdf is absent."""

import shutil
from pathlib import Path

from core.comic.export import ExportEngine


def test_export_pdf_falls_back_to_pil_without_manga2pdf(tmp_path, monkeypatch):
    # Force the PIL fallback path regardless of whether manga2pdf is installed.
    real_which = shutil.which

    def fake_which(cmd):
        return None if cmd == "manga2pdf" else real_which(cmd)

    monkeypatch.setattr(shutil, "which", fake_which)

    from PIL import Image

    page_dir = tmp_path / "pages"
    page_dir.mkdir()
    for i in (1, 2):
        Image.new("RGB", (1400, 1000), (i * 40, 40, 40)).save(page_dir / f"page_{i:02d}.png")

    out = tmp_path / "comic.pdf"
    result = ExportEngine().export_pdf(page_dir, out=str(out))

    assert Path(result).exists() and Path(result).stat().st_size > 0
    assert out.read_bytes().startswith(b"%PDF")
