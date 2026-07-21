"""tests/test_consistency_l2l3.py — reference collection + face fallback (no GPU).

- ``collect_reference_images`` is pure logic (no network/images).
- ``apply_l3`` degrades gracefully: it returns the panel unchanged when
  ``cv2`` is missing or no face is detected. cv2-dependent assertions are
  skipped where cv2 is not installed, so CI stays green without opencv.
"""

import pytest
from PIL import Image

from core.comic.consistency import ConsistencyEngine
from core.schemas import CharacterAsset, Panel


def _asset(name: str, portrait: str | None = None) -> CharacterAsset:
    a = CharacterAsset(name=name, l1_prompt=f"{name} desc")
    a.portrait_local = portrait
    return a


def test_collect_reference_images_basic():
    eng = ConsistencyEngine()
    chars = {
        "a": _asset("a", "assets/a.png"),
        "b": _asset("b", "assets/b.png"),
    }
    panel = Panel(
        panel_id="p1",
        reference_characters=["a", "b"],
    )
    refs = eng.collect_reference_images(
        panel=panel,
        characters_by_name=chars,
        prev_panel_local="output/prev.png",
    )
    assert refs == ["assets/a.png", "assets/b.png", "output/prev.png"]


def test_collect_reference_images_skips_missing_and_dedups():
    eng = ConsistencyEngine()
    chars = {
        "a": _asset("a", "assets/a.png"),
        "b": _asset("b", None),  # no portrait -> skipped
    }
    panel = Panel(
        panel_id="p1",
        reference_characters=["a", "a"],  # duplicate -> de-duped
    )
    refs = eng.collect_reference_images(
        panel=panel,
        characters_by_name=chars,
        prev_panel_local=None,  # None -> skipped
    )
    assert refs == ["assets/a.png"]


def test_collect_reference_images_accepts_dict_and_caps():
    eng = ConsistencyEngine()
    chars = {f"c{i}": _asset(f"c{i}", f"assets/c{i}.png") for i in range(12)}
    panel = {"reference_characters": [f"c{i}" for i in range(12)]}
    refs = eng.collect_reference_images(
        panel=panel,
        characters_by_name=chars,
        max_refs=9,
    )
    assert len(refs) == 9
    assert refs[0] == "assets/c0.png"


def test_apply_l3_skips_without_cv2():
    try:
        import cv2  # noqa: F401
    except ImportError:
        pass
    else:
        pytest.skip("cv2 installed; degradation covered by no-face test")
    eng = ConsistencyEngine()
    img = Image.new("RGB", (64, 64), (10, 20, 30))
    out = eng.apply_l3(img, img)
    assert out is img


def test_apply_l3_no_face_returns_original():
    pytest.importorskip("cv2")  # skip cleanly where cv2 is absent
    eng = ConsistencyEngine()
    # Solid-color image: Haar cascade detects no face -> graceful skip.
    img = Image.new("RGB", (80, 80), (220, 220, 220))
    out = eng.apply_l3(img, img)
    assert isinstance(out, Image.Image)
    assert out.size == (80, 80) and out.mode == "RGB"
