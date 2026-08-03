"""Voice and timeline helpers for finished-page lettering attribution."""

from __future__ import annotations

import re

from core.comic.visual_bible import parse_stage_ref, resolve_canonical_name
from core.schemas import ComicPagePlan, PagePanelSpec, VisualBible

_FIRST_PERSON_LETTER_RE = re.compile(
    r"(我的儿子|我从来|请你相信我|你从来也没有认识过我|我爱你|昨天死了|"
    r"\bmy son\b|\bi love you\b|you never knew me)",
    re.IGNORECASE,
)

TIMELINE_PRESENT_LINE = (
    "Timeline=present: cool static reading-frame atmosphere; calm interior; "
    "focus on the letter-reader now."
)
TIMELINE_PAST_LINE = (
    "Timeline=past: warmer memory staging with slight film grain; depict lived scenes "
    "in environment — do not default to letter-holding unless action requires it."
)
TIMELINE_LIMINAL_LINE = (
    "Timeline=liminal: bridge present and memory; keep visual transition cues clear."
)


def letter_role_names(bible: VisualBible | None) -> tuple[set[str], set[str]]:
    """Return (reader_names, writer_names) including aliases and stage refs."""
    readers: set[str] = set()
    writers: set[str] = set()
    if bible is None:
        return readers, writers
    for key, canon in bible.characters.items():
        names = {key, canon.canonical_name, *canon.aliases}
        for stage in canon.stages:
            if stage.portrait_key:
                names.add(stage.portrait_key)
            names.add(f"{canon.canonical_name}@{stage.stage}")
        fn = (canon.narrative_function or "").strip()
        if fn == "letter_reader":
            readers |= {n for n in names if n}
        elif fn == "letter_writer":
            writers |= {n for n in names if n}
    return readers, writers


def _base_names(names: list[str], bible: VisualBible | None) -> set[str]:
    out: set[str] = set()
    for name in names:
        base, _ = parse_stage_ref(name)
        if bible is not None:
            base = resolve_canonical_name(base, bible)
        if base:
            out.add(base)
            out.add(name)
    return out


def looks_like_letter_narration(text: str | None) -> bool:
    """Heuristic: first-person confession / letter voice."""
    if not text:
        return False
    return bool(_FIRST_PERSON_LETTER_RE.search(text))


def sanitize_panel_voice(
    panel: PagePanelSpec,
    bible: VisualBible | None,
    *,
    page_timeline: str = "",
) -> PagePanelSpec:
    """Move mis-attributed letter narration from dialogue into caption."""
    updated = panel.model_copy(deep=True)
    if not updated.timeline and page_timeline:
        updated.timeline = page_timeline  # type: ignore[assignment]
    readers, writers = letter_role_names(bible)
    on_panel = _base_names(list(updated.characters), bible)
    reader_only = bool(on_panel) and on_panel.issubset(readers) and not (on_panel & writers)
    dialogue = updated.dialogue
    if dialogue and looks_like_letter_narration(dialogue) and reader_only:
        # Letter-writer I-voice must not be the reader's spoken bubble.
        caption = (updated.caption or "").strip()
        moved = dialogue.strip()
        updated.caption = f"{caption}\n{moved}".strip() if caption else moved
        updated.dialogue = None
        updated.speaker = ""
        # Drop dialogue lettering boxes for this panel; caption boxes kept/added by planner.
        return updated
    speaker = (updated.speaker or "").strip()
    if speaker and bible is not None:
        base, _ = parse_stage_ref(speaker)
        updated.speaker = resolve_canonical_name(base, bible)
    return updated


def sanitize_plan_voice(plan: ComicPagePlan, bible: VisualBible | None) -> ComicPagePlan:
    """Sanitize every panel's voice attribution on a page plan."""
    updated = plan.model_copy(deep=True)
    page_tl = (updated.timeline or "").strip()
    new_panels: list[PagePanelSpec] = []
    dialogue_panels_cleared: set[str] = set()
    for panel in updated.panels:
        before = panel.dialogue
        fixed = sanitize_panel_voice(panel, bible, page_timeline=page_tl)
        if before and not fixed.dialogue:
            dialogue_panels_cleared.add(panel.panel_id)
        new_panels.append(fixed)
    updated.panels = new_panels
    if dialogue_panels_cleared:
        updated.lettering_boxes = [
            box
            for box in updated.lettering_boxes
            if not (box.kind == "dialogue" and box.panel_id in dialogue_panels_cleared)
        ]
    return updated


def timeline_prompt_lines(timeline: str) -> list[str]:
    """Visual grammar lines for a timeline value."""
    value = (timeline or "").strip().casefold()
    if value == "present":
        return [TIMELINE_PRESENT_LINE]
    if value == "past":
        return [TIMELINE_PAST_LINE]
    if value == "liminal":
        return [TIMELINE_LIMINAL_LINE]
    return []


def voice_timeline_plan_instructions(bible: VisualBible | None) -> str:
    """Extra planner instructions for speaker + timeline fields."""
    readers, writers = letter_role_names(bible)
    reader_s = ", ".join(sorted(readers)[:6]) or "(none marked)"
    writer_s = ", ".join(sorted(writers)[:6]) or "(none marked)"
    return (
        "Voice and timeline rules:\n"
        "- Set page timeline to present | past | liminal when clear "
        "(present = reading-the-letter frame; past = remembered life events).\n"
        "- Panels may override timeline.\n"
        "- Narration / letter first-person / time-place → caption with empty speaker.\n"
        "- Spoken lines → dialogue with speaker = speaking character name.\n"
        f"- letter_reader canons: {reader_s}. letter_writer canons: {writer_s}.\n"
        "- Never put letter_writer first-person confession into letter_reader dialogue bubbles.\n"
    )
