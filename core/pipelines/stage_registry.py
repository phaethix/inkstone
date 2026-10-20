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


def stage_spec(name: str) -> StageSpec:
    for spec in _SPECS:
        if spec.name == name:
            return spec
    raise KeyError(f"no stage spec for {name}")


def stage_contract_for(name: str) -> StageContract:
    return stage_contract(stage_spec(name).contract_stage)


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
    return problems
