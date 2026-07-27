"""core.comic.layout — page composition and dialogue bubbles.

``LayoutEngine`` arranges generated panel images into comic pages (grid packing
with pagination past four panels per page) and, for the webtoon mode, stacks
them into a single vertical strip. Panels that carry dialogue get a rounded
speech bubble rendered on top.

Pure PIL — no network, no external services.
"""

from core.config import webtoon_max_pixels
from dataclasses import dataclass
from math import ceil
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from core.comic.fonts import resolve_font

# Upper bound for a single webtoon canvas, in pixels. A webtoon strip is one
# giant RGB buffer (3 bytes/px), so an unbounded strip OOMs on long books:
# 180 panels at 1400x1500 each would need >1GB in a single allocation.
# Override with INKSTONE_WEBTOON_MAX_PIXELS; set it to 0 to disable the guard.
DEFAULT_WEBTOON_MAX_PIXELS = 200_000_000


def _webtoon_max_pixels() -> int:
    return webtoon_max_pixels()


@dataclass
class PanelImage:
    """One panel to place on a page."""

    image: Image.Image
    dialogue: str | None = None


def _line_height(font) -> int:
    """Line height for bubble text, robust across bitmap and truetype fonts."""
    getmetrics = getattr(font, "getmetrics", None)
    if getmetrics is not None:
        try:
            ascent, descent = getmetrics()
            if ascent + descent > 0:
                return int((ascent + descent) * 1.15)
        except Exception:  # noqa: BLE001 — fall through to width heuristic
            pass
    try:
        return int(font.getlength("Ag") * 1.2) or 12
    except Exception:  # noqa: BLE001
        return 12


class LayoutEngine:
    """Compose panel images into pages or a vertical webtoon strip."""

    def __init__(
        self,
        page_width: int = 1400,
        cell_height: int = 1000,
        bg=(255, 255, 255),
        font_path: str | None = None,
    ):
        self.page_width = page_width
        self.cell_height = cell_height
        self.bg = bg
        # Optional CJK font override for dialogue bubbles; falls back to
        # INKSTONE_FONT_PATH then platform-known CJK fonts when None.
        self.font_path = font_path

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
        caption_heights = (
            self._bubble_height(panel.dialogue, int(cell_w * 0.8)) + 40
            for panel in page_panels
            if panel.dialogue
        )
        required_caption = max(caption_heights, default=0)
        cell_h = max(self.cell_height, required_caption)
        canvas = Image.new("RGB", (self.page_width, cell_h * rows), self.bg)
        for idx, panel in enumerate(page_panels):
            r, c = divmod(idx, cols)
            box = (c * cell_w, r * cell_h, (c + 1) * cell_w, (r + 1) * cell_h)
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
            bw = int(cw * 0.8)
            bh = self._bubble_height(panel.dialogue, bw)
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
            scaled_panel = img.resize((width, h))
            if panel.dialogue:
                bubble_w = int(width * 0.8)
                bubble_h = self._bubble_height(panel.dialogue, bubble_w)
                bubble_x = (width - bubble_w) // 2
                caption_h = bubble_h + 40
                with_caption = Image.new("RGB", (width, h + caption_h), self.bg)
                with_caption.paste(scaled_panel, (0, 0))
                self._draw_bubble(
                    ImageDraw.Draw(with_caption),
                    (bubble_x, h + 20, bubble_w, bubble_h),
                    panel.dialogue,
                )
                scaled_panel = with_caption
            scaled.append(scaled_panel)
            total_h += scaled_panel.height
        limit = _webtoon_max_pixels()
        if limit and width * total_h > limit:
            raise ValueError(
                f"webtoon canvas would be {width}x{total_h}px "
                f"({width * total_h / 1e6:.0f}MP, over the {limit / 1e6:.0f}MP limit); "
                "re-run with --format page, or raise INKSTONE_WEBTOON_MAX_PIXELS "
                "(0 disables the guard)"
            )
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
    def _bubble_height(self, text: str, width: int) -> int:
        font, _source = resolve_font(text, font_path=self.font_path)
        lines = self._wrap_text(text, font, width - 20)
        return max(50, len(lines) * _line_height(font) + 20)

    def _draw_bubble(
        self, draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], text: str
    ) -> None:
        x, y, w, h = box
        pad = 10
        draw.rounded_rectangle(
            [x, y, x + w, y + h], radius=12, fill="white", outline="black", width=2
        )
        font, _source = resolve_font(text, font_path=self.font_path)
        max_w = w - 2 * pad
        ty = y + pad
        line_h = _line_height(font)
        for line in self._wrap_text(text, font, max_w):
            draw.text((x + pad, ty), line, fill="black", font=font)
            ty += line_h

    @staticmethod
    def _wrap_text(text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
        lines: list[str] = []
        for paragraph in text.split("\n"):
            if not paragraph:
                lines.append("")
                continue
            cur = ""
            for char in paragraph:
                if font.getlength(cur + char) <= max_width:
                    cur += char
                else:
                    lines.append(cur)
                    cur = char
            if cur:
                lines.append(cur)
        return lines or [""]
