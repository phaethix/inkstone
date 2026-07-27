"""tests.test_export — PDF export fallback when manga2pdf is absent."""

import shutil
from pathlib import Path

from core.comic.export import ExportEngine


def _force_pil_path(monkeypatch):
    real_which = shutil.which

    def fake_which(cmd):
        if cmd in {"manga2pdf", "pdfunite", "gs"}:
            return None
        return real_which(cmd)

    monkeypatch.setattr(shutil, "which", fake_which)
    monkeypatch.setattr(ExportEngine, "_try_img2pdf", staticmethod(lambda pages, out: False))


def test_export_pdf_falls_back_to_pil_without_manga2pdf(tmp_path, monkeypatch):
    _force_pil_path(monkeypatch)

    from PIL import Image

    page_dir = tmp_path / "pages"
    page_dir.mkdir()
    for i in (1, 2):
        Image.new("RGB", (1400, 1000), (i * 40, 40, 40)).save(page_dir / f"page_{i:02d}.png")

    out = tmp_path / "comic.pdf"
    result = ExportEngine().export_pdf(page_dir, out=str(out))

    assert Path(result).exists() and Path(result).stat().st_size > 0
    assert out.read_bytes().startswith(b"%PDF")


def test_export_pdf_ignores_webtoon_when_page_sheets_exist(tmp_path, monkeypatch):
    _force_pil_path(monkeypatch)

    from PIL import Image

    page_dir = tmp_path / "pages"
    page_dir.mkdir()
    Image.new("RGB", (100, 100), (10, 10, 10)).save(page_dir / "webtoon.png")
    Image.new("RGB", (1400, 1000), (200, 40, 40)).save(page_dir / "page_01.png")
    Image.new("RGB", (1400, 1000), (40, 200, 40)).save(page_dir / "page_02.png")

    out = tmp_path / "comic.pdf"
    ExportEngine().export_pdf(page_dir, out=str(out))
    assert out.read_bytes().startswith(b"%PDF")


def test_export_pdf_batched_pil_for_many_pages(tmp_path, monkeypatch):
    """Many pages must keep peak concurrent Image.open count near the batch size."""
    _force_pil_path(monkeypatch)
    monkeypatch.setenv("INKSTONE_PDF_BATCH", "3")
    # No pypdf merge → falls back to single encode after batches fail to merge,
    # OR if pypdf exists batches merge. Either way export must succeed.
    from PIL import Image

    page_dir = tmp_path / "pages"
    page_dir.mkdir()
    for i in range(1, 8):
        Image.new("RGB", (200, 150), (i * 20, 40, 40)).save(page_dir / f"page_{i:02d}.png")

    open_counts: list[int] = []
    concurrent = {"n": 0}
    real_open = Image.open

    def counting_open(*args, **kwargs):
        concurrent["n"] += 1
        open_counts.append(concurrent["n"])
        im = real_open(*args, **kwargs)

        class _Tracked:
            def __init__(self, inner):
                self._inner = inner

            def convert(self, *a, **k):
                return self._inner.convert(*a, **k)

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                self.close()
                return False

            def close(self):
                concurrent["n"] = max(0, concurrent["n"] - 1)
                return self._inner.close()

            def __getattr__(self, name):
                return getattr(self._inner, name)

        return _Tracked(im)

    monkeypatch.setattr(Image, "open", counting_open)
    out = tmp_path / "comic.pdf"
    ExportEngine().export_pdf(page_dir, out=str(out))
    assert out.read_bytes().startswith(b"%PDF")
    assert open_counts
    assert max(open_counts) <= 3
