"""core.comic.layout — page composition and dialogue bubbles.

``LayoutEngine`` arranges generated panel images into comic pages (grid packing
with pagination past four panels per page) and, for the webtoon mode, stacks
them into a single vertical strip. Panels that carry dialogue get a rounded
speech bubble rendered on top.

Pure PIL — no network, no external services.
"""

from dataclasses import dataclass
from math import ceil
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


@dataclass
class PanelImage:
    """One panel to place on a page."""

    image: Image.Image
    dialogue: str | None = None


class LayoutEngine:
    """Compose panel images into pages or a vertical webtoon strip."""

    def __init__(self, page_width: int = 1400, cell_height: int = 1000, bg=(255, 255, 255)):
        self.page_width = page_width
        self.cell_height = cell_height
        self.bg = bg

    def compose(
        self, panels: list[PanelImage], output_dir, *, layout_mode: str = "page"
    ) -> list[str]:
        """Render ``panels`` to disk and return the written file paths.

        Args:
            panels: panels in reading order.
            output_dir: directory for the produced images.
            layout_mode: ``"page"`` for grid pages (``page_01.png`` ...) or
                ``"webtoon"`` for a single vertical ``webtoon.png``.
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        if layout_mode == "webtoon":
            return self._compose_webtoon(panels, output_dir)
        return self._compose_pages(panels, output_dir)

    # ------------------------------------------------------------------ #
    # Page mode
    # ------------------------------------------------------------------ #
    def _compose_pages(self, panels: list[PanelImage], output_dir: Path) -> list[str]:
        pages = self._paginate(panels, per_page=4)
        paths: list[str] = []
        for i, page_panels in enumerate(pages, start=1):
            canvas = self._render_page(page_panels)
            path = output_dir / f"page_{i:02d}.png"
            canvas.save(path)
            paths.append(str(path))
        return paths

    @staticmethod
    def _paginate(panels: list[PanelImage], per_page: int = 4) -> list[list[PanelImage]]:
        if not panels:
            return [[]]
        return [panels[i : i + per_page] for i in range(0, len(panels), per_page)]

    @staticmethod
    def _cols_for(n: int) -> int:
        return {1: 1, 2: 2, 3: 3, 4: 2}.get(n, 2)

    def _render_page(self, page_panels: list[PanelImage]) -> Image.Image:
        n = len(page_panels)
        cols = self._cols_for(n)
        rows = ceil(n / cols) if n else 1
        cell_w = self.page_width // cols
        canvas = Image.new("RGB", (self.page_width, self.cell_height * rows), self.bg)
        for idx, panel in enumerate(page_panels):
            r, c = divmod(idx, cols)
            box = (c * cell_w, r * self.cell_height, (c + 1) * cell_w, (r + 1) * self.cell_height)
            self._place_panel(canvas, panel, box)
        return canvas

    def _place_panel(
        self, canvas: Image.Image, panel: PanelImage, box: tuple[int, int, int, int]
    ) -> None:
        x0, y0, x1, y1 = box
        cw, ch = x1 - x0, y1 - y0
        img = panel.image.convert("RGB").resize((cw, ch))
        canvas.paste(img, (x0, y0))
        if panel.dialogue:
            bw, bh = int(cw * 0.8), int(ch * 0.3)
            bx = x0 + (cw - bw) // 2
            by = y1 - bh - 20
            draw = ImageDraw.Draw(canvas)
            self._draw_bubble(draw, (bx, by, bw, bh), panel.dialogue)

    # ------------------------------------------------------------------ #
    # Webtoon mode
    # ------------------------------------------------------------------ #
    def _compose_webtoon(self, panels: list[PanelImage], output_dir: Path) -> list[str]:
        if not panels:
            return []
        width = self.page_width
        scaled: list[Image.Image] = []
        total_h = 0
        for panel in panels:
            img = panel.image.convert("RGB")
            h = int(width * img.height / img.width) if img.width else width
            s = img.resize((width, h))
            scaled.append(s)
            total_h += h
        canvas = Image.new("RGB", (width, total_h), self.bg)
        y = 0
        for s in scaled:
            canvas.paste(s, (0, y))
            y += s.height
        path = output_dir / "webtoon.png"
        canvas.save(path)
        return [str(path)]

    # ------------------------------------------------------------------ #
    # Dialogue bubble
    # ------------------------------------------------------------------ #
    def _draw_bubble(
        self, draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], text: str
    ) -> None:
        x, y, w, h = box
        pad = 10
        draw.rounded_rectangle(
            [x, y, x + w, y + h], radius=12, fill="white", outline="black", width=2
        )
        font = ImageFont.load_default()
        max_w = w - 2 * pad
        ty = y + pad
        line_h = int(font.getlength("Ag") * 1.2) or 12
        for line in self._wrap_text(text, font, max_w):
            draw.text((x + pad, ty), line, fill="black", font=font)
            ty += line_h

    @staticmethod
    def _wrap_text(text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
        lines: list[str] = []
        cur = ""
        for ch in text:
            if font.getlength(cur + ch) <= max_width:
                cur += ch
            else:
                lines.append(cur)
                cur = ch
        if cur:
            lines.append(cur)
        return lines or [""]
