"""core.comic.vision_layout — cached orchestration for multimodal placement.

Bridges the async pipeline and the pure request/parse helpers in
``vision_placement``: look at one finished (blank) page, return one tightened
pixel box per lettering job, and cache the result by (image, texts, model,
prompt). Any failure returns ``None`` so callers fall back to deterministic
placement. The order matches ``resolve_lettering_jobs`` exactly.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from PIL import Image

from core.comic import vision_placement as vp
from core.comic.layout import LayoutEngine
from core.comic.page_lettering import resolve_lettering_jobs
from core.schemas import ComicPagePlan

# Re-exported for callers / cache identity.
MODEL = "agnes-3.0-flash"
PLACEMENT_PROMPT_VERSION = vp.PLACEMENT_PROMPT_VERSION


def _blank_bytes(image: Image.Image) -> bytes:
    """Stable bytes for the blank page (PNG, RGB) used in the cache key."""
    import io

    buf = io.BytesIO()
    image.convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


def placement_cache_key(
    blank_bytes: bytes,
    items: list[tuple[str, str]],
    model: str,
    prompt_version: str,
) -> str:
    """sha256 over everything that can change the model's placement decision."""
    payload = json.dumps(
        {
            "blank": hashlib.sha256(blank_bytes).hexdigest(),
            "items": items,
            "model": model,
            "prompt_version": prompt_version,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _get_chat():
    """Resolve the configured chat provider (indirection for tests / fallback)."""
    from core.api.chat_provider import get_chat_provider

    return get_chat_provider()


async def vision_placement_boxes(
    blank: Image.Image,
    plan: ComicPagePlan,
    source_text: str,
    cache_dir: Path,
    *,
    font_path: str | None = None,
) -> list[tuple[int, int, int, int]] | None:
    """Return tightened pixel boxes from the vision model, or None to fall back.

    Cache hit avoids a model call; a model error / bad credentials / malformed
    reply all become None so the deterministic placer still produces a page.
    """
    try:
        jobs = resolve_lettering_jobs(plan, source_text=source_text)
        items = [(kind, text) for _pid, kind, text, _frac in jobs]
        if not items:
            return []
        cache_dir = Path(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        key = placement_cache_key(_blank_bytes(blank), items, MODEL, PLACEMENT_PROMPT_VERSION)
        cache_file = cache_dir / f"{key}.json"
        if cache_file.is_file():
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            boxes = [tuple(b) for b in data.get("boxes", [])]
            if len(boxes) == len(items):
                return [(int(x), int(y), int(w), int(h)) for x, y, w, h in boxes]

        req = vp.build_placement_request(blank, items)
        chat = _get_chat()
        raw = await chat.chat_function_call(
            req["messages"], req["tools"], req["tool_choice"], max_retries=2
        )
        model_boxes = vp.parse_placements(raw, expected_count=len(items), page_size=blank.size)
        engine = LayoutEngine(font_path=font_path)
        boxes = vp.fit_boxes_to_text(engine, items, model_boxes, blank.size)
        cache_file.write_text(json.dumps({"boxes": [list(b) for b in boxes]}), encoding="utf-8")
        return boxes
    except Exception:  # noqa: BLE001 — any vision failure must fall back, not crash
        return None
