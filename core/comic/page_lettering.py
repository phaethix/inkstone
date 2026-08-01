"""Deferred lettering: composite plan text onto blank finished-page art."""

from __future__ import annotations

from PIL import Image, ImageDraw

from core.comic.fonts import resolve_font
from core.comic.layout import LayoutEngine, _line_height
from core.comic.lettering_lang import sanitize_lettering_text, source_lettering_script
from core.schemas import ComicPagePlan

# Bump when overlay geometry/chrome behavior changes so resume can re-letter blanks.
LETTERING_VERSION = "deferred_v3"

_MARGIN_FRAC = 0.04
_MAX_W_FRAC = 0.36
_MAX_H_FRAC = 0.16
_PAD = 8


def resolve_lettering_jobs(
    plan: ComicPagePlan,
    *,
    source_text: str = "",
) -> list[tuple[str, str, str, tuple[float, float, float, float]]]:
    script = source_lettering_script(source_text) if source_text else "cjk"
    box_map = {(b.panel_id, b.kind): (b.x, b.y, b.w, b.h) for b in plan.lettering_boxes}
    n = max(1, len(plan.panels))
    jobs: list[tuple[str, str, str, tuple[float, float, float, float]]] = []
    for i, panel in enumerate(plan.panels):
        band_y0 = i / n
        band_h = 1.0 / n
        slot = 0
        for kind in ("caption", "dialogue", "sfx"):
            raw = getattr(panel, kind)
            text = sanitize_lettering_text(raw, kind=kind, script=script)
            if not text:
                continue
            key = (panel.panel_id, kind)
            if key in box_map:
                box = _safe_anchor(*box_map[key], band_y0=band_y0, band_h=band_h, kind=kind)
            else:
                box = _heuristic_box(kind, band_y0, band_h, slot=slot)
            slot += 1
            jobs.append((panel.panel_id, kind, text, box))
    return jobs


def _clamp_box(x: float, y: float, w: float, h: float) -> tuple[float, float, float, float]:
    m = _MARGIN_FRAC
    w = min(max(w, 0.08), 1.0 - 2 * m)
    h = min(max(h, 0.05), 1.0 - 2 * m)
    x = min(max(x, m), 1.0 - m - w)
    y = min(max(y, m), 1.0 - m - h)
    return (x, y, w, h)


def _safe_anchor(
    x: float,
    y: float,
    w: float,
    h: float,
    *,
    band_y0: float,
    band_h: float,
    kind: str,
) -> tuple[float, float, float, float]:
    """Clamp LLM boxes and nudge vertically away from band center (faces)."""
    x, y, w, h = _clamp_box(x, y, min(w, _MAX_W_FRAC + 0.05), min(h, _MAX_H_FRAC + 0.05))
    cy = y + h / 2
    band_mid = band_y0 + band_h * 0.5
    # If the box sits in the middle third of its panel band, push to top or bottom edge.
    if abs(cy - band_mid) < band_h * 0.18:
        if kind == "caption" or cy <= band_mid:
            y = band_y0 + band_h * 0.06
        else:
            y = band_y0 + band_h * 0.72
        y = min(max(y, _MARGIN_FRAC), 1.0 - _MARGIN_FRAC - h)
    return _clamp_box(x, y, w, h)


def _heuristic_box(
    kind: str, band_y0: float, band_h: float, *, slot: int = 0
) -> tuple[float, float, float, float]:
    # Prefer edges of the panel band — avoid vertical center where faces usually sit.
    if kind == "caption":
        return _clamp_box(0.06, band_y0 + 0.05 * band_h + slot * 0.04, 0.55, 0.16 * band_h)
    if kind == "sfx":
        return _clamp_box(0.58, band_y0 + 0.08 * band_h, 0.30, 0.14 * band_h)
    # dialogue: top-right of band
    return _clamp_box(0.52, band_y0 + 0.08 * band_h + slot * 0.05, 0.40, 0.18 * band_h)


def fit_lettering_box(
    engine: LayoutEngine,
    kind: str,
    text: str,
    anchor: tuple[int, int, int, int],
    page_size: tuple[int, int],
) -> tuple[int, int, int, int]:
    """Shrink chrome to text size; keep fully inside the page with a margin."""
    ax, ay, aw, ah = anchor
    page_w, page_h = page_size
    margin_x = max(4, int(page_w * _MARGIN_FRAC))
    margin_y = max(4, int(page_h * _MARGIN_FRAC))
    max_w = max(24, min(aw, int(page_w * _MAX_W_FRAC), page_w - 2 * margin_x))
    max_h = max(24, min(ah, int(page_h * _MAX_H_FRAC), page_h - 2 * margin_y))

    font, _ = resolve_font(text, font_path=engine.font_path)
    if kind == "sfx":
        lines = engine._wrap_text(text, font, max(20, max_w - 8))
        line_h = _line_height(font)
        need_h = min(max_h, max(line_h, len(lines) * line_h))
        longest = max((font.getlength(line) for line in lines), default=0)
        need_w = min(max_w, int(longest) + 8)
    else:
        lines = engine._wrap_text(text, font, max(8, max_w - 2 * _PAD))
        longest = max((font.getlength(line) for line in lines), default=0)
        need_w = min(max_w, max(24, int(longest) + 2 * _PAD))
        need_h = min(max_h, engine._bubble_height(text, need_w))

    x = min(max(ax, margin_x), page_w - margin_x - need_w)
    y = min(max(ay, margin_y), page_h - margin_y - need_h)
    x = max(margin_x, min(x, page_w - margin_x - need_w))
    y = max(margin_y, min(y, page_h - margin_y - need_h))
    return (x, y, need_w, need_h)


def letter_finished_page(
    blank: Image.Image,
    plan: ComicPagePlan,
    *,
    font_path: str | None = None,
    source_text: str = "",
) -> Image.Image:
    img = blank.convert("RGB").copy()
    draw = ImageDraw.Draw(img)
    engine = LayoutEngine(font_path=font_path)
    w, h = img.size
    occupied: list[tuple[int, int, int, int]] = []
    for _pid, kind, text, (nx, ny, nw, nh) in resolve_lettering_jobs(plan, source_text=source_text):
        anchor = (int(nx * w), int(ny * h), max(8, int(nw * w)), max(8, int(nh * h)))
        box = fit_lettering_box(engine, kind, text, anchor, (w, h))
        box = _avoid_overlap(box, occupied, (w, h))
        occupied.append(box)
        if kind == "caption":
            engine._draw_caption(draw, box, text)
        elif kind == "sfx":
            engine._draw_sfx(draw, box, text)
        else:
            engine._draw_bubble(draw, box, text)
    return img


def _avoid_overlap(
    box: tuple[int, int, int, int],
    occupied: list[tuple[int, int, int, int]],
    page_size: tuple[int, int],
) -> tuple[int, int, int, int]:
    """Nudge a box downward slightly when it intersects an already-placed box."""
    x, y, bw, bh = box
    page_w, page_h = page_size
    margin_y = max(4, int(page_h * _MARGIN_FRAC))
    for _ in range(6):
        hit = False
        for ox, oy, ow, oh in occupied:
            if x < ox + ow and x + bw > ox and y < oy + oh and y + bh > oy:
                y = oy + oh + 4
                hit = True
                break
        if not hit:
            break
    y = min(max(y, margin_y), page_h - margin_y - bh)
    return (x, y, bw, bh)
