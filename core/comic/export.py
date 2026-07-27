"""core.comic.export — turn composed pages into a final comic PDF.

``ExportEngine.export_pdf`` wraps a directory of ``page_NN.png`` images into a
PDF. Preference order:

1. ``manga2pdf`` CLI when on ``PATH`` (two-page / R2L manga layout)
2. ``img2pdf`` when importable (embeds page files without decoding all RGB)
3. Batched Pillow export (≤8 resident RGB pages) + merge via ``pypdf`` /
   ``pdfunite`` / ``gs`` when needed

The vertical webtoon strip is produced by ``LayoutEngine`` (pure PIL), not here.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

try:
    from PIL import Image
except ImportError:  # pragma: no cover — Pillow is a hard dependency
    Image = None

logger = logging.getLogger(__name__)

# Peak resident RGB pages for the Pillow path (override with INKSTONE_PDF_BATCH).
_DEFAULT_PDF_BATCH = 8


def _pdf_batch_size() -> int:
    raw = os.environ.get("INKSTONE_PDF_BATCH", "").strip()
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    return _DEFAULT_PDF_BATCH


class ExportEngine:
    """Produce a PDF from composed panel images."""

    def export_pdf(
        self,
        page_dir,
        out: str = "comic.pdf",
        layout: str = "TwoPageRight",
        direction: str = "R2L",
    ) -> str:
        """Build a PDF from the ``page_NN.png`` files in ``page_dir``.

        Args:
            page_dir: directory whose ``page_01.png`` ... files define page order.
            out: output PDF path.
            layout: manga2pdf page layout (e.g. ``TwoPageRight`` for flip pages).
            direction: reading direction (e.g. ``R2L``); ignored by non-manga2pdf paths.

        Returns:
            The output PDF path.

        Raises:
            RuntimeError: if no page images are found, or if no exporter can run.
        """
        page_dir = Path(page_dir)
        pages = sorted(page_dir.glob("page_*.png"))
        if not pages:
            # Ignore vertical webtoon strips sitting alongside page sheets.
            pages = sorted(p for p in page_dir.glob("*.png") if p.name.lower() != "webtoon.png")
        if not pages:
            raise RuntimeError(f"no page images found in {page_dir}")

        if shutil.which("manga2pdf"):
            cmd = ["manga2pdf", str(page_dir), "-o", out, "-p", layout, "-d", direction]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                raise RuntimeError(f"manga2pdf failed (exit {result.returncode}): {result.stderr}")
            return out

        logger.warning(
            "manga2pdf CLI not found; exporting a plain multi-page PDF instead "
            "(no %s layout / %s reading direction). "
            "Install with `pip install manga2pdf` for the full manga layout.",
            layout,
            direction,
        )

        if self._try_img2pdf(pages, out):
            return out

        if Image is None:
            raise RuntimeError(
                "manga2pdf/img2pdf unavailable and Pillow is not installed; cannot export PDF"
            )
        self._export_pdf_pil_batched(pages, out)
        return out

    @staticmethod
    def _try_img2pdf(pages: list[Path], out: str) -> bool:
        try:
            import img2pdf  # type: ignore[import-untyped]
        except ImportError:
            return False
        with open(out, "wb") as fh:
            fh.write(img2pdf.convert([str(p) for p in pages]))
        return True

    def _export_pdf_pil_batched(self, pages: list[Path], out: str) -> None:
        """Convert pages in small batches so peak RGB memory stays bounded."""
        batch = _pdf_batch_size()
        if len(pages) <= batch:
            self._save_pil_batch(pages, out)
            return

        with tempfile.TemporaryDirectory(prefix="inkstone-pdf-") as tmp:
            tmp_path = Path(tmp)
            parts: list[Path] = []
            for i in range(0, len(pages), batch):
                part = tmp_path / f"part_{i:04d}.pdf"
                self._save_pil_batch(pages[i : i + batch], str(part))
                parts.append(part)
            if not self._merge_pdf_parts(parts, out):
                raise RuntimeError(
                    f"cannot merge {len(parts)} PDF batches for {len(pages)} pages; "
                    "install pypdf (`pip install pypdf`) or img2pdf, "
                    "or lower INKSTONE_PDF_BATCH / install pdfunite"
                )

    @staticmethod
    def _save_pil_batch(pages: list[Path], out: str) -> None:
        images: list[Image.Image] = []
        try:
            for path in pages:
                with Image.open(path) as src:
                    images.append(src.convert("RGB"))
            images[0].save(out, "PDF", save_all=True, append_images=images[1:])
        finally:
            for im in images:
                im.close()

    @staticmethod
    def _merge_pdf_parts(parts: list[Path], out: str) -> bool:
        try:
            from pypdf import PdfReader, PdfWriter  # type: ignore[import-untyped]
        except ImportError:
            PdfReader = None  # type: ignore[assignment]
        else:
            writer = PdfWriter()
            for part in parts:
                reader = PdfReader(str(part))
                for page in reader.pages:
                    writer.add_page(page)
            with open(out, "wb") as fh:
                writer.write(fh)
            return True

        if shutil.which("pdfunite"):
            result = subprocess.run(
                ["pdfunite", *[str(p) for p in parts], out],
                capture_output=True,
                text=True,
            )
            return result.returncode == 0

        if shutil.which("gs"):
            cmd = [
                "gs",
                "-dBATCH",
                "-dNOPAUSE",
                "-q",
                "-sDEVICE=pdfwrite",
                f"-sOutputFile={out}",
                *[str(p) for p in parts],
            ]
            result = subprocess.run(cmd, capture_output=True, text=True)
            return result.returncode == 0

        return False
