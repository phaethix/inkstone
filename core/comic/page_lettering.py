"""Deferred lettering: composite plan text onto blank finished-page art."""

from __future__ import annotations

from PIL import Image, ImageDraw

from core.comic.layout import LayoutEngine
from core.schemas import ComicPagePlan

Kind = str  # "caption" | "dialogue" | "sfx"


def resolve_lettering_jobs(
    plan: ComicPagePlan,
) -> list[tuple[str, str, str, tuple[float, float, float, float]]]:
    box_map = {(b.panel_id, b.kind): (b.x, b.y, b.w, b.h) for b in plan.lettering_boxes}
    n = max(1, len(plan.panels))
    jobs: list[tuple[str, str, str, tuple[float, float, float, float]]] = []
    for i, panel in enumerate(plan.panels):
        band_y0 = i / n
        band_h = 1.0 / n
        for kind in ("caption", "dialogue", "sfx"):
            text = getattr(panel, kind)
            if not text:
                continue
            key = (panel.panel_id, kind)
            if key in box_map:
                box = _clamp_box(*box_map[key])
            else:
                box = _heuristic_box(kind, band_y0, band_h)
            jobs.append((panel.panel_id, kind, text, box))
    return jobs


def _clamp_box(x: float, y: float, w: float, h: float) -> tuple[float, float, float, float]:
    x = min(max(x, 0.0), 0.95)
    y = min(max(y, 0.0), 0.95)
    w = min(max(w, 0.08), 1.0 - x)
    h = min(max(h, 0.05), 1.0 - y)
    return (x, y, w, h)


def _heuristic_box(kind: str, band_y0: float, band_h: float) -> tuple[float, float, float, float]:
    if kind == "caption":
        return _clamp_box(0.1, band_y0 + 0.02 * band_h, 0.8, 0.22 * band_h)
    if kind == "sfx":
        return _clamp_box(0.55, band_y0 + 0.05 * band_h, 0.35, 0.2 * band_h)
    return _clamp_box(0.15, band_y0 + 0.55 * band_h, 0.7, 0.28 * band_h)


def letter_finished_page(
    blank: Image.Image,
    plan: ComicPagePlan,
    *,
    font_path: str | None = None,
) -> Image.Image:
    img = blank.convert("RGB").copy()
    draw = ImageDraw.Draw(img)
    engine = LayoutEngine(font_path=font_path)
    w, h = img.size
    for _pid, kind, text, (nx, ny, nw, nh) in resolve_lettering_jobs(plan):
        box = (int(nx * w), int(ny * h), max(8, int(nw * w)), max(8, int(nh * h)))
        if kind == "caption":
            engine._draw_caption(draw, box, text)
        elif kind == "sfx":
            engine._draw_sfx(draw, box, text)
        else:
            engine._draw_bubble(draw, box, text)
    return img
