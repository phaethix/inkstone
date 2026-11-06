"""Deferred lettering: composite plan text onto blank finished-page art."""

from __future__ import annotations

from PIL import Image, ImageDraw, ImageStat

from core.comic.fonts import resolve_font
from core.comic.layout import BUBBLE_PAD, LayoutEngine, _line_height
from core.comic.lettering_lang import sanitize_lettering_text, source_lettering_script
from core.schemas import ComicPagePlan

# Bump when overlay geometry/chrome behavior changes so resume can re-letter blanks.
LETTERING_VERSION = "deferred_v4"

_MARGIN_FRAC = 0.04
_MAX_W_FRAC = 0.36
_MAX_H_FRAC = 0.16
# Shared with LayoutEngine._draw_bubble so fitting and drawing wrap identically.
_PAD = BUBBLE_PAD


def _luma(rgb: tuple[int, int, int]) -> float:
    return 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]


def text_ink_for_fill(fill: tuple[int, int, int]) -> str:
    """Black ink on light fills, white ink on dark ones (legibility)."""
    return "white" if _luma(fill) < 128 else "black"


def blend_fill_with_art(
    image: Image.Image,
    box: tuple[int, int, int, int],
) -> tuple[int, int, int]:
    """Sample the artwork just outside the box and derive a tone that belongs.

    A fixed warm-white paper block reads as a sticker on a dark night scene.
    Instead we take the mean color of a ring of surrounding pixels and pull the
    fill toward it, lifting slightly so glyphs stay readable. The result is a
    scene-derived tone (dark blue on a night page, pale on a bright page) rather
    than a one-size-fits-all white.
    """
    x, y, w, h = box
    iw, ih = image.size
    ring = 18
    left = max(0, x - ring)
    top = max(0, y - ring)
    right = min(iw, x + w + ring)
    bottom = min(ih, y + h + ring)
    acc = [0.0, 0.0, 0.0]
    total = 0
    # Four strips around the box (skip the box interior so text area is excluded).
    for rect in (
        (left, top, right, max(top + 1, y)),
        (left, min(bottom - 1, y + h), right, bottom),
        (left, top, max(left + 1, x), bottom),
        (min(right - 1, x + w), top, right, bottom),
    ):
        if rect[0] < rect[2] and rect[1] < rect[3]:
            crop = image.crop(rect).convert("RGB")
            mean = ImageStat.Stat(crop).mean
            weight = crop.size[0] * crop.size[1]
            for c in range(3):
                acc[c] += mean[c] * weight
            total += weight
    if total == 0:
        return (250, 247, 240)
    avg = tuple(int(acc[c] / total) for c in range(3))
    # Blend 70% toward the scene, keep a gentle lift for contrast.
    lift = 26
    base = (250, 247, 240)
    blended = tuple(int(0.3 * base[c] + 0.7 * avg[c]) for c in range(3))
    return tuple(min(255, v + lift) for v in blended)


def pick_lettering_region(
    image: Image.Image,
    frac_box: tuple[float, float, float, float],
) -> tuple[int, int, int, int]:
    """Choose a flat, empty area via deterministic content analysis.

    The LLM-supplied normalized box is only a size/preferred-location hint. We
    score a grid of candidate cells by local luminance variance (low variance =
    calm sky / empty space, high variance = busy subject we must not cover) and
    keep the calmest cell near the preferred location. This replaces hardcoded
    corner coordinates with a decision driven by the actual artwork.
    """
    iw, ih = image.size
    fx, fy, fw, fh = frac_box
    want_w = max(0.08, min(fw, 1.0 - 2 * _MARGIN_FRAC)) * iw
    want_h = max(0.05, min(fh, 1.0 - 2 * _MARGIN_FRAC)) * ih
    pref_cx = (fx + fw / 2) * iw
    pref_cy = (fy + fh / 2) * ih
    gray = image.convert("L")
    cols, rows = 6, 8
    cw, ch = iw / cols, ih / rows
    best: tuple[float, int, int] | None = None
    for r in range(rows):
        for c in range(cols):
            cx0, cy0 = int(c * cw), int(r * ch)
            cx1, cy1 = int((c + 1) * cw), int((r + 1) * ch)
            if cx1 <= cx0 or cy1 <= cy0:
                continue
            cell = gray.crop((cx0, cy0, cx1, cy1))
            # Variance = stddev^2; calm regions score low.
            variance = ImageStat.Stat(cell).stddev[0] ** 2
            cx = (cx0 + cx1) / 2
            cy = (cy0 + cy1) / 2
            # The hinted location dominates: each lettering job has its own
            # intended area (right-top dialogue, left-bottom caption, ...).
            # Calmness only breaks ties nearby, so different jobs never all
            # collapse onto the single flattest cell in the whole image.
            distance = ((cx - pref_cx) ** 2 + (cy - pref_cy) ** 2) ** 0.5
            score = distance + min(variance, 400.0) * 0.5
            if best is None or score < best[0]:
                best = (score, int(cx), int(cy))
    _, ccx, ccy = best or (0.0, int(pref_cx), int(pref_cy))
    bw, bh = int(want_w), int(want_h)
    margin_x = int(iw * _MARGIN_FRAC)
    margin_y = int(ih * _MARGIN_FRAC)
    x = min(max(int(ccx - bw / 2), margin_x), iw - margin_x - bw)
    y = min(max(int(ccy - bh / 2), margin_y), ih - margin_y - bh)
    return (x, y, bw, bh)


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
    # Narration captions must never sit across the middle of the finished page:
    # that is where the figures are. Anime faces defeat cv2, and a per-panel
    # band midpoint is meaningless on a full-bleed page, so apply a page-level
    # edge-band rule that holds regardless of which band placed the caption.
    if kind == "caption":
        cy = y + h / 2
        if 0.2 <= cy <= 0.8:
            y = _MARGIN_FRAC if cy <= 0.5 else 1.0 - _MARGIN_FRAC - h
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
        # _PAD must match _draw_bubble's pad: fitting wraps at (max_w - 2*_PAD)
        # and the drawer re-wraps at (need_w - 2*_PAD). When they disagree the
        # drawer folds a short sentence that fit on one line, splitting words
        # like 「坦诚」. Sharing the pad constant keeps both widths identical.
        need_w = min(max_w, max(24, int(longest) + 2 * _PAD))
        need_h = min(max_h, engine._bubble_height(text, need_w))

    x = min(max(ax, margin_x), page_w - margin_x - need_w)
    y = min(max(ay, margin_y), page_h - margin_y - need_h)
    x = max(margin_x, min(x, page_w - margin_x - need_w))
    y = max(margin_y, min(y, page_h - margin_y - need_h))
    return (x, y, need_w, need_h)


