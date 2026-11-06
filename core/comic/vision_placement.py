"""core.comic.vision_placement — multimodal lettering placement (preview stage).

Asks a vision-capable chat model to look at one finished (blank) comic page and
return where each piece of lettering should sit, following manga convention:
dialogue near its speaker, narration / sfx in empty space, never covering faces
or main subjects. Coordinates are normalized (0..1) and validated before use.

This module is deliberately standalone: the preview script and (once approved)
the real pipeline both call the same builders, so a prompt edit changes the
request identity everywhere. Pure request construction / parsing — the actual
model call lives in the caller so it stays easy to cache.
"""

from __future__ import annotations

import base64
import io
from typing import Any

from PIL import Image

# Prompt version participates in the request identity (cache) once wired in.
PLACEMENT_PROMPT_VERSION = "place_v2"

PLACEMENT_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "place_lettering",
        "description": (
            "Return one placement box per text item for this comic page, following "
            "manga lettering convention."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "placements": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string"},
                            "x": {"type": "number", "description": "left, 0..1 of width"},
                            "y": {"type": "number", "description": "top, 0..1 of height"},
                            "w": {"type": "number", "description": "width, 0..1"},
                            "h": {"type": "number", "description": "height, 0..1"},
                            "reason": {"type": "string"},
                        },
                        "required": ["text", "x", "y", "w", "h"],
                    },
                }
            },
            "required": ["placements"],
        },
    },
}

PLACEMENT_TOOL_CHOICE = {"type": "function", "function": {"name": "place_lettering"}}

SYSTEM_PROMPT = (
    "You are a professional manga lettering artist. You will be shown a finished "
    "comic page image and a list of text pieces (dialogue / caption / sfx) that "
    "must be placed on it. Decide the best position for each, following manga "
    "convention. CRITICAL for dialogue: place the speech bubble IMMEDIATELY next "
    "to the speaking character's head (a short gap above or beside the head, on "
    "an empty side) — do NOT put it in a far-away corner of the page. Identify "
    "who is speaking and mention them in the reason. Put narration captions and "
    "sfx in calm empty space near the relevant subject (e.g. an sfx about a "
    "lantern goes beside that lantern), and NEVER cover a character's face, "
    "body, or an important subject. Give x,y of the box top-left corner in "
    "normalized 0..1 coordinates; the box only needs to be big enough for the "
    "text, so keep w/h modest and close to the text length. Keep boxes fully "
    "inside the page with a small margin. Return normalized coordinates (0..1) "
    "via the place_lettering function, one box per text item, in order."
)


def _image_data_url(image: Image.Image) -> str:
    buf = io.BytesIO()
    image.convert("RGB").save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return "data:image/png;base64," + b64


def _items_text(items: list[tuple[str, str]]) -> str:
    lines = []
    for i, (kind, text) in enumerate(items, 1):
        lines.append(f"{i}. [{kind}] {text}")
    return "\n".join(lines)


def build_placement_request(
    image: Image.Image,
    items: list[tuple[str, str]],
) -> dict[str, Any]:
    """Build an OpenAI-style multimodal chat request (image + text + tool).

    Reuses the project's forced function-call shape so the existing
    ``ChatProvider.chat_function_call`` can send it as-is.
    """
    text_part = (
        "Place these lettering items on the page image. Return exactly "
        f"{len(items)} placement boxes, in this order.\n\n{_items_text(items)}"
    )
    user_content: list[dict[str, Any]] = [
        {"type": "text", "text": text_part},
        {"type": "image_url", "image_url": {"url": _image_data_url(image)}},
    ]
    return {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "tools": [PLACEMENT_TOOL],
        "tool_choice": PLACEMENT_TOOL_CHOICE,
    }


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def parse_placements(
    raw: dict[str, Any],
    *,
    expected_count: int,
    page_size: tuple[int, int],
) -> list[tuple[int, int, int, int]]:
    """Validate model output into in-page pixel boxes; pad with safe fallbacks.

    The model may return out-of-range numbers or fewer boxes than requested.
    Never trust it: clamp every coordinate into the page with a margin, and pad
    missing entries with a top-edge box so lettering never crashes.
    """
    pw, ph = page_size
    mx, my = int(pw * 0.04), int(ph * 0.04)
    entries = list((raw or {}).get("placements") or [])
    boxes: list[tuple[int, int, int, int]] = []

    def fallback(index: int) -> tuple[int, int, int, int]:
        bw = min(int(pw * 0.34), pw - 2 * mx)
        bh = min(int(ph * 0.06), ph - 2 * my)
        x = mx
        y = min(my + index * (bh + 8), ph - my - bh)
        return (x, y, bw, bh)

    for i in range(expected_count):
        if i >= len(entries) or not isinstance(entries[i], dict):
            boxes.append(fallback(i))
            continue
        e = entries[i]
        try:
            x = _clamp(float(e["x"]), 0.0, 1.0) * pw
            y = _clamp(float(e["y"]), 0.0, 1.0) * ph
            w = _clamp(float(e["w"]), 0.08, 0.6) * pw
            h = _clamp(float(e["h"]), 0.04, 0.2) * ph
        except (KeyError, TypeError, ValueError):
            boxes.append(fallback(i))
            continue
        # keep the box fully inside the page with a margin
        bw, bh = int(w), int(h)
        x = int(_clamp(x, mx, pw - mx - bw))
        y = int(_clamp(y, my, ph - my - bh))
        bw = min(bw, pw - mx - x)
        bh = min(bh, ph - my - y)
        boxes.append((x, y, max(1, bw), max(1, bh)))
    return boxes


def fit_boxes_to_text(
    engine,
    items: list[tuple[str, str]],
    model_boxes: list[tuple[int, int, int, int]],
    page_size: tuple[int, int],
) -> list[tuple[int, int, int, int]]:
    """Keep the model's chosen position, but recompute size from the real text.

    The vision model understands *where* a line belongs (near the speaker, in
    calm sky) but its w/h are only a loose hint and tend to be far too large,
    leaving a big empty box around one short line. We treat each model box as an
    anchor and let the pipeline's own fit_lettering_box shrink chrome to glyph
    size, preserving the top-left position the AI picked.
    """
    from core.comic.page_lettering import fit_lettering_box

    out: list[tuple[int, int, int, int]] = []
    for (kind, text), box in zip(items, model_boxes, strict=True):
        out.append(fit_lettering_box(engine, kind, text, box, page_size))
    return out
