"""core.pipelines.stage_registry — declarative catalog of pipeline stages.

The scheduler and the action-key code already know these stages, but the
knowledge was spread across ``stage_contract``, ``cas``, and the pipeline. This
catalog gives one place to answer: which phase owns a stage, which contract it
declares, and which manifest stage a successful artifact is recorded under.

It is intentionally metadata-only. It imports no pipeline code, so it can be
used by the CLI, tests, and future per-stage runners without a cycle.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.comic.stage_contract import _STAGES, StageContract, stage_contract
from core.pipelines.stage_scheduler import PipelinePhase


@dataclass(frozen=True)
class StageSpec:
    """One executable stage and where its artifact is recorded."""

    name: str
    phase: PipelinePhase
    contract_stage: str
    manifest_stage: str


_SPECS: tuple[StageSpec, ...] = (
    StageSpec("extract", PipelinePhase.PLAN, "extract", "extract"),
    StageSpec("bible", PipelinePhase.PLAN, "bible", "bible"),
    StageSpec("beats", PipelinePhase.PLAN, "beats", "beats"),
    StageSpec("page_plan", PipelinePhase.PLAN, "page_plan", "page_plan"),
    StageSpec("storyboard", PipelinePhase.PLAN, "storyboard", "storyboard"),
    StageSpec("page_script", PipelinePhase.PLAN, "page_script", "page_script"),
    StageSpec("portrait", PipelinePhase.RENDER, "portrait", "portrait"),
    StageSpec("render.page", PipelinePhase.RENDER, "render.page", "render.page"),
    StageSpec("render.panel", PipelinePhase.RENDER, "render.panel", "render.panel"),
    StageSpec("letter", PipelinePhase.RENDER, "letter", "letter"),
    StageSpec("layout", PipelinePhase.EXPORT, "layout", "layout"),
    StageSpec("export", PipelinePhase.EXPORT, "export", "export"),
    # A webtoon is a second export binding; it shares the export contract and
    # manifest stage rather than inventing a second artifact schema.
    StageSpec("webtoon", PipelinePhase.EXPORT, "export", "export"),
)


def all_specs() -> tuple[StageSpec, ...]:
    return _SPECS


# Phase 0c: the read edges, declared rather than inferred.
#
# Each stage's ``hard_inputs`` name *what* it reads; this table names *who*
# produces it. ``EXTERNAL`` covers everything the pipeline does not produce
# itself (the source text, the prompt renderer, the font, the binding choice).
#
# The two tables are checked against each other, so an input added to a
# contract without naming its producer fails the registry, and so does a
# producer that exists only in this table.
EXTERNAL = ""


_INPUT_SOURCES: dict[str, dict[str, str]] = {
    "extract": {"source_chunk": EXTERNAL},
    # A bible summarizes this chunk against the previous summary, so its own
    # prior value is a legitimate self-read and the only one allowed.
    "bible": {"source_chunk": EXTERNAL, "bible_summary": "bible"},
    "beats": {"source_chunk": EXTERNAL},
    "storyboard": {"source_chunk": EXTERNAL, "bible_entries_for_chunk": "bible"},
    "page_plan": {
        "source_chunk": EXTERNAL,
        "beats": "beats",
        "bible_entries_for_page": "bible",
    },
    "page_script": {
        "source_chunk": EXTERNAL,
        "storyboard": "storyboard",
        "extracted_elements": "extract",
    },
    "portrait": {"bible_entry_for_character": "bible", "character_asset": EXTERNAL},
    "render.page": {
        "page_plan": "page_plan",
        "bible_entries_for_page": "bible",
        "prompt_renderer": EXTERNAL,
    },
    "render.panel": {
        "storyboard_panel": "storyboard",
        "bible_entries_for_panel": "bible",
        "prompt_renderer": EXTERNAL,
    },
    # Lettering consumes the blank art, so it cannot precede render.page.
    "letter": {"blank_page": "render.page", "page_plan": "page_plan", "source_text": EXTERNAL},
    "layout": {"panel_images": "render.panel", "drawn_text": EXTERNAL, "page_geometry": EXTERNAL},
    "export": {"bound_images": "letter", "binding": EXTERNAL},
    # A webtoon shares the export contract but binds lettered pages, not panels.
    "webtoon": {"bound_images": "letter", "binding": EXTERNAL},
}


def manifest_stages() -> frozenset[str]:
    """Every ``stage`` string a current manifest may carry."""
    return frozenset(spec.manifest_stage for spec in _SPECS)


def stage_spec(name: str) -> StageSpec:
    for spec in _SPECS:
        if spec.name == name:
            return spec
    raise KeyError(f"no stage spec for {name}")


def stage_contract_for(name: str) -> StageContract:
    return stage_contract(stage_spec(name).contract_stage)


def input_sources(stage: str) -> dict[str, str]:
    """Map each hard input of ``stage`` to the stage that produces it.

    ``EXTERNAL`` means the pipeline does not produce it.
    """
    stage_spec(stage)  # an unknown stage must fail here, not silently return {}
    try:
        return dict(_INPUT_SOURCES[stage])
    except KeyError as exc:
        raise KeyError(f"no input sources for {stage}") from exc


def read_edges() -> dict[str, frozenset[str]]:
    """``stage -> producer stages``, excluding external inputs.

    This is the machine-readable form of §5's declared-dependency rule: an edge
    here means the producer's bytes may legitimately reach the consumer's key.
    """
    edges: dict[str, frozenset[str]] = {}
    for spec in _SPECS:
        producers = {
            producer for producer in input_sources(spec.name).values() if producer != EXTERNAL
        }
        edges[spec.name] = frozenset(producers)
    return edges


def upstream_of(name: str) -> frozenset[str]:
    """Transitive producers of ``name``, including itself."""
    edges = read_edges()
    seen: set[str] = set()
    stack = [name]
    while stack:
        current = stack.pop()
        for producer in edges.get(current, frozenset()):
            if producer not in seen:
                seen.add(producer)
                stack.append(producer)
    return frozenset(seen | {name})


def validate_read_edges() -> list[str]:
    """Return inconsistencies between contracts and the read-edge table."""
    problems: list[str] = []
    names = {spec.name for spec in _SPECS}
    contract_names = {spec.contract_stage for spec in _SPECS}
    phase_of = {spec.name: spec.phase for spec in _SPECS}
    order = {PipelinePhase.PLAN: 0, PipelinePhase.RENDER: 1, PipelinePhase.EXPORT: 2}

    for spec in _SPECS:
        expected = stage_contract_for(spec.name).hard_inputs
        declared = input_sources(spec.name)
        missing = expected - set(declared)
        extra = set(declared) - expected
        for input_name in sorted(missing):
            problems.append(f"{spec.name}: hard input {input_name!r} has no declared producer")
        for input_name in sorted(extra):
            problems.append(f"{spec.name}: declares a producer for unknown input {input_name!r}")
        for input_name, producer in declared.items():
            if producer == EXTERNAL:
                continue
            if producer not in names and producer not in contract_names:
                problems.append(
                    f"{spec.name}: input {input_name!r} names unknown producer {producer!r}"
                )
            elif producer in names and order[phase_of[producer]] > order[spec.phase]:
                problems.append(
                    f"{spec.name} ({spec.phase.value}) reads {producer} "
                    f"({phase_of[producer].value}), which runs later"
                )
    return problems


def validate_stage_registry() -> list[str]:
    """Return human-readable inconsistencies; an empty list is healthy."""
    problems: list[str] = []
    names = [spec.name for spec in _SPECS]
    if len(names) != len(set(names)):
        problems.append("duplicate stage spec")
    for spec in _SPECS:
        if spec.contract_stage not in _STAGES:
            problems.append(f"{spec.name}: unknown contract {spec.contract_stage}")
    for contract_name in _STAGES:
        if contract_name not in names and contract_name not in {
            spec.contract_stage for spec in _SPECS
        }:
            problems.append(f"contract {contract_name} has no stage spec")
    problems.extend(validate_read_edges())
    return problems
