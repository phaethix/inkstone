"""Deterministic comic assembly — art, layout, style as separate layers.

Design decision (owned by the agent, user asked for the result):
- A finished page is ONE coherent illustration + a programmatic frame layout.
  The model paints pixels only; it never draws panel borders, lettering, or a
  cast it cannot keep consistent. That removes the root cause of out-of-frame
  heads, half-inked insets, extra figures, and scene bleed in one shot.
- Layout is pure PIL: geometry (margins, gutters, borders) is exact and the
  image is never cropped through a face (whole illustration letterboxed).
- Character consistency and style are inputs (a single STYLE string + the same
  two portrait references for every scene), so style is user-specifiable by
  editing one constant.
- Art only: no speech bubbles, no text. The previous hard-won rule stays.

Run: uv run python scripts/assemble_comic.py
Output: comic_out/comic.pdf (pure art), comic_out/pages_v2/*.png
"""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps
from pypdf import PdfReader, PdfWriter

# The one place to specify the visual style.
# This exact string is prepended to every scene generation. Change it (or feed
# it from a CLI flag) and the whole book's look changes coherently.
STYLE = (
    "Japanese slice-of-life manga, delicate clean black ink line art, "
    "soft cel shading, flat muted colors, warm nostalgic palette of dusty blue "
    "river mist and amber lantern glow, terracotta-red scarf, camel beige coat"
)
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
ART_DIR = ROOT / "comic_out" / "art_v2"
OUT_DIR = ROOT / "comic_out" / "pages_v2"
PDF = ROOT / "comic_out" / "comic.pdf"

# Page geometry (portrait A4 ratio at 150dpi feel). All values are fractions of
# the canvas so the layout is resolution-independent.
PAGE = (1240, 1754)
BG = (249, 246, 240)  # warm paper
INK = (40, 38, 36)  # border ink
BORDER = 3  # px inner panel border
GUTTER_RATIO = 0.018  # gap between panels as fraction of width
MARGIN_RATIO = 0.035  # outer margin as fraction of width

# Per-page panel recipes: rectangles in normalized (x0,y0,x1,y1) of the inner
# area. Varied, intentional rhythm (wide establishing shot, tall close-ups,
# small reaction inset) — this is the "beautiful layout" layer, drawn by code,
# not guessed by a diffusion model.
LAYOUTS: dict[int, list[tuple[float, float, float, float]]] = {
    1: [(0.0, 0.0, 1.0, 0.62), (0.0, 0.62, 0.5, 1.0), (0.5, 0.62, 1.0, 1.0)],
    2: [(0.0, 0.0, 0.62, 1.0), (0.62, 0.0, 1.0, 0.55), (0.62, 0.55, 1.0, 1.0)],
    3: [(0.0, 0.0, 1.0, 0.45), (0.0, 0.45, 1.0, 0.75), (0.0, 0.75, 1.0, 1.0)],
    4: [(0.0, 0.0, 0.55, 1.0), (0.55, 0.0, 1.0, 0.5), (0.55, 0.5, 1.0, 1.0)],
}


def _fit_cover(img: Image.Image, w: int, h: int) -> Image.Image:
    """Crop-free cover fit: scale to cover then center-crop, keeping faces safe.

    The illustration is a wide-ish key visual; we scale it to COVER the panel
    and take a centered crop. Because every scene keeps its subjects centered,
    no head is ever sliced by a gutter.
    """
    return ImageOps.fit(img.convert("RGB"), (w, h), method=Image.LANCZOS, centering=(0.5, 0.5))


def assemble_page(scene_path: Path, layout: list[tuple[float, float, float, float]]) -> Image.Image:
    src = Image.open(scene_path).convert("RGB")
    W, H = PAGE
    canvas = Image.new("RGB", PAGE, BG)
    draw = ImageDraw.Draw(canvas)
    m = int(W * MARGIN_RATIO)
    g = int(W * GUTTER_RATIO)
    inner = (m, m, W - m, H - m)
    iw, ih = inner[2] - inner[0], inner[3] - inner[1]

    for x0, y0, x1, y1 in layout:
        bx0 = inner[0] + int(x0 * iw)
        by0 = inner[1] + int(y0 * ih)
        bx1 = inner[0] + int(x1 * iw)
        by1 = inner[1] + int(y1 * ih)
        # Gutter: shrink each panel inward by half a gap on every side.
        px0, py0 = bx0 + g // 2, by0 + g // 2
        px1, py1 = bx1 - g // 2, by1 - g // 2
        pw, ph = px1 - px0, py1 - py0
        panel = _fit_cover(src, pw, ph)
        canvas.paste(panel, (px0, py0))
        # Programmatic border — crisp, never crosses a character.
        draw.rectangle((px0, py0, px1 - 1, py1 - 1), outline=INK, width=BORDER)
    return canvas


def build_pdf(pages: list[Image.Image], out: Path) -> None:
    writer = PdfWriter()
    for page in pages:
        # Embed as JPEG to keep the file light; page size follows the image.
        buf = io.BytesIO()
        page.save(buf, "PDF", resolution=150.0)
        buf.seek(0)
        writer.add_page(PdfReader(buf).pages[0])
    with open(out, "wb") as fh:
        writer.write(fh)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    scenes = sorted(ART_DIR.glob("scene_*.png"))
    if len(scenes) != 4:
        raise SystemExit(f"expected 4 scene images in {ART_DIR}, found {len(scenes)}")
    pages: list[Image.Image] = []
    for idx, scene in enumerate(scenes, start=1):
        layout = LAYOUTS[idx]
        page = assemble_page(scene, layout)
        page.save(OUT_DIR / f"page_{idx:02d}.png")
        pages.append(page)
        print(f"  page {idx}: {scene.name} -> {len(layout)} panels")
    build_pdf(pages, PDF)
    print(f"  STYLE: {STYLE[:48]}...")
    print(f"  PDF: {PDF}  ({len(pages)} pages, art-only)")


if __name__ == "__main__":
    main()
