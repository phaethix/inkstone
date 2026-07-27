"""core.comic.export — turn composed pages into a final comic PDF.

``ExportEngine.export_pdf`` wraps a directory of ``page_NN.png`` images into a
PDF. It prefers the external ``manga2pdf`` CLI (nicer two-page / R2L manga
layout) when that CLI is on ``PATH``, and otherwise falls back to a
**pure-PIL** multi-page PDF so the ``page`` export works with zero extra
dependencies. The vertical webtoon strip is produced by ``LayoutEngine``
(pure PIL), not here.
"""

import logging
import shutil
import subprocess
from pathlib import Path

try:
    from PIL import Image
except ImportError:  # pragma: no cover — Pillow is a hard dependency
    Image = None

logger = logging.getLogger(__name__)


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
            direction: reading direction (e.g. ``R2L``); ignored by the PIL fallback.

        Returns:
            The output PDF path.

        Raises:
            RuntimeError: if no page images are found, or if neither manga2pdf
                nor Pillow can produce the PDF.
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

        # Fallback: pure-PIL multi-page PDF — no external CLI required.
        # Degradation must be loud: the user gets a plain PDF, not the
        # manga layout the README gallery shows.
        logger.warning(
            "manga2pdf CLI not found; exporting a plain multi-page PDF instead "
            "(no %s layout / %s reading direction). "
            "Install with `pip install manga2pdf` for the full manga layout.",
            layout,
            direction,
        )
        if Image is None:
            raise RuntimeError(
                "manga2pdf CLI not found and Pillow is not installed; cannot export PDF"
            )
        imgs = [Image.open(p).convert("RGB") for p in pages]
        imgs[0].save(out, "PDF", save_all=True, append_images=imgs[1:])
        return out
