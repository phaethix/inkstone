"""core.comic.adaptation — per-chapter plans copied from key beats already extracted.

No new model call. A beat the extractor marked must-draw is required; the
others are optional. Each beat after the first depends on the previous beat
in that same unit. A chunk that sits in more than one unit is left unassigned.
"""

from core.schemas import AdaptationPlan, KeyBeat, NarrativeBeat, ProjectState, SourceUnit


def adaptation_plan(unit_id: str, beats: list[KeyBeat]) -> AdaptationPlan:
    """Copy key beats into one unit, chaining each beat to the one before it."""
    recorded: list[NarrativeBeat] = []
    previous = ""
    for beat in beats:
        recorded.append(
            NarrativeBeat(
                beat_id=beat.beat_id,
                unit_id=unit_id,
                summary=beat.summary,
                status="required" if beat.must_draw else "optional",
                depends_on=[previous] if previous else [],
            )
        )
        previous = beat.beat_id
    return AdaptationPlan(unit_id=unit_id, beats=recorded)


def _lines(text: str) -> list[str]:
    return [line.strip() for line in text.split("\n") if line.strip()]


def _slice_of(haystack: list[str], needle: list[str]) -> bool:
    if not needle or len(needle) > len(haystack):
        return False
    width = len(needle)
    return any(
        haystack[index : index + width] == needle
        for index in range(len(haystack) - width + 1)
    )


def _suffix_len(unit_lines: list[str], chunk_lines: list[str]) -> int:
    for length in range(len(chunk_lines), 0, -1):
        if _slice_of(unit_lines, chunk_lines[-length:]):
            return length
    return 0


def owning_unit(units: list[SourceUnit], chunk: str) -> str | None:
    """Return the one unit this chunk belongs to.

    Blank lines are ignored, so a pipeline chunk that repeats a heading with an
    extra blank line still matches. A chunk that contains two whole units is
    left unassigned. A chunk that only crosses a boundary belongs to the unit
    that owns its suffix.
    """
    chunk_lines = _lines(chunk)
    if not chunk_lines or not units:
        return None
    containing = [unit for unit in units if _slice_of(_lines(unit.text), chunk_lines)]
    if len(containing) == 1:
        return containing[0].unit_id
    if len(containing) > 1:
        return None
    covered = [
        unit for unit in units if _lines(unit.text) and _slice_of(chunk_lines, _lines(unit.text))
    ]
    if len(covered) == 1:
        return covered[0].unit_id
    if len(covered) > 1:
        return None
    ranked: list[tuple[int, SourceUnit]] = []
    for unit in units:
        length = _suffix_len(_lines(unit.text), chunk_lines)
        if length:
            ranked.append((length, unit))
    if not ranked:
        return None
    best = max(length for length, _unit in ranked)
    winners = [unit for length, unit in ranked if length == best]
    if len(winners) != 1 or best == len(chunk_lines):
        return None
    prefix = chunk_lines[: len(chunk_lines) - best]
    prefix_owned = any(
        unit.unit_id != winners[0].unit_id and _suffix_len(_lines(unit.text), prefix) == len(prefix)
        for unit in units
    )
    if not prefix_owned:
        return None
    return winners[0].unit_id


def sync_adaptation_plans(state: ProjectState, chunks: list[str]) -> None:
    """Ensure every source unit has a plan, and fill it from a cached key-beat set."""
    for unit in state.source_units:
        state.adaptation_plans.setdefault(unit.unit_id, AdaptationPlan(unit_id=unit.unit_id))
    for key, beat_set in state.beat_cache.items():
        if beat_set is None or not str(key).isdigit():
            continue
        index = int(key)
        if index >= len(chunks) or not beat_set.beats:
            continue
        unit_id = owning_unit(state.source_units, chunks[index])
        if unit_id is None:
            continue
        current = state.adaptation_plans.get(unit_id)
        if current is not None and current.beats:
            continue
        state.adaptation_plans[unit_id] = adaptation_plan(unit_id, beat_set.beats)
