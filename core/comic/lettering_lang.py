"""Detect source lettering script and validate ComicPagePlan lettering fields."""

from __future__ import annotations

import re
from typing import Literal

from core.comic.fonts import text_requires_cjk
from core.schemas import ComicPagePlan, PagePanelSpec

Script = Literal["cjk", "latin", "mixed", "unknown"]

_LETTER_RE = re.compile(r"[A-Za-z\u00C0-\u024F\u4E00-\u9FFF\u3040-\u30FF\uAC00-\uD7AF]")

# Parenthetical glosses that are mostly latin / pinyin (incl. tone marks + curly quotes).
_PINYIN_PAREN_RE = re.compile(
    r"[（(][^）)]*[A-Za-zĀÁǍÀĒÉĚÈĪÍǏÌŌÓǑÒŪÚǓÙÜǖǘǚǜāáǎàēéěèīíǐìōóǒòūúǔùüǖǘǚǜ][^）)]*[）)]"
)

_MAX_CAPTION_CHARS = 48
_MAX_DIALOGUE_CHARS = 36
_MAX_SFX_CHARS = 16


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


def strip_pinyin_glosses(text: str) -> str:
    """Remove parenthetical pinyin / latin pronunciation glosses."""
    cleaned = _PINYIN_PAREN_RE.sub("", text or "")
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip(" \t,;；、")


def truncate_lettering(text: str, *, kind: str) -> str:
    """Keep lettering short enough for overlay chrome."""
    limits = {
        "caption": _MAX_CAPTION_CHARS,
        "dialogue": _MAX_DIALOGUE_CHARS,
        "sfx": _MAX_SFX_CHARS,
    }
    limit = limits.get(kind, _MAX_DIALOGUE_CHARS)
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip("，,、;； ") + "…"


def sanitize_lettering_text(text: str | None, *, kind: str, script: Script) -> str | None:
    """Clean one lettering field for overlay / storage."""
    if not text or not str(text).strip():
        return None
    cleaned = str(text).strip()
    if script == "cjk":
        cleaned = strip_pinyin_glosses(cleaned)
        # Drop leftover latin-only tails after CJK (e.g. broken gloss remnants).
        if text_requires_cjk(cleaned):
            cleaned = re.sub(r"[A-Za-zĀ-žā-ž][A-Za-zĀ-žā-ž\s,.\-']{2,}$", "", cleaned).strip()
    cleaned = truncate_lettering(cleaned, kind=kind)
    return cleaned or None


def sanitize_plan_lettering(plan: ComicPagePlan, script: Script) -> ComicPagePlan:
    """Sanitize all caption/dialogue/sfx on a page plan."""
    panels: list[PagePanelSpec] = []
    for panel in plan.panels:
        data = panel.model_dump()
        for kind in ("caption", "dialogue", "sfx"):
            data[kind] = sanitize_lettering_text(getattr(panel, kind), kind=kind, script=script)
        panels.append(PagePanelSpec.model_validate(data))
    kept_kinds = {
        (p.panel_id, kind)
        for p in panels
        for kind in ("caption", "dialogue", "sfx")
        if getattr(p, kind)
    }
    boxes = [b for b in plan.lettering_boxes if (b.panel_id, b.kind) in kept_kinds]
    return plan.model_copy(update={"panels": panels, "lettering_boxes": boxes})


def _field_mismatch(text: str | None, script: Script) -> bool:
    if not text or not _LETTER_RE.search(text):
        return False
    has_cjk = text_requires_cjk(text)
    if script == "cjk":
        # Pure latin, or mostly latin gloss without enough CJK, is a mismatch.
        if not has_cjk:
            return True
        # Chinese + heavy pinyin still counts as polluted for mismatch retry.
        latin = sum(
            1
            for ch in text
            if ("A" <= ch <= "Z") or ("a" <= ch <= "z") or ("\u00c0" <= ch <= "\u024f")
        )
        cjk = sum(1 for ch in text if text_requires_cjk(ch))
        return latin >= max(8, cjk // 2) and _PINYIN_PAREN_RE.search(text) is not None
    if script == "latin":
        return has_cjk and sum(1 for ch in text if text_requires_cjk(ch)) >= max(
            1, len(_LETTER_RE.findall(text)) // 2
        )
    return False


def lettering_field_mismatches(plan: ComicPagePlan, script: Script) -> list[tuple[str, str, str]]:
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
    boxes = [b for b in plan.lettering_boxes if (b.panel_id, b.kind) not in bad]
    return plan.model_copy(update={"panels": panels, "lettering_boxes": boxes})
