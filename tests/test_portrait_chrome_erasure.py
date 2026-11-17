"""tests/test_portrait_chrome_erasure.py — portrait references must be chrome-free.

The boy's portrait carried a self-painted speech bubble ("你...你来了"). Sent
as an i2i reference it was traced verbatim into every panel (three panels, one
identical bubble). Two layers fix it: the neutralizer must erase bubble/text
strokes inside the kept subject region, and the shared portrait style must
declare chrome-free output up front. Option A of the 2026-11-12 decision.
"""

from pathlib import Path

import numpy as np
from PIL import Image

from core.pipelines.creative_comic import _neutral_reference_path


def _portrait_with_bubble(tmp_path: Path) -> Path:
    """A portrait-like image whose subject region contains a speech bubble."""
    img = Image.new("RGB", (200, 200), (150, 170, 190))  # misty blue backdrop
    # Busy scenery band on the left edge (drives neutralization).
    for y in range(200):
        for x in range(30):
            img.putpixel((x, y), (90, 120, 150))
    img.paste(Image.new("RGB", (90, 140), (200, 180, 160)), (55, 40))  # figure
    # White bubble with dark strokes INSIDE the kept ellipse (x 90-140, y 60-85).
    img.paste(Image.new("RGB", (50, 25), (255, 255, 255)), (90, 60))
    for x in range(96, 134, 6):  # dark "text" strokes
        img.paste(Image.new("RGB", (3, 9), (30, 30, 30)), (x, 68))
    src = tmp_path / "portrait.png"
    img.save(src)
    return src


def _stroke_pixels(img: Image.Image) -> int:
    """Count dark pixels in the bubble area after neutralization."""
    arr = np.asarray(img.convert("RGB"))
    region = arr[60:85, 90:140]
    return int((region.max(axis=2) < 90).sum())


def test_bubble_inside_subject_region_is_erased(tmp_path):
    """A self-painted bubble inside the kept ellipse must not survive."""
    out = _neutral_reference_path(str(_portrait_with_bubble(tmp_path)))
    img = Image.open(out).convert("RGB")
    strokes = _stroke_pixels(img)
    assert strokes < 10, f"bubble text still visible ({strokes} dark px)"


def test_bubble_area_inpaints_surrounding_tone(tmp_path):
    """Erased bubble pixels must not leave a white scar: they take on the
    surrounding figure tone, not paper white."""
    out = _neutral_reference_path(str(_portrait_with_bubble(tmp_path)))
    arr = np.asarray(Image.open(out).convert("RGB"))
    region = arr[60:85, 90:140]
    mean = region.reshape(-1, 3).mean(axis=0)
    assert mean[0] < 235, f"bubble area still paper-white: mean={mean}"


def test_clean_portrait_is_untouched_by_bubble_pass(tmp_path):
    """A portrait with no chrome keeps byte-identical output (no false erase)."""
    from core.pipelines.creative_comic import _erase_reference_bubble

    img = Image.new("RGB", (200, 200), (150, 170, 190))
    for y in range(200):
        for x in range(30):
            img.putpixel((x, y), (90, 120, 150))
    img.paste(Image.new("RGB", (90, 140), (200, 180, 160)), (55, 40))
    src = tmp_path / "clean.png"
    img.save(src)
    before = np.asarray(img.convert("RGB"))
    after_erase = np.asarray(_erase_reference_bubble(img.convert("RGB")))
    assert np.array_equal(before, after_erase), "clean portrait was modified"
    out = _neutral_reference_path(str(src))
    after = np.asarray(Image.open(out).convert("RGB"))
    # The neutral composite fades from 40% of the ellipse radius outward, so
    # only the fade-free core must equal the erase output exactly.
    assert np.array_equal(after_erase[80:120, 80:130], after[80:120, 80:130])
