"""Detect source lettering script and validate ComicPagePlan lettering fields."""

from __future__ import annotations

import re
from typing import Literal

from core.comic.fonts import text_requires_cjk
from core.schemas import ComicPagePlan, PagePanelSpec

Script = Literal["cjk", "latin", "mixed", "unknown"]

_LETTER_RE = re.compile(r"[A-Za-z\u00C0-\u024F\u4E00-\u9FFF\u3040-\u30FF\uAC00-\uD7AF]")


def source_lettering_script(text: str) -> Script:
    letters = _LETTER_RE.findall(text or "")
    if not letters:
        return "unknown"
    cjk = sum(1 for ch in letters if text_requires_cjk(ch))
    latin = len(letters) - cjk
    if cjk and not latin:
        return "cjk"
    if latin and not cjk:
        return "latin"
    ratio = cjk / len(letters)
    if ratio >= 0.15:
        return "cjk"
    if ratio <= 0.05:
        return "latin"
    return "mixed"


def _field_mismatch(text: str | None, script: Script) -> bool:
    if not text or not _LETTER_RE.search(text):
        return False
    has_cjk = text_requires_cjk(text)
    if script == "cjk":
        return not has_cjk
    if script == "latin":
        return has_cjk and sum(1 for ch in text if text_requires_cjk(ch)) >= max(
            1, len(_LETTER_RE.findall(text)) // 2
        )
    return False


def lettering_field_mismatches(
    plan: ComicPagePlan, script: Script
) -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []
    if script not in ("cjk", "latin"):
        return out
    for panel in plan.panels:
        for kind in ("caption", "dialogue", "sfx"):
            val = getattr(panel, kind)
            if _field_mismatch(val, script):
                out.append((panel.panel_id, kind, val or ""))
    return out


def strip_mismatched_lettering(plan: ComicPagePlan, script: Script) -> ComicPagePlan:
    bad = {(p, k) for p, k, _ in lettering_field_mismatches(plan, script)}
    panels: list[PagePanelSpec] = []
    for panel in plan.panels:
        data = panel.model_dump()
        for kind in ("caption", "dialogue", "sfx"):
            if (panel.panel_id, kind) in bad:
                data[kind] = None
        panels.append(PagePanelSpec.model_validate(data))
    boxes = [
        b
        for b in plan.lettering_boxes
        if (b.panel_id, b.kind) not in bad
    ]
    return plan.model_copy(update={"panels": panels, "lettering_boxes": boxes})
