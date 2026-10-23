"""core.pipelines.plan_graph — the work graph a run plans before it draws.

The pipeline's two passes share one shape: plan every chunk before spending an
image call, then render the nodes the graph produced. Building that graph is pure
bookkeeping over the checkpoint — no provider, no key, no fingerprint — so it was
227 lines of the orchestrator that only needed ``ProjectState`` and the node
types.

Nothing here derives an action key. The graph says *which node to visit*; the
node's key is derived at the point of work, so this module cannot drift from the
key rules in ``core.comic.cas``.
"""

from __future__ import annotations

from core.pipelines.stage_scheduler import PipelinePhase, StageGraph, WorkKind, WorkNode
from core.schemas import ProjectState


def _panel_state_key(chunk_index: int, panel_index: int) -> str:
    """Return the pipeline-owned identity for one storyboard position."""
    return f"c{chunk_index:04d}-p{panel_index:04d}"


def _build_plan_graph(chunk_count: int) -> StageGraph:
    """Describe the serial planning pass without touching pipeline state."""
    graph = StageGraph()
    previous_node_id: str | None = None
    for chunk_index in range(chunk_count):
        node_id = f"plan.chunk:{chunk_index:04d}"
        graph.add(
            WorkNode(
                node_id=node_id,
                phase=PipelinePhase.PLAN,
                kind=WorkKind.PLAN_CHUNK,
                unit_index=chunk_index,
                item_index=None,
                dependencies=() if previous_node_id is None else (previous_node_id,),
            )
        )
        previous_node_id = node_id
    return graph


def _expand_page_render_nodes(
    graph: StageGraph, state: ProjectState, chunk_count: int
) -> dict[int, tuple[WorkNode, ...]]:
    """Describe finished-page rendering from the plans the plan phase produced.

    One node per planned page, chained in reading order: a page's prompt uses
    the blank of the page before it, and that blank is state written by the
    earlier node. Node ids are ephemeral scheduler identities;
    ``page_state_key`` is still derived from ``(chunk, page)`` only when a node
    is dispatched, so no scheduler id ever becomes a persisted key.
    """
    nodes_by_chunk: dict[int, list[WorkNode]] = {}
    previous_node_id: str | None = None
    for chunk_index in range(chunk_count):
        pageset = state.page_cache.get(str(chunk_index))
        if pageset is None:
            continue
        for page_index in range(len(pageset.pages)):
            node_id = f"render.page:{chunk_index:04d}:{page_index:04d}"
            node = WorkNode(
                node_id=node_id,
                phase=PipelinePhase.RENDER,
                kind=WorkKind.RENDER_PAGE,
                unit_index=chunk_index,
                item_index=page_index,
                dependencies=() if previous_node_id is None else (previous_node_id,),
            )
            graph.add(node)
            nodes_by_chunk.setdefault(chunk_index, []).append(node)
            previous_node_id = node_id
    return {chunk_index: tuple(nodes) for chunk_index, nodes in nodes_by_chunk.items()}


def _expand_panel_render_nodes(
    graph: StageGraph,
    state: ProjectState,
    chunk_count: int,
    *,
    continuity: bool,
) -> tuple[dict[int, tuple[WorkNode, ...]], dict[str, tuple[str, ...]]]:
    """Describe panel-compose rendering from the planned storyboards.

    With continuity enabled, one ordered node per positional panel mirrors the
    sequential run that hands ``prev_panel_local`` forward. Without it, one
    batch node per chunk keeps the existing bounded-concurrency gather; the
    batch's positional state keys stay as in-memory scheduler metadata and are
    never persisted. Scheduler ids never become persisted keys either way.
    """
    nodes_by_chunk: dict[int, list[WorkNode]] = {}
    batch_panel_keys: dict[str, tuple[str, ...]] = {}
    previous_node_id: str | None = None
    for chunk_index in range(chunk_count):
        cached = state.chunk_cache.get(str(chunk_index))
        board = cached.storyboard if cached else None
        if board is None:
            continue
        if continuity:
            for panel_index in range(len(board.panels)):
                node_id = f"render.panel:{chunk_index:04d}:{panel_index:04d}"
                node = WorkNode(
                    node_id=node_id,
                    phase=PipelinePhase.RENDER,
                    kind=WorkKind.RENDER_PANEL,
                    unit_index=chunk_index,
                    item_index=panel_index,
                    dependencies=() if previous_node_id is None else (previous_node_id,),
                )
                graph.add(node)
                nodes_by_chunk.setdefault(chunk_index, []).append(node)
                previous_node_id = node_id
        else:
            node_id = f"render.panel_batch:{chunk_index:04d}"
            node = WorkNode(
                node_id=node_id,
                phase=PipelinePhase.RENDER,
                kind=WorkKind.RENDER_PANEL_BATCH,
                unit_index=chunk_index,
                item_index=None,
                dependencies=(),
            )
            graph.add(node)
            nodes_by_chunk.setdefault(chunk_index, []).append(node)
            batch_panel_keys[node_id] = tuple(
                _panel_state_key(chunk_index, panel_index)
                for panel_index in range(len(board.panels))
            )
    return (
        {chunk_index: tuple(nodes) for chunk_index, nodes in nodes_by_chunk.items()},
        batch_panel_keys,
    )


