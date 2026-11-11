"""tests/test_reference_neutral.py — portrait references must be scene-free.

User report traced scene bleed: both portraits were generated with full
backgrounds (misty river, lighthouse, ships, railing). Sent as i2i references,
the model copies that world into every panel — chapter 2 (an indoor inn)
rendered open river views. Neutralizing the reference background removes the
scene vocabulary while keeping the face and outfit the reference exists for.
"""

from pathlib import Path

import numpy as np
from PIL import Image

from core.pipelines.creative_comic import _neutral_reference_path


def _portrait_with_scene(tmp_path: Path) -> Path:
    """A portrait-like image: person block center, busy scenery edges."""
    img = Image.new("RGB", (200, 200), (150, 170, 190))  # misty blue backdrop
    # Lighthouse + water bands on the edges.
    for y in range(200):
        for x in range(30):
            img.putpixel((x, y), (90, 120, 150))
    img.paste(Image.new("RGB", (90, 140), (200, 180, 160)), (55, 40))  # figure
    src = tmp_path / "portrait.png"
    img.save(src)
    return src


def test_scene_background_is_replaced_with_neutral_studio(tmp_path):
    out = _neutral_reference_path(str(_portrait_with_scene(tmp_path)))
    assert out, "scene reference left as-is"
    img = Image.open(out).convert("RGB")
    # Corners are now the neutral backdrop, not the misty river blue.
    corner = img.getpixel((3, 3))
    assert max(corner) - min(corner) < 12, f"corner still scene-colored: {corner}"


def test_already_neutral_reference_passes_through(tmp_path):
    img = Image.new("RGB", (120, 120), (245, 245, 245))
    img.paste(Image.new("RGB", (60, 80), (180, 160, 150)), (30, 20))
    src = tmp_path / "clean.png"
    img.save(src)
    out = _neutral_reference_path(str(src))
    assert out == str(src), "neutral reference must not be rewritten"


def test_neutralized_reference_keeps_subject(tmp_path):
    src = _portrait_with_scene(tmp_path)
    out = _neutral_reference_path(str(src))
    before = np.asarray(Image.open(src).convert("RGB"))
    after = np.asarray(Image.open(out).convert("RGB"))
    center_b = before[80:120, 80:120].mean(axis=(0, 1))
    center_a = after[80:120, 80:120].mean(axis=(0, 1))
    assert np.abs(center_b - center_a).max() < 12, "subject pixels changed"
