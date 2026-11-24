"""Layout diversity helpers for finished-page anti-template planning."""

from __future__ import annotations

from collections.abc import Iterable

LAYOUT_CATALOG: frozenset[str] = frozenset(
    {
        "splash_action",
        "dialogue_grid",
        "inset_memory",
        "widescreen_scene",
        "diagonal_motion",
        "crowd_establishing",
        "object_closeup",
        "over_shoulder",
        "split_timeline",
        "environmental_wide",
    }
)

POSITIVE_LAYOUT_VARIETY_LINE = (
    "environmental staging, action blocking, and varied shots carry the page "
    "composition, with the declared splash_action layout honored when selected"
)
ANTI_CENTER_STANDEE_LINE = POSITIVE_LAYOUT_VARIETY_LINE


def normalize_layout_intent(intent: str) -> str:
    """Return the catalog token if intent starts with/contains one; else stripped text."""
    text = (intent or "").strip()
    if not text:
        return ""
    lower = text.casefold()
    for token in sorted(LAYOUT_CATALOG, key=len, reverse=True):
        if lower == token or lower.startswith(token + " ") or lower.startswith(token + ":"):
            return token
        if token in lower.split()[0:1]:
            return token
    # allow "splash_action — fight in the rain"
    first = lower.replace("—", " ").replace("-", " ").split()[0]
    if first in LAYOUT_CATALOG:
        return first
    return text


def consecutive_layout_streak(intents: Iterable[str]) -> int:
    """Length of the trailing run of identical normalized layout intents."""
    normalized = [normalize_layout_intent(i) for i in intents if normalize_layout_intent(i)]
    if not normalized:
        return 0
    last = normalized[-1]
    streak = 0
    for intent in reversed(normalized):
        if intent == last:
            streak += 1
        else:
            break
    return streak


def summarize_recent_layouts(intents: Iterable[str], *, limit: int = 5) -> str:
    """Human-readable recent layout_intent list for planner context."""
    items = [normalize_layout_intent(i) or (i or "").strip() for i in intents]
    items = [i for i in items if i][-limit:]
    if not items:
        return "(none)"
    return ", ".join(items)


def layout_diversity_instructions(recent_layouts: list[str] | None) -> str:
    """Instructions block injected into plan_comic_pages user message."""
    catalog = ", ".join(sorted(LAYOUT_CATALOG))
    recent = summarize_recent_layouts(recent_layouts or [])
    return (
        "Layout diversity rules:\n"
        f"- Prefer layout_intent tokens from this catalog (then free detail): {catalog}.\n"
        f"- Recent layout_intent values in this project/chunk: {recent}.\n"
        "- Choose a fresh layout_intent after the immediately previous page.\n"
        "- Favor environmental staging, action blocking, and varied shots across "
        "consecutive pages.\n"
    )