def _overlap_area(box: tuple[int, int, int, int], other: tuple[int, int, int, int]) -> int:
    x, y, bw, bh = box
    ox, oy, ow, oh = other
    ix = max(0, min(x + bw, ox + ow) - max(x, ox))
    iy = max(0, min(y + bh, oy + oh) - max(y, oy))
    return ix * iy


def _clamp_pixel_box(
    box: tuple[int, int, int, int], page_size: tuple[int, int]
) -> tuple[int, int, int, int]:
    x, y, bw, bh = box
    page_w, page_h = page_size
    margin_x = max(4, int(page_w * _MARGIN_FRAC))
    margin_y = max(4, int(page_h * _MARGIN_FRAC))
    bw = min(bw, max(1, page_w - 2 * margin_x))
    bh = min(bh, max(1, page_h - 2 * margin_y))
    x = min(max(x, margin_x), page_w - margin_x - bw)
    y = min(max(y, margin_y), page_h - margin_y - bh)
    return (x, y, bw, bh)


def _shift_off_faces(
    box: tuple[int, int, int, int],
    faces: list[tuple[int, int, int, int]],
    page_size: tuple[int, int],
) -> tuple[int, int, int, int]:
    """Move a lettering box off face rectangles. No faces leaves the box put."""
    if not faces:
        return box
    x, y, bw, bh = box
    page_h = page_size[1]
    margin_y = max(4, int(page_h * _MARGIN_FRAC))
    candidates = [box]
    for fx, fy, fw, fh in faces:
        candidates.append((x, fy - bh - 4, bw, bh))
        candidates.append((x, fy + fh + 4, bw, bh))
        candidates.append((fx - bw - 4, y, bw, bh))
        candidates.append((fx + fw + 4, y, bw, bh))
    candidates.append((x, margin_y, bw, bh))
    candidates.append((x, page_h - margin_y - bh, bw, bh))
    clamped = [_clamp_pixel_box(item, page_size) for item in candidates]
    clear = [item for item in clamped if all(_overlap_area(item, face) == 0 for face in faces)]
    if clear:
        return min(clear, key=lambda item: abs(item[0] - x) + abs(item[1] - y))
    return min(clamped, key=lambda item: sum(_overlap_area(item, face) for face in faces))


def detect_page_faces(image: Image.Image) -> list[tuple[int, int, int, int]]:
    """Face rectangles on a blank page. Missing cv2 or no face yields none."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return []
    from core.comic.consistency import _detect_faces, _load_cascade

    cascade = _load_cascade(cv2, "haarcascade_frontalface_default.xml")
    if cascade is None:
        return []
    try:
        rgb = np.array(image.convert("RGB"))
        return _detect_faces(cascade, rgb, cv2)
    except Exception:  # noqa: BLE001 — a detector failure must not block lettering
        return []


def letter_finished_page(
    blank: Image.Image,
    plan: ComicPagePlan,
    *,
    font_path: str | None = None,
    source_text: str = "",
    placement_boxes: list[tuple[int, int, int, int]] | None = None,
) -> Image.Image:
    img = blank.convert("RGB").copy()
    draw = ImageDraw.Draw(img)
    engine = LayoutEngine(font_path=font_path)
    w, h = img.size
    faces = detect_page_faces(img)
    jobs = resolve_lettering_jobs(plan, source_text=source_text)
    use_vision = placement_boxes is not None and len(placement_boxes) == len(jobs)
    occupied: list[tuple[int, int, int, int]] = []
    for i, (_pid, kind, text, anchor_frac) in enumerate(jobs):
        if use_vision:
            # Vision model chose the *position*; keep it (do not let the
            # band/caption edge heuristics push it away), but recompute the box
            # size from the real text so the chrome stays tight.
            anchor = (placement_boxes or [])[i]
        else:
            # Fallback: deterministic flat-area analysis near the hinted spot.
            region = pick_lettering_region(blank, anchor_frac)
            ax, ay, aw, ah = region
            anchor = (ax, ay, aw, ah)
        box = fit_lettering_box(engine, kind, text, anchor, (w, h))
        box = _shift_off_faces(box, faces, (w, h))
        box = _avoid_overlap(box, occupied, (w, h))
        box = _shift_off_faces(box, faces, (w, h))
        occupied.append(box)
        # Derive the bubble tone from the local artwork so it blends into the
        # scene instead of reading as a fixed white sticker.
        fill = blend_fill_with_art(blank, box)
        ink = text_ink_for_fill(fill)
        if kind == "caption":
            engine._draw_caption(draw, box, text, fill=fill, ink=ink)
        elif kind == "sfx":
            engine._draw_sfx(draw, box, text)
        else:
            engine._draw_bubble(draw, box, text, fill=fill, ink=ink)
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
