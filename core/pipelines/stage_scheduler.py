"""Ephemeral dependency graph for the comic pipeline stages.

This module intentionally has no dependency on pipeline execution, persistence,
providers, or action-key derivation.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class PipelinePhase(str, Enum):
    PLAN = "plan"
    RENDER = "render"
    EXPORT = "export"


class WorkKind(str, Enum):
    PLAN_CHUNK = "plan_chunk"
    RENDER_PAGE = "render_page"
    RENDER_PANEL = "render_panel"
    RENDER_PANEL_BATCH = "render_panel_batch"
    EXPORT = "export"


_PHASE_ORDER = {
    PipelinePhase.PLAN: 0,
    PipelinePhase.RENDER: 1,
    PipelinePhase.EXPORT: 2,
}


@dataclass(frozen=True)
class WorkNode:
    node_id: str
    phase: PipelinePhase
    kind: WorkKind
    unit_index: int | None
    item_index: int | None
    dependencies: tuple[str, ...]


class StageGraph:
    """Mutable, in-memory graph whose nodes are immutable work descriptions."""

    def __init__(self) -> None:
        self._nodes: dict[str, WorkNode] = {}

    def add(self, node: WorkNode) -> None:
        if node.node_id in self._nodes:
            raise ValueError(f"duplicate work node id: {node.node_id}")
        self._nodes[node.node_id] = node

    def validate(self) -> None:
        for node_id in sorted(self._nodes):
            node = self._nodes[node_id]
            for dependency_id in node.dependencies:
                dependency = self._nodes.get(dependency_id)
                if dependency is None:
                    raise ValueError(
                        f"work node {node_id} has missing dependency {dependency_id}"
                    )
                if _PHASE_ORDER[dependency.phase] > _PHASE_ORDER[node.phase]:
                    raise ValueError(
                        f"phase-inverted dependency: {node_id} ({node.phase.value}) "
                        f"depends on {dependency_id} ({dependency.phase.value})"
                    )

        state: dict[str, int] = {}
        path: list[str] = []

        def visit(node_id: str) -> None:
            state[node_id] = 1
            path.append(node_id)
            for dependency_id in sorted(self._nodes[node_id].dependencies):
                if state.get(dependency_id, 0) == 0:
                    visit(dependency_id)
                elif state.get(dependency_id) == 1:
                    cycle_start = path.index(dependency_id)
                    cycle = path[cycle_start:] + [dependency_id]
                    raise ValueError("dependency cycle: " + " -> ".join(cycle))
            path.pop()
            state[node_id] = 2

        for node_id in sorted(self._nodes):
            if state.get(node_id, 0) == 0:
                visit(node_id)

    def ready(
        self, completed: set[str], phase: PipelinePhase
    ) -> tuple[WorkNode, ...]:
        if any(
            _PHASE_ORDER[node.phase] < _PHASE_ORDER[phase]
            and node_id not in completed
            for node_id, node in self._nodes.items()
        ):
            return ()

        eligible = (
            node
            for node_id, node in self._nodes.items()
            if node.phase is phase
            and node_id not in completed
            and all(dependency_id in completed for dependency_id in node.dependencies)
        )
        return tuple(
            sorted(
                eligible,
                key=lambda node: (
                    -1 if node.unit_index is None else node.unit_index,
                    -1 if node.item_index is None else node.item_index,
                    node.node_id,
                ),
            )
        )
