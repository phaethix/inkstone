"""core.comic.export — turn composed pages into a final comic PDF.

``ExportEngine.export_pdf`` wraps a directory of ``page_NN.png`` images into a
PDF via the external ``manga2pdf`` CLI. The CLI must be installed separately
(it is not a Python import dependency); only its command line is invoked. The
vertical webtoon strip is produced by ``LayoutEngine`` (pure PIL), not here.
"""

import subprocess


class ExportEngine:
    """Produce a PDF from composed panel images via the manga2pdf CLI."""

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
