"""Tests for the vision-based lettering placement helpers (preview stage)."""

from __future__ import annotations

from PIL import Image

from core.comic import vision_placement as vp


def _sample_image() -> Image.Image:
    return Image.new("RGB", (832, 1248), (60, 80, 110))


def test_build_request_contains_image_and_text_items():
    """The multimodal request must carry the page image and every text item."""
    req = vp.build_placement_request(
        _sample_image(),
        [("dialogue", "这首歌，倒是比人坦诚。"), ("caption", "江风裹着初秋的凉意")],
    )
    user = next(m for m in req["messages"] if m["role"] == "user")
    content = user["content"]
    assert isinstance(content, list)
    kinds = [part.get("type") for part in content]
    assert "image_url" in kinds, "image must be sent to the vision model"
    assert "text" in kinds
    text_blob = " ".join(str(p.get("text", "")) for p in content if p.get("type") == "text")
    assert "这首歌" in text_blob and "江风裹着" in text_blob
    # tool schema forces a placement function
    assert req["tool_choice"]["function"]["name"] == "place_lettering"


def test_parse_and_validate_clamps_boxes_into_page():
    """Model coordinates are normalized (0..1) and must stay inside the page."""
    raw = {
        "placements": [
            {"text": "a", "x": 0.5, "y": 0.1, "w": 0.3, "h": 0.1, "reason": "near speaker"},
            # out-of-range / negative values must be clamped, not trusted
            {"text": "b", "x": 1.4, "y": -0.2, "w": 0.9, "h": 0.5, "reason": "bad"},
        ]
    }
    out = vp.parse_placements(raw, expected_count=2, page_size=(832, 1248))
    assert len(out) == 2
    for box in out:
        x, y, w, h = box
        assert 0 <= x and 0 <= y
        assert x + w <= 832 and y + h <= 1248
        assert w > 0 and h > 0


def test_parse_placements_pads_missing_entries():
    """A short model reply must not crash; missing boxes get a safe fallback."""
    out = vp.parse_placements(
        {"placements": [{"text": "a", "x": 0.1, "y": 0.1, "w": 0.3, "h": 0.1}]},
        expected_count=3,
        page_size=(832, 1248),
    )
    assert len(out) == 3
    for box in out:
        assert box[2] > 0 and box[3] > 0


def test_fit_boxes_to_text_shrinks_oversized_model_boxes_keeping_position():
    """Model w/h are only a loose hint; real size comes from the text.

    The vision model returned huge empty boxes (P4 had a 266x162 block for a
    short line). fit_boxes_to_text must shrink each box to the glyph size using
    the same fit_lettering_box the pipeline trusts, while keeping the model's
    top-left anchor (so the AI-chosen location survives).
    """
    from core.comic.layout import LayoutEngine

    engine = LayoutEngine()
    page = (832, 1248)
    oversized = [(49, 49, 266, 162)]  # model's huge box for one short caption
    fitted = vp.fit_boxes_to_text(
        engine,
        [("dialogue", "这首歌，倒是比人坦诚。")],
        oversized,
        page,
    )
    assert len(fitted) == 1
    bx, by, bw, bh = fitted[0]
    # Position kept (near the model's top-left), but size shrunk a lot.
    assert abs(bx - 49) < 40 and abs(by - 49) < 40
    assert bw < 266 * 0.75 and bh < 162 * 0.6, "box was not shrunk to text size"
    assert bw > 0 and bh > 0
    assert bx + bw <= 832 and by + bh <= 1248
