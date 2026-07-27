"""core.comic.layout — page composition and lettering (caption / dialogue / sfx).

``LayoutEngine`` arranges generated panel images into comic pages (grid packing
with pagination past four panels per page) and, for the webtoon mode, stacks
them into a single vertical strip. Reader-visible text is drawn as:
caption (top narration bar), dialogue (rounded speech bubble), and sfx
(outlined onomatopoeia).

Pure PIL — no network, no external services.
"""

from dataclasses import dataclass
from math import ceil
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from core.comic.fonts import resolve_font, text_requires_cjk
from core.config import DEFAULT_WEBTOON_MAX_PIXELS, webtoon_max_pixels

# Re-export for callers/tests that historically imported from this module.
# Override with INKSTONE_WEBTOON_MAX_PIXELS; set it to 0 to disable the guard.
__all__ = ["DEFAULT_WEBTOON_MAX_PIXELS", "LayoutEngine", "PanelImage"]


def _webtoon_max_pixels() -> int:
    return webtoon_max_pixels()


@dataclass
class PanelImage:
    """One panel to place on a page."""

    image: Image.Image
    dialogue: str | None = None
    caption: str | None = None
    sfx: str | None = None


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
        # Optional CJK font override for lettering; falls back to
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
            return []
        return [panels[i : i + per_page] for i in range(0, len(panels), per_page)]

    @staticmethod
    def _cols_for(n: int) -> int:
        return {1: 1, 2: 2, 3: 3, 4: 2}.get(n, 2)

    def _lettering_reserve(self, panel: PanelImage, cell_w: int) -> int:
        """Extra vertical space needed inside a cell for caption/dialogue/sfx."""
        reserve = 0
        text_w = int(cell_w * 0.8)
        if panel.caption:
            reserve += self._bubble_height(panel.caption, text_w) + 24
        if panel.dialogue:
            reserve += self._bubble_height(panel.dialogue, text_w) + 40
        if panel.sfx:
            reserve += self._bubble_height(panel.sfx, int(cell_w * 0.45)) + 16
        return reserve

    def _render_page(self, page_panels: list[PanelImage]) -> Image.Image:
        n = len(page_panels)
        cols = self._cols_for(n)
        rows = ceil(n / cols) if n else 1
        cell_w = self.page_width // cols
        required = max(
            (self._lettering_reserve(panel, cell_w) for panel in page_panels),
            default=0,
        )
        cell_h = max(self.cell_height, required)
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
        # Contain (letterbox) — never stretch into the cell aspect.
        fitted = ImageOps.contain(panel.image.convert("RGB"), (cw, ch))
        px = x0 + (cw - fitted.width) // 2
        py = y0 + (ch - fitted.height) // 2
        canvas.paste(fitted, (px, py))
        draw = ImageDraw.Draw(canvas)
        if panel.caption:
            bw = int(cw * 0.9)
            bh = self._bubble_height(panel.caption, bw)
            bx = x0 + (cw - bw) // 2
            by = y0 + 12
            self._draw_caption(draw, (bx, by, bw, bh), panel.caption)
        if panel.dialogue:
            bw = int(cw * 0.8)
            bh = self._bubble_height(panel.dialogue, bw)
            bx = x0 + (cw - bw) // 2
            by = y1 - bh - 20
            self._draw_bubble(draw, (bx, by, bw, bh), panel.dialogue)
        if panel.sfx:
            bw = int(cw * 0.45)
            bh = self._bubble_height(panel.sfx, bw)
            bx = x1 - bw - 12
            by = y0 + (40 if panel.caption else 12)
            self._draw_sfx(draw, (bx, by, bw, bh), panel.sfx)

    # ------------------------------------------------------------------ #
    # Webtoon mode
    # ------------------------------------------------------------------ #
    def _panel_strip_height(self, panel: PanelImage, width: int) -> int:
        img_w, img_h = panel.image.size
        h = int(width * img_h / img_w) if img_w else width
        if panel.caption:
            h += self._bubble_height(panel.caption, int(width * 0.9)) + 24
        if panel.dialogue:
            h += self._bubble_height(panel.dialogue, int(width * 0.8)) + 40
        if panel.sfx:
            h += self._bubble_height(panel.sfx, int(width * 0.45)) + 16
        return h

    def _compose_webtoon(self, panels: list[PanelImage], output_dir: Path) -> list[str]:
        if not panels:
            return []
        width = self.page_width
        limit = _webtoon_max_pixels()
        total_h = sum(self._panel_strip_height(panel, width) for panel in panels)
        if limit and width * total_h > limit:
            raise ValueError(
                f"webtoon canvas would be {width}x{total_h}px "
                f"({width * total_h / 1e6:.0f}MP, over the {limit / 1e6:.0f}MP limit); "
                "re-run with --format page, or raise INKSTONE_WEBTOON_MAX_PIXELS "
                "(0 disables the guard)"
            )
        scaled: list[Image.Image] = []
        for panel in panels:
            img = panel.image.convert("RGB")
            h = int(width * img.height / img.width) if img.width else width
            scaled_panel = img.resize((width, h))
            extra_top = 0
            extra_bot = 0
            if panel.caption:
                extra_top += self._bubble_height(panel.caption, int(width * 0.9)) + 24
            if panel.dialogue:
                extra_bot += self._bubble_height(panel.dialogue, int(width * 0.8)) + 40
            if panel.sfx:
                extra_bot += self._bubble_height(panel.sfx, int(width * 0.45)) + 16
            if extra_top or extra_bot:
                framed = Image.new("RGB", (width, h + extra_top + extra_bot), self.bg)
                framed.paste(scaled_panel, (0, extra_top))
                draw = ImageDraw.Draw(framed)
                y = 12
                if panel.caption:
                    bw = int(width * 0.9)
                    bh = self._bubble_height(panel.caption, bw)
                    self._draw_caption(draw, ((width - bw) // 2, y, bw, bh), panel.caption)
                    y += bh + 12
                y_bot = extra_top + h + 12
                if panel.sfx:
                    bw = int(width * 0.45)
                    bh = self._bubble_height(panel.sfx, bw)
                    self._draw_sfx(draw, (width - bw - 12, y_bot, bw, bh), panel.sfx)
                    y_bot += bh + 8
                if panel.dialogue:
                    bw = int(width * 0.8)
                    bh = self._bubble_height(panel.dialogue, bw)
                    self._draw_bubble(draw, ((width - bw) // 2, y_bot, bw, bh), panel.dialogue)
                scaled_panel = framed
            scaled.append(scaled_panel)
        canvas = Image.new("RGB", (width, total_h), self.bg)
        y = 0
        for s in scaled:
            canvas.paste(s, (0, y))
            y += s.height
        path = output_dir / "webtoon.png"
        canvas.save(path)
        return [str(path)]

    # ------------------------------------------------------------------ #
    # Lettering drawers
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

    def _draw_caption(
        self, draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], text: str
    ) -> None:
        """Rectangular narration bar (not a speech bubble)."""
        x, y, w, h = box
        pad = 10
        draw.rectangle([x, y, x + w, y + h], fill=(245, 245, 240), outline="black", width=2)
        font, _source = resolve_font(text, font_path=self.font_path)
        max_w = w - 2 * pad
        ty = y + pad
        line_h = _line_height(font)
        for line in self._wrap_text(text, font, max_w):
            draw.text((x + pad, ty), line, fill="black", font=font)
            ty += line_h

    def _draw_sfx(
        self, draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], text: str
    ) -> None:
        """Outlined onomatopoeia — no bubble chrome."""
        x, y, w, h = box
        font, _source = resolve_font(text, font_path=self.font_path)
        max_w = max(20, w - 8)
        lines = self._wrap_text(text, font, max_w)
        line_h = _line_height(font)
        ty = y
        for line in lines:
            # Stroke then fill for a crude comic SFX look.
            for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2), (-1, -1), (1, 1)):
                draw.text((x + dx, ty + dy), line, fill="white", font=font)
            draw.text((x, ty), line, fill="black", font=font)
            ty += line_h

    @staticmethod
    def _wrap_text(text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
        lines: list[str] = []
        for paragraph in text.split("\n"):
            if not paragraph:
                lines.append("")
                continue
            if text_requires_cjk(paragraph):
                lines.extend(LayoutEngine._wrap_chars(paragraph, font, max_width))
            else:
                lines.extend(LayoutEngine._wrap_words(paragraph, font, max_width))
        return lines or [""]

    @staticmethod
    def _wrap_chars(text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
        lines: list[str] = []
        cur = ""
        for char in text:
            if font.getlength(cur + char) <= max_width:
                cur += char
            else:
                if cur:
                    lines.append(cur)
                cur = char
        if cur:
            lines.append(cur)
        return lines

    @staticmethod
    def _wrap_words(text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
        lines: list[str] = []
        cur = ""
        for word in text.split(" "):
            candidate = word if not cur else f"{cur} {word}"
            if font.getlength(candidate) <= max_width:
                cur = candidate
                continue
            if cur:
                lines.append(cur)
            if font.getlength(word) <= max_width:
                cur = word
            else:
                lines.extend(LayoutEngine._wrap_chars(word, font, max_width))
                cur = ""
        if cur:
            lines.append(cur)
        return lines
