"""Key-beat coverage helpers for finished-page planning."""

from __future__ import annotations

from core.schemas import ComicPagePlan, ComicPagePlanSet, KeyBeat, KeyBeatSet


def uncovered_must_draw_beats(beats: KeyBeatSet, pageset: ComicPagePlanSet) -> list[KeyBeat]:
    """Return must_draw beats not listed in any page's covers_beats."""
    covered: set[str] = set()
    for page in pageset.pages:
        for beat_id in page.covers_beats or []:
            if beat_id:
                covered.add(beat_id)
    return [
        beat
        for beat in beats.beats
        if beat.must_draw and beat.beat_id and beat.beat_id not in covered
    ]


def beat_coverage_retry_note(uncovered: list[KeyBeat]) -> str:
    """User-message appendix asking the planner to stage uncovered beats."""
    if not uncovered:
        return ""
    lines = [
        "CRITICAL: these must_draw beats were not covered. Stage each as drawable "
        "panels/pages (physical action + environment), not caption-only, and set "
        "covers_beats on the covering page:"
    ]
    for beat in uncovered[:12]:
        lines.append(
            f"- {beat.beat_id}: {beat.summary}"
            + (f" (chars: {', '.join(beat.characters)})" if beat.characters else "")
        )
    return "\n".join(lines)


def covers_beats_prompt_line(plan: ComicPagePlan) -> str | None:
    """Prompt line requiring physical staging for covered beats."""
    ids = [b for b in (plan.covers_beats or []) if b]
    if not ids:
        return None
    return (
        "This page covers key beats: "
        + ", ".join(ids)
        + ". Depict them as physical staged scenes (body + environment), "
        "not as a character merely standing and holding a letter."
    )
