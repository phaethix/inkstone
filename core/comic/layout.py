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

from core.comic.fonts import DEFAULT_CJK_FONT_SIZE, resolve_font, text_requires_cjk
from core.config import DEFAULT_WEBTOON_MAX_PIXELS, webtoon_max_pixels

# Re-export for callers/tests that historically imported from this module.
# Override with INKSTONE_WEBTOON_MAX_PIXELS; set it to 0 to disable the guard.
__all__ = ["DEFAULT_WEBTOON_MAX_PIXELS", "LayoutEngine", "PanelImage"]

# Smallest CJK size a caption bar will step down to. Below this the bar clips.
_MIN_CAPTION_SIZE = 9


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
        sfx_top = y0 + 12
        if panel.caption:
            bw = int(cw * 0.9)
            bh = self._bubble_height(panel.caption, bw)
            bx = x0 + (cw - bw) // 2
            by = y0 + 12
            self._draw_caption(draw, (bx, by, bw, bh), panel.caption)
            sfx_top = by + bh + 8
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
            self._draw_sfx(draw, (bx, sfx_top, bw, bh), panel.sfx)

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
        # Pillow treats the right and bottom as inclusive, so the outline
        # ends on the last pixel inside the box.
        draw.rounded_rectangle(
            [x, y, x + w - 1, y + h - 1], radius=12, fill="white", outline="black", width=2
        )
        max_w = w - 2 * pad
        font, lines = self._fitted_caption(text, max(1, max_w), h)
        ty = y + pad
        line_h = _line_height(font)
        limit = y + h - pad
        for line in lines:
            if ty + line_h > limit:
                break
            if max_w < 1 or font.getlength(line) > max_w:
                continue
            draw.text((x + pad, ty), line, fill="black", font=font)
            ty += line_h

    def _caption_block_height(self, font: ImageFont.ImageFont, lines: list[str]) -> int:
        return len(lines) * _line_height(font) + 20

    def _fitted_caption(
        self, text: str, max_width: int, max_height: int
    ) -> tuple[ImageFont.ImageFont, list[str]]:
        """Lines that stay inside a caption bar.

        A bar already tall enough and wide enough keeps the resolved size. A CJK
        caption that wraps past the bar, or a line wider than the bar, steps the
        font down. A bitmap font cannot change size.
        """
        font, source = resolve_font(text, font_path=self.font_path)
        lines = self._wrap_text(text, font, max_width)
        if source in {"default", "default-no-cjk"}:
            return font, lines
        size = DEFAULT_CJK_FONT_SIZE
        too_wide = any(font.getlength(line) > max_width for line in lines)
        while size > _MIN_CAPTION_SIZE and (
            self._caption_block_height(font, lines) > max_height or too_wide
        ):
            size -= 1
            font, _source = resolve_font(text, size=size, font_path=self.font_path)
            lines = self._wrap_text(text, font, max_width)
            too_wide = any(font.getlength(line) > max_width for line in lines)
        return font, lines

    def _draw_caption(
        self, draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], text: str
    ) -> None:
        """Rectangular narration bar (not a speech bubble)."""
        x, y, w, h = box
        pad = 10
        draw.rectangle([x, y, x + w - 1, y + h - 1], fill=(245, 245, 240), outline="black", width=2)
        max_w = w - 2 * pad
        font, lines = self._fitted_caption(text, max(1, max_w), h)
        ty = y + pad
        line_h = _line_height(font)
        limit = y + h - pad
        for line in lines:
            if ty + line_h > limit:
                break
            if max_w < 1 or font.getlength(line) > max_w:
                continue
            draw.text((x + pad, ty), line, fill="black", font=font)
            ty += line_h

    def _draw_sfx(
        self, draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], text: str
    ) -> None:
        """Outlined onomatopoeia — no bubble chrome."""
        x, y, w, h = box
        # Stroke is ±2 and the glyph antialias is about one more pixel.
        inset = 4
        max_w = w - 2 * inset
        if max_w < 1:
            return
        font, lines = self._fitted_caption(text, max_w, h)
        line_h = _line_height(font)
        tx = x + inset
        ty = y + inset
        limit = y + h - inset
        for line in lines:
            if ty + line_h > limit:
                break
            if font.getlength(line) > max_w:
                continue
            # Stroke then fill for a crude comic SFX look.
            for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2), (-1, -1), (1, 1)):
                draw.text((tx + dx, ty + dy), line, fill="white", font=font)
            draw.text((tx, ty), line, fill="black", font=font)
            ty += line_h

    @staticmethod
    def _wrap_text(text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
        lines: list[str] = []
        for paragraph in text.split("\n"):
            if not paragraph:
                lines.append("")
                continue
            if text_requires_cjk(paragraph):
                has_word = any(ch not in " \n" and not text_requires_cjk(ch) for ch in paragraph)
                if has_word:
                    lines.extend(LayoutEngine._wrap_mixed(paragraph, font, max_width))
                else:
                    lines.extend(LayoutEngine._wrap_chars(paragraph, font, max_width))
            else:
                lines.extend(LayoutEngine._wrap_words(paragraph, font, max_width))
        return lines or [""]

    @staticmethod
    def _wrap_mixed(text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
        """Char-wrap CJK and keep each Latin word on one line."""
        tokens: list[str] = []
        word = ""
        for char in text:
            if char == " ":
                if word:
                    tokens.append(word)
                    word = ""
                tokens.append(" ")
            elif text_requires_cjk(char):
                if word:
                    tokens.append(word)
                    word = ""
                tokens.append(char)
            else:
                word += char
        if word:
            tokens.append(word)

        lines: list[str] = []
        cur = ""
        for token in tokens:
            if token == " ":
                if cur and font.getlength(cur + " ") <= max_width:
                    cur += " "
                continue
            if font.getlength(cur + token) <= max_width:
                cur += token
                continue
            moved = LayoutEngine._rebreak_for_punctuation(cur, token, font, max_width)
            if moved is None:
                moved = LayoutEngine._hang_spaced_unit(cur, token, font, max_width)
            if moved is not None:
                head, cur = moved
                if head:
                    lines.append(head)
                continue
            if cur.strip():
                lines.append(cur.rstrip())
            if font.getlength(token) <= max_width:
                cur = token
            else:
                broken = LayoutEngine._wrap_clock(token, font, max_width)
                if broken is None:
                    broken = LayoutEngine._wrap_grouped_number(token, font, max_width)
                if broken is None:
                    broken = LayoutEngine._wrap_number_unit(token, font, max_width)
                if broken is None:
                    broken = LayoutEngine._wrap_date(token, font, max_width)
                if broken is None:
                    broken = LayoutEngine._wrap_numeric_expression(token, font, max_width)
                if broken is None:
                    broken = LayoutEngine._wrap_range(token, font, max_width)
                if broken is None:
                    broken = LayoutEngine._wrap_chars(token, font, max_width)
                lines.extend(broken[:-1])
                cur = broken[-1] if broken else ""
        if cur.strip():
            lines.append(cur.rstrip())
        return lines

    @staticmethod
    def _rebreak_for_punctuation(
        cur: str, token: str, font: ImageFont.ImageFont, max_width: int
    ) -> tuple[str, str] | None:
        """Keep a closing mark with the previous glyph, and an opening mark with the next."""
        no_start = (
            "，。！？、；：」』】）》〉〕］｝”’…—～·%％℃°‰′″℉Ωμ"
            "㎡㎏㎝㎜㎞¥$€£￥元块角分个只名位岁,.;:!?)]"
        )
        no_end = "「『【（《〈〔［｛“‘([{"
        body = cur.rstrip()
        if len(body) < 2 or not token:
            return None
        closing_run = all(char in no_start for char in token)
        opening_tail = len(token) == 1 and body[-1] in no_end
        if not closing_run and not opening_tail:
            return None
        head, tail = body[:-1], body[-1]
        # Pulling the last glyph must not leave an opening mark alone on the line above,
        # and must not split a Latin word to hang a mark. A digit may take the mark.
        if head and head[-1] in no_end:
            return None
        if closing_run and tail.isascii() and tail.isalpha():
            return None
        moved = tail + token
        if closing_run and cur.endswith(" "):
            spaced = tail + " " + token
            if font.getlength(spaced) <= max_width:
                moved = spaced
        if font.getlength(moved) > max_width:
            return None
        return head, moved

    @staticmethod
    def _hang_spaced_unit(
        cur: str, token: str, font: ImageFont.ImageFont, max_width: int
    ) -> tuple[str, str] | None:
        """Keep a spaced letter unit with the previous digit when that group fits."""
        body = cur.rstrip()
        if not body or not body[-1].isdigit() or not token.isascii() or not token.isalpha():
            return None
        gap = " " if cur.endswith(" ") else ""
        field = body[-1]
        if len(body) >= 3 and body[-3] == ":" and body[-2].isdigit():
            field = body[-2:]
        hung = field + gap + token
        if font.getlength(hung) > max_width:
            return None
        return body[: -len(field)].rstrip(), hung

    @staticmethod
    def _wrap_clock(token: str, font: ImageFont.ImageFont, max_width: int) -> list[str] | None:
        """Keep each clock field intact, and hang its colon on the previous field."""
        start = 0
        while start < len(token) and token[start].isascii() and token[start].isalpha():
            start += 1
        end = len(token)
        while end > start and token[end - 1].isascii() and token[end - 1].isalpha():
            end -= 1
        prefix, body, suffix = token[:start], token[start:end], token[end:]
        if prefix or suffix:
            lines = LayoutEngine._wrap_clock(body, font, max_width)
            if lines is None:
                return None
            if prefix:
                if font.getlength(prefix + lines[0]) <= max_width:
                    lines[0] = prefix + lines[0]
                elif font.getlength(prefix) <= max_width:
                    lines.insert(0, prefix)
                else:
                    return None
            if suffix:
                if font.getlength(lines[-1] + suffix) <= max_width:
                    lines[-1] += suffix
                elif font.getlength(suffix) <= max_width:
                    lines.append(suffix)
                else:
                    return None
            return lines
        parts = token.split(":")
        if not 2 <= len(parts) <= 3:
            return None
        hour, *rest = parts
        if not hour.isdigit() or not 1 <= len(hour) <= 2:
            return None
        if any(len(part) != 2 or not part.isdigit() for part in rest):
            return None
        if len(rest) == 1:
            mark = f":{rest[0]}"
            for start in range(len(hour)):
                hung = hour[start:] + mark
                if font.getlength(hung) <= max_width:
                    head = hour[:start]
                    return [head, hung] if head else [hung]
            return None
        lines: list[str] = []
        cur = ""
        for field in (hour, *rest):
            while True:
                piece = field if not cur else f":{field}"
                if font.getlength(cur + piece) <= max_width:
                    cur += piece
                    break
                if ":" in cur:
                    head, tail = cur.rsplit(":", 1)
                    if (
                        tail
                        and font.getlength(head + ":") <= max_width
                        and font.getlength(tail) <= max_width
                    ):
                        lines.append(head + ":")
                        cur = tail
                        continue
                if (
                    cur
                    and font.getlength(cur + ":") <= max_width
                    and font.getlength(field) <= max_width
                ):
                    lines.append(cur + ":")
                    cur = field
                    break
                return None
        if cur:
            lines.append(cur)
        return lines

    @staticmethod
    def _wrap_grouped_number(
        token: str, font: ImageFont.ImageFont, max_width: int
    ) -> list[str] | None:
        """Keep each thousands group intact, and hang its comma on the previous digit."""
        unit = ""
        body = token
        split = len(token)
        while split > 0 and token[split - 1].isascii() and token[split - 1].isalpha():
            split -= 1
        if 0 < split < len(token):
            unit = token[split:]
            body = token[:split]
        fraction = ""
        if body.count(".") == 1:
            body, fraction = body.split(".", 1)
            if not fraction.isdigit():
                return None
        elif "." in body:
            return None
        parts = body.split(",")
        if len(parts) < 2 or not parts[0].isdigit() or not 1 <= len(parts[0]) <= 3:
            return None
        if any(len(part) != 3 or not part.isdigit() for part in parts[1:]):
            return None
        lines: list[str] = []
        cur = ""
        for part in parts:
            piece = part if not cur else f",{part}"
            if font.getlength(cur + piece) <= max_width:
                cur += piece
                continue
            if cur and font.getlength(cur + ",") <= max_width and font.getlength(part) <= max_width:
                lines.append(cur + ",")
                cur = part
                continue
            return None
        if fraction:
            attached = False
            while not attached:
                if font.getlength(cur + "." + fraction) <= max_width:
                    cur = cur + "." + fraction
                    attached = True
                    break
                if font.getlength(cur + ".") <= max_width and font.getlength(fraction) <= max_width:
                    lines.append(cur + ".")
                    cur = fraction
                    attached = True
                    break
                if "," not in cur:
                    break
                head, tail = cur.rsplit(",", 1)
                if (
                    not tail
                    or font.getlength(head + ",") > max_width
                    or font.getlength(tail) > max_width
                ):
                    break
                lines.append(head + ",")
                cur = tail
            if not attached:
                return None
        if unit:
            if font.getlength(cur + unit) <= max_width:
                cur += unit
            elif font.getlength(unit) <= max_width:
                if cur:
                    lines.append(cur)
                cur = unit
            else:
                return None
        if cur:
            lines.append(cur)
        return lines

    @staticmethod
    def _wrap_date(token: str, font: ImageFont.ImageFont, max_width: int) -> list[str] | None:
        """Keep a date's year, month, and day intact, and hang each separator."""
        if token.count("-") == 2 and "/" not in token:
            sep = "-"
        elif token.count("/") == 2 and "-" not in token:
            sep = "/"
        else:
            return None
        year, month, day = token.split(sep)
        if not (
            len(year) == 4
            and year.isdigit()
            and len(month) == 2
            and month.isdigit()
            and len(day) == 2
            and day.isdigit()
        ):
            return None
        lines: list[str] = []
        cur = ""
        for field in (year, month, day):
            while True:
                piece = field if not cur else sep + field
                if font.getlength(cur + piece) <= max_width:
                    cur += piece
                    break
                if sep in cur:
                    head, tail = cur.rsplit(sep, 1)
                    if (
                        tail
                        and font.getlength(head + sep) <= max_width
                        and font.getlength(tail) <= max_width
                    ):
                        lines.append(head + sep)
                        cur = tail
                        continue
                if (
                    cur
                    and font.getlength(cur + sep) <= max_width
                    and font.getlength(field) <= max_width
                ):
                    lines.append(cur + sep)
                    cur = field
                    break
                return None
        if cur:
            lines.append(cur)
        return lines

    @staticmethod
    def _wrap_numeric_expression(
        token: str, font: ImageFont.ImageFont, max_width: int
    ) -> list[str] | None:
        """Keep both operands intact, and keep a trailing unit on the second."""
        split = len(token)
        while split > 0 and token[split - 1].isascii() and token[split - 1].isalpha():
            split -= 1
        if split > 0 and token[split - 1] in "㎡㎏㎝㎜㎞℃°‰′″℉Ωμ％%":
            split -= 1
        unit = token[split:]
        body = token[:split]
        operators = [mark for mark in ("×", "x", "*", "/") if mark in body]
        if len(operators) != 1 or body.count(operators[0]) != 1:
            return None
        operator = operators[0]
        left, right = body.split(operator, 1)

        def _operand(part: str) -> bool:
            if not part or part.count(".") > 1 or part.startswith(".") or part.endswith("."):
                return False
            return part.replace(".", "", 1).isdigit()

        if not _operand(left) or not _operand(right):
            return None
        right += unit
        if font.getlength(body + unit) <= max_width:
            return [body + unit]
        hung = left + operator
        if font.getlength(hung) <= max_width and font.getlength(right) <= max_width:
            return [hung, right]
        opened = operator + right
        if font.getlength(left) <= max_width and font.getlength(opened) <= max_width:
            return [left, opened]
        return None

    @staticmethod
    def _wrap_range(token: str, font: ImageFont.ImageFont, max_width: int) -> list[str] | None:
        """Keep both sides of a number range intact, and hang the dash on the first."""
        dashes = [mark for mark in ("-", "–") if mark in token]
        if len(dashes) != 1 or token.count(dashes[0]) != 1:
            return None
        dash = dashes[0]
        left, right = token.split(dash, 1)
        if not left.isdigit() or not right.isdigit():
            return None
        if font.getlength(token) <= max_width:
            return [token]
        hung = left + dash
        if font.getlength(hung) <= max_width and font.getlength(right) <= max_width:
            return [hung, right]
        opened = dash + right
        if font.getlength(left) <= max_width and font.getlength(opened) <= max_width:
            return [left, opened]
        return None

    @staticmethod
    def _wrap_number_unit(
        token: str, font: ImageFont.ImageFont, max_width: int
    ) -> list[str] | None:
        """Keep a letter unit with the last digit when that group fits the line."""
        split = 0
        seen_dot = False
        while split < len(token):
            char = token[split]
            if char.isdigit():
                split += 1
                continue
            if (
                char == "."
                and not seen_dot
                and split > 0
                and split + 1 < len(token)
                and token[split + 1].isdigit()
            ):
                seen_dot = True
                split += 1
                continue
            break
        unit = token[split:]
        if split == 0 or not unit.isascii() or not unit.isalpha():
            return None
        number = token[:split]
        for index in range(len(number)):
            if number[index] == ".":
                continue
            hung = number[index:] + unit
            if font.getlength(hung) <= max_width:
                head = number[:index]
                lines = LayoutEngine._wrap_chars(head, font, max_width) if head else []
                lines.append(hung)
                return lines
        return None

    @staticmethod
    def _wrap_chars(text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
        lines: list[str] = []
        cur = ""
        for char in text:
            if font.getlength(cur + char) <= max_width:
                cur += char
                continue
            moved = LayoutEngine._rebreak_for_punctuation(cur, char, font, max_width)
            if moved is not None:
                head, cur = moved
                if head:
                    lines.append(head)
                continue
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
                broken = LayoutEngine._wrap_clock(word, font, max_width)
                if broken is None:
                    broken = LayoutEngine._wrap_grouped_number(word, font, max_width)
                if broken is None:
                    broken = LayoutEngine._wrap_number_unit(word, font, max_width)
                if broken is None:
                    broken = LayoutEngine._wrap_date(word, font, max_width)
                if broken is None:
                    broken = LayoutEngine._wrap_numeric_expression(word, font, max_width)
                if broken is None:
                    broken = LayoutEngine._wrap_range(word, font, max_width)
                if broken is None:
                    broken = LayoutEngine._wrap_chars(word, font, max_width)
                lines.extend(broken)
                cur = ""
        if cur:
            lines.append(cur)
        return lines
