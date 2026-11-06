"""Preview: ask the vision model to place lettering on all 4 finished pages.

This is a throwaway preview harness (C-plan step 1). It does NOT touch the
generate pipeline, state, or the shipped comic.pdf. It reads the current blank
pages, calls agnes-3.0-flash once per page with the image + text items, and
writes AI-positioned lettering previews plus a JSON record to /tmp.

Run: uv run python scripts/vision_placement_preview.py
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from PIL import Image

from core.comic import fonts as F
from core.comic import vision_placement as vp
from core.comic.layout import LayoutEngine
from core.comic.page_lettering import blend_fill_with_art, text_ink_for_fill
from core.dotenv import load_dotenv
from core.schemas import ComicPagePlanSet

ROOT = Path(__file__).resolve().parent.parent
BLANK_DIR = ROOT / "comic_out" / "pages" / "blank"
PLAN_DIR = ROOT / "comic_out" / "review" / "plans"
OUT_DIR = Path("/tmp/vision_placement_preview")


def _font_path() -> str | None:
    for path in F._CJK_FONT_CANDIDATES:
        if F._try_load(path, F.DEFAULT_CJK_FONT_SIZE) is not None:
            return path
    return None


def _items_for_page(plan) -> list[tuple[str, tuple[str, str]]]:
    """Return [(panel_id, (kind, text))] in plan order."""
    items: list[tuple[str, tuple[str, str]]] = []
    for pn in plan.panels:
        for kind in ("caption", "dialogue", "sfx"):
            text = getattr(pn, kind)
            if text:
                items.append((pn.panel_id, (kind, text)))
    return items


def _page_specs() -> list[tuple[Image.Image, list[tuple[str, tuple[str, str]]], str]]:
    specs = []
    page_no = 0
    for chunk in ("c0000", "c0001"):
        plan_set = ComicPagePlanSet.model_validate(
            json.loads((PLAN_DIR / f"page_plan.{chunk}.json").read_text(encoding="utf-8"))
        )
        for pi, plan in enumerate(plan_set.pages):
            page_no += 1
            blank = Image.open(BLANK_DIR / f"page_{chunk}_p{pi:04d}.png").convert("RGB")
            specs.append((blank, _items_for_page(plan), f"P{page_no}"))
    return specs


async def _place_one(chat, blank, items):
    vp_items = [(kind, text) for _pid, (kind, text) in items]
    req = vp.build_placement_request(blank, vp_items)
    try:
        raw = await chat.chat_function_call(
            req["messages"], req["tools"], req["tool_choice"], max_retries=2
        )
    except Exception as exc:  # noqa: BLE001 — preview must still produce a page
        print(f"    model call failed: {type(exc).__name__}: {exc}")
        raw = {}
    boxes = vp.parse_placements(raw, expected_count=len(items), page_size=blank.size)
    return boxes, raw


def _draw_preview(blank, items, boxes, font_path):
    from PIL import ImageDraw

    from core.comic.vision_placement import fit_boxes_to_text

    img = blank.copy()
    draw = ImageDraw.Draw(img)
    engine = LayoutEngine(font_path=font_path)
    # Keep the model's chosen position, shrink size to the real text length.
    vp_items = [(kind, text) for _pid, (kind, text) in items]
    fitted = fit_boxes_to_text(engine, vp_items, boxes, blank.size)
    for (_pid, (kind, text)), box in zip(items, fitted, strict=True):
        fill = blend_fill_with_art(blank, box)
        ink = text_ink_for_fill(fill)
        if kind == "caption":
            engine._draw_caption(draw, box, text, fill=fill, ink=ink)
        elif kind == "sfx":
            engine._draw_sfx(draw, box, text)
        else:
            engine._draw_bubble(draw, box, text, fill=fill, ink=ink)
    return img, fitted


async def main() -> None:
    load_dotenv()
    from core.api.chat_provider import get_chat_provider

    os.makedirs(OUT_DIR, exist_ok=True)
    chat = get_chat_provider()
    print(f"vision provider: {type(chat).__name__} / {chat.model}")
    font_path = _font_path()
    record: dict[str, object] = {}
    for blank, items, label in _page_specs():
        print(f"{label}: {len(items)} text item(s) -> calling vision model ...")
        boxes, raw = await _place_one(chat, blank, items)
        preview, fitted = _draw_preview(blank, items, boxes, font_path)
        out = OUT_DIR / f"{label}.png"
        preview.save(out)
        record[label] = {
            "items": [{"kind": k, "text": t} for _pid, (k, t) in items],
            "model_boxes_px": boxes,
            "fitted_boxes_px": fitted,
            "raw": raw,
            "preview": str(out),
        }
        print(f"    saved {out}  model={boxes}  fitted={fitted}")
    (OUT_DIR / "record.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"record -> {OUT_DIR / 'record.json'}")


if __name__ == "__main__":
    asyncio.run(main())