def _previous_render_node_id(
    nodes_by_chunk: dict[int, tuple[WorkNode, ...]], chunk_index: int
) -> str | None:
    """Return the last render node of the nearest earlier chunk that has one."""
    previous_id: str | None = None
    for earlier in sorted(ci for ci in nodes_by_chunk if ci < chunk_index):
        nodes = nodes_by_chunk[earlier]
        if nodes:
            previous_id = nodes[-1].node_id
    return previous_id


def _append_page_render_nodes(
    graph: StageGraph,
    state: ProjectState,
    chunk_index: int,
    nodes_by_chunk: dict[int, tuple[WorkNode, ...]],
) -> bool:
    """Register page nodes missing from the pre-loop expansion.

    The render pass can plan a chunk that had no pageset when render nodes were
    expanded. Those pages must be added before dispatch; the outstanding-render
    check only knows nodes that were registered. Returns True when the graph
    gained nodes.
    """
    pageset = state.page_cache.get(str(chunk_index))
    if pageset is None:
        return False
    existing = list(nodes_by_chunk.get(chunk_index, ()))
    page_count = len(pageset.pages)
    if len(existing) >= page_count:
        return False
    previous_node_id = (
        existing[-1].node_id if existing else _previous_render_node_id(nodes_by_chunk, chunk_index)
    )
    for page_index in range(len(existing), page_count):
        node_id = f"render.page:{chunk_index:04d}:{page_index:04d}"
        node = WorkNode(
            node_id=node_id,
            phase=PipelinePhase.RENDER,
            kind=WorkKind.RENDER_PAGE,
            unit_index=chunk_index,
            item_index=page_index,
            dependencies=() if previous_node_id is None else (previous_node_id,),
        )
        graph.add(node)
        existing.append(node)
        previous_node_id = node_id
    nodes_by_chunk[chunk_index] = tuple(existing)
    return True


def _append_panel_render_nodes(
    graph: StageGraph,
    state: ProjectState,
    chunk_index: int,
    nodes_by_chunk: dict[int, tuple[WorkNode, ...]],
    batch_panel_keys: dict[str, tuple[str, ...]],
    *,
    continuity: bool,
) -> bool:
    """Register panel nodes missing from the pre-loop expansion.

    Same hole as ``_append_page_render_nodes``: a storyboard stored during the
    render pass is invisible to the snapshot taken before the chunk loop.
    Returns True when the graph gained nodes.
    """
    cached = state.chunk_cache.get(str(chunk_index))
    board = cached.storyboard if cached else None
    if board is None:
        return False
    existing = list(nodes_by_chunk.get(chunk_index, ()))
    if continuity:
        panel_count = len(board.panels)
        if len(existing) >= panel_count:
            return False
        previous_node_id = (
            existing[-1].node_id
            if existing
            else _previous_render_node_id(nodes_by_chunk, chunk_index)
        )
        for panel_index in range(len(existing), panel_count):
            node_id = f"render.panel:{chunk_index:04d}:{panel_index:04d}"
            node = WorkNode(
                node_id=node_id,
                phase=PipelinePhase.RENDER,
                kind=WorkKind.RENDER_PANEL,
                unit_index=chunk_index,
                item_index=panel_index,
                dependencies=() if previous_node_id is None else (previous_node_id,),
            )
            graph.add(node)
            existing.append(node)
            previous_node_id = node_id
        nodes_by_chunk[chunk_index] = tuple(existing)
        return True
    if existing:
        return False
    node_id = f"render.panel_batch:{chunk_index:04d}"
    node = WorkNode(
        node_id=node_id,
        phase=PipelinePhase.RENDER,
        kind=WorkKind.RENDER_PANEL_BATCH,
        unit_index=chunk_index,
        item_index=None,
        dependencies=(),
    )
    graph.add(node)
    nodes_by_chunk[chunk_index] = (node,)
    batch_panel_keys[node_id] = tuple(
        _panel_state_key(chunk_index, panel_index) for panel_index in range(len(board.panels))
    )
    return True
