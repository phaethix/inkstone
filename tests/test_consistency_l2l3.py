"""tests/test_consistency_l2l3.py — reference collection + face fallback (no GPU).

- ``collect_reference_images`` is pure logic (no network/images).
- ``apply_l3`` degrades gracefully: it returns the panel unchanged when
  ``cv2`` is missing or no face is detected. cv2-dependent assertions are
  skipped where cv2 is not installed, so CI stays green without opencv.
"""

from pathlib import Path

import pytest
from PIL import Image

from core.comic.consistency import ConsistencyEngine, _panel_reference_names
from core.schemas import CharacterAsset, Panel


def _asset(name: str, portrait: str | None = None) -> CharacterAsset:
    a = CharacterAsset(name=name, l1_prompt=f"{name} desc")
    a.portrait_local = portrait
    return a


def test_panel_reference_names_unions_both_fields():
    """Models often fill only one field; L1/L2 must see the same union."""
    assert _panel_reference_names(
        Panel(panel_id="p1", reference_characters=["a", "b"], characters_present=[])
    ) == ["a", "b"]
    assert _panel_reference_names(
        Panel(panel_id="p2", reference_characters=[], characters_present=["b"])
    ) == ["b"]
    assert _panel_reference_names(
        Panel(panel_id="p3", reference_characters=["b"], characters_present=["a", "b"])
    ) == ["b", "a"]


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


def test_collect_reference_images_falls_back_to_characters_present():
    eng = ConsistencyEngine()
    chars = {"a": _asset("a", "assets/a.png")}
    panel = Panel(panel_id="p1", characters_present=["a"], reference_characters=[])
    refs = eng.collect_reference_images(panel=panel, characters_by_name=chars)
    assert refs == ["assets/a.png"]


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
    eng = ConsistencyEngine(enable_l3=True)
    img = Image.new("RGB", (64, 64), (10, 20, 30))
    out = eng.apply_l3(img, img)
    assert out is img


def test_apply_l3_no_face_returns_original():
    pytest.importorskip("cv2")  # skip cleanly where cv2 is absent
    eng = ConsistencyEngine(enable_l3=True)
    # Solid-color image: Haar cascade detects no face -> graceful skip.
    img = Image.new("RGB", (80, 80), (220, 220, 220))
    out = eng.apply_l3(img, img)
    assert isinstance(out, Image.Image)
    assert out.size == (80, 80) and out.mode == "RGB"


def _astronaut_fixture() -> "Path":
    return Path(__file__).parent / "fixtures" / "astronaut.png"


def test_apply_l3_composites_real_face():
    pytest.importorskip("cv2")  # positive path requires OpenCV
    fixture = _astronaut_fixture()
    if not fixture.exists():
        pytest.skip("astronaut fixture missing")
    eng = ConsistencyEngine(enable_l3=True)
    img = Image.open(fixture).convert("RGB")
    # Same image as panel and portrait -> Haar finds a face in both and the
    # face-ratio guard passes, so compositing runs and returns a NEW image.
    out = eng.apply_l3(img, img)
    assert isinstance(out, Image.Image)
    assert out.size == img.size
    assert out.mode == img.mode
    assert out is not img  # composite path produced a new object


def test_apply_l3_no_face_returns_same_object():
    pytest.importorskip("cv2")
    eng = ConsistencyEngine(enable_l3=True)
    # No face -> graceful skip, original object returned unchanged.
    img = Image.new("RGB", (80, 80), (220, 220, 220))
    out = eng.apply_l3(img, img)
    assert out is img


def test_apply_l3_disabled_via_env(monkeypatch):
    pytest.importorskip("cv2")
    monkeypatch.setenv("INKSTONE_L3", "0")
    fixture = _astronaut_fixture()
    if not fixture.exists():
        pytest.skip("astronaut fixture missing")
    eng = ConsistencyEngine()  # reads INKSTONE_L3 at construction
    img = Image.open(fixture).convert("RGB")
    out = eng.apply_l3(img, img)
    assert out is img  # disabled -> no compositing, original returned


def test_apply_l3_skips_far_shot_small_face():
    pytest.importorskip("cv2")
    fixture = _astronaut_fixture()
    if not fixture.exists():
        pytest.skip("astronaut fixture missing")
    eng = ConsistencyEngine(enable_l3=True)
    # Downscale so the detected face (~53px) falls below MIN_PANEL_FACE_PX (80):
    # a far/wide shot where a swap would seam -> graceful skip, original returned.
    small = Image.open(fixture).convert("RGB").resize((256, 256))
    out = eng.apply_l3(small, small)
    assert out is small
