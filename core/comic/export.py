"""core.comic.export — turn composed pages into final comic artifacts.

``ExportEngine`` offers two products:

- ``export_pdf`` wraps a directory of ``page_NN.png`` images into a PDF via the
  external ``manga2pdf`` CLI. The CLI must be installed separately (it is not a
  Python import dependency); only its command line is invoked.
- ``export_webtoon`` stacks already-rendered panel images into one tall PNG
  using PIL, with no external dependency.
"""

import subprocess
from pathlib import Path

from PIL import Image


class ExportEngine:
    """Produce PDF / webtoon outputs from composed panel images."""

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
            direction: reading direction (e.g. ``R2L``).

        Returns:
            The output PDF path.

        Raises:
            RuntimeError: if the ``manga2pdf`` CLI exits non-zero.
        """
        page_dir = str(page_dir)
        cmd = ["manga2pdf", page_dir, "-o", out, "-p", layout, "-d", direction]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"manga2pdf failed (exit {result.returncode}): {result.stderr}")
        return out

    def export_webtoon(self, panel_paths, out: str = "webtoon.png") -> str:
        """Vertically concatenate ``panel_paths`` into a single PNG.

        Args:
            panel_paths: ordered list of image paths to stack.
            out: output PNG path.

        Returns:
            The output PNG path.
        """
        paths = [Path(p) for p in panel_paths]
        if not paths:
            raise ValueError("export_webtoon requires at least one panel")
        imgs = [Image.open(p).convert("RGB") for p in paths]
        width = max(i.width for i in imgs)
        total_h = sum(i.height for i in imgs)
        canvas = Image.new("RGB", (width, total_h), (255, 255, 255))
        y = 0
        for img in imgs:
            canvas.paste(img, (0, y))
            y += img.height
        out_path = Path(out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(out_path)
        return str(out_path)
