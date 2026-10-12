"""core.comic.structure_report — five checks over records a project already stores.

No model call. A check with nothing to read is ``absent``, not a pass. The
report is not a readability or quality gate.
"""

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from core.schemas import ProjectState


@dataclass
class StructureCheck:
    name: str
    status: str
    detail: str


@dataclass
class StructureReport:
    checks: list[StructureCheck]

    @property
    def ok(self) -> bool:
        return all(check.status != "fail" for check in self.checks)

    def to_json(self) -> dict:
        payload = asdict(self)
        payload["ok"] = self.ok
        return payload


def _normalize(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _check(name: str, status: str, detail: str) -> StructureCheck:
    return StructureCheck(name=name, status=status, detail=detail)


def _source_check(state: ProjectState, source: str) -> StructureCheck:
    units = state.source_units
    if not units:
        return _check("source", "absent", "no source units")
    canonical = _normalize(source)
    if "".join(unit.text for unit in units) != canonical:
        return _check("source", "fail", "units do not concatenate to the source")
    cursor = 0
    for unit in units:
        if unit.start != cursor or canonical[unit.start : unit.end] != unit.text:
            return _check("source", "fail", f"{unit.unit_id} is not a slice of the source")
        cursor = unit.end
    if cursor != len(canonical):
        return _check("source", "fail", "units do not cover the source")
    return _check("source", "pass", "units partition the source")


def _required_beat_ids(state: ProjectState) -> list[str]:
    found: list[str] = []
    for plan in state.adaptation_plans.values():
        for beat in plan.beats:
            if beat.status == "required" and beat.beat_id and beat.beat_id not in found:
                found.append(beat.beat_id)
    if found:
        return found
    for beat_set in state.beat_cache.values():
        if beat_set is None:
            continue
        for beat in beat_set.beats:
            if beat.must_draw and beat.beat_id and beat.beat_id not in found:
                found.append(beat.beat_id)
    return found


def _panel_beat_ids(state: ProjectState) -> set[str]:
    found: set[str] = set()
    for cache in state.chunk_cache.values():
        board = cache.storyboard
        if board is None:
            continue
        for panel in board.panels:
            found.update(beat_id for beat_id in panel.beat_ids if beat_id)
    for pageset in state.page_cache.values():
        for page in pageset.pages:
            for panel in page.panels:
                found.update(beat_id for beat_id in panel.beat_ids if beat_id)
    return found


def _beats_check(state: ProjectState) -> StructureCheck:
    required = _required_beat_ids(state)
    if not required:
        return _check("beats", "absent", "no required beats")
    named = _panel_beat_ids(state)
    missing = [beat_id for beat_id in required if beat_id not in named]
    if missing:
        return _check("beats", "fail", "missing " + ", ".join(missing))
    return _check("beats", "pass", "every required beat is on a panel")


def _lettering_texts(state: ProjectState) -> list[str]:
    texts: list[str] = []

    def _take(panel) -> None:
        for value in (panel.caption, panel.dialogue, panel.sfx):
            if isinstance(value, str) and value.strip():
                text = value.strip()
                if text not in texts:
                    texts.append(text)

    for cache in state.chunk_cache.values():
        board = cache.storyboard
        if board is None:
            continue
        for panel in board.panels:
            _take(panel)
    for pageset in state.page_cache.values():
        for page in pageset.pages:
            for panel in page.panels:
                _take(panel)
    return texts


def _visible_text_check(state: ProjectState, source: str) -> StructureCheck:
    texts = _lettering_texts(state)
    if not texts:
        return _check("visible_text", "absent", "no lettering text")
    canonical = _normalize(source)
    missing = [text for text in texts if text not in canonical]
    if missing:
        return _check("visible_text", "fail", "not in the source: " + " | ".join(missing))
    return _check("visible_text", "pass", "lettering text is in the source")


def _causal_check(state: ProjectState) -> StructureCheck:
    problems: list[str] = []
    saw_edge = False
    for plan in state.adaptation_plans.values():
        earlier: list[str] = []
        for beat in plan.beats:
            for dependency in beat.depends_on:
                saw_edge = True
                if dependency not in earlier:
                    problems.append(f"{beat.beat_id} depends on {dependency}")
            if beat.beat_id and beat.beat_id not in earlier:
                earlier.append(beat.beat_id)
    if not saw_edge:
        return _check("causal_order", "absent", "no dependencies")
    if problems:
        return _check("causal_order", "fail", "; ".join(problems))
    return _check("causal_order", "pass", "each dependency names an earlier beat in the same unit")


def _blocked_check(state: ProjectState) -> StructureCheck:
    problems: list[str] = []
    done = list(state.pages_done) + list(state.panels_done)
    for key in state.skipped_chunks:
        drawn = []
        if str(key).isdigit():
            prefix = f"c{int(key):04d}-"
            drawn = [item for item in done if item.startswith(prefix)]
        if drawn:
            problems.append(f"chunk {key} skipped but marked done: {', '.join(drawn)}")
        else:
            problems.append(f"chunk {key} skipped")
    for key, record in state.tombstones.items():
        problems.append(f"{key} {record.outcome}")
    if not problems:
        return _check("blocked", "pass", "no skipped chunks or tombstones")
    return _check("blocked", "fail", "; ".join(problems))


def chapter_validated(report: StructureReport, state: ProjectState) -> bool:
    """A chapter is validated only when every check passes and a page was stored.

    Absent checks and a project with no drawn page do not count.
    """
    if not state.pages_done and not state.panels_done:
        return False
    return all(check.status == "pass" for check in report.checks)


def structural_report(state: ProjectState, source: str) -> StructureReport:
    """Check the five structural records. Does not call a model."""
    return StructureReport(
        checks=[
            _source_check(state, source),
            _beats_check(state),
            _visible_text_check(state, source),
            _causal_check(state),
            _blocked_check(state),
        ]
    )


def write_structure_report(
    report: StructureReport,
    output_dir: str | Path,
    *,
    state: ProjectState | None = None,
) -> Path:
    """Write ``structure_report.json`` next to the project state."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    payload = report.to_json()
    if state is not None:
        payload["chapter_validated"] = chapter_validated(report, state)
    path = out / "structure_report.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
