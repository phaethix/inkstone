"""core.pipelines.creative_comic — text-to-comic orchestration.

Default (``finished_page``) flow for one source text:

    segment -> extract -> merge characters -> (portraits)
    -> plan_comic_pages -> one finished page image per plan -> bind PDF/webtoon

Legacy (``panel_compose``) flow:

    … -> storyboard -> per panel (L1/L2, optional L3) -> LayoutEngine -> export

Billable chat products are cached so resume does not re-pay:
``ProjectState.chunk_cache`` holds extract / storyboard / optional page_script;
``ProjectState.page_cache`` holds finished-page plans (``ComicPagePlanSet``).
Generated page/panel assets resume independently via ``pages_done`` /
``panels_done``.

Providers are injected so the pipeline can be exercised without network.
"""

import asyncio
import hashlib
import json
import logging
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import TypeVar

from PIL import Image
from pydantic import BaseModel

from core.api import get_chat_provider, get_image_provider
from core.comic.adaptation import (
    assign_panel_beat_ids,
    key_beats_for_chunk,
    stamp_page_panel_beats,
    stamp_storyboard_beats,
    sync_adaptation_plans,
)
from core.comic.artifact_projection import drop_page_artifact, record_page_artifact
from core.comic.budget import BudgetPaused, BudgetSession, BudgetSpec
from core.comic.cas import (
    beats_action_key,
    bible_action_key,
    bible_inputs,
    extract_action_key,
    extract_inputs,
    letter_action_key,
    letter_inputs,
    page_plan_action_key,
    page_script_action_key,
    portrait_action_key,
    put_bytes,
    read_manifest_entry,
    render_page_action_key,
    render_page_inputs,
    render_panel_action_key,
    stored_output,
    storyboard_action_key,
)
from core.comic.consistency import (
    DEFAULT_PORTRAIT_STYLE,
    ConsistencyEngine,
    _panel_reference_names,
)
from core.comic.env_snapshot import h_env
from core.comic.export import (
    _pdf_batch_size,
)
from core.comic.fonts import resolve_font
from core.comic.gate import SAMPLE_GATE_SIZE, GatePaused, GateSession
from core.comic.identity import (
    apply_recorded_merges,
    elements_with_recorded_merges,
    ensure_character_l1,
    harden_human_identity_prompt,
    merge_settings,
    offer_alias_suggestion,
    page_state_key,
    suggestion_from_alias,
)
from core.comic.key_beats import (
    beat_coverage_retry_note,
    cacheable_beats,
    uncovered_must_draw_beats,
)
from core.comic.layout import PanelImage, _webtoon_max_pixels
from core.comic.layout_diversity import layout_diversity_instructions
from core.comic.ledger import ConsistencyLedger, ContinuityReviewPaused
from core.comic.lettering_lang import (
    lettering_field_mismatches,
    sanitize_lettering_text,
    sanitize_plan_lettering,
    scrub_lettering_panel,
    source_lettering_script,
    strip_mismatched_lettering,
    strip_pinyin_glosses,
    truncate_lettering,
)
from core.comic.locks import exclusive_lock
from core.comic.page_lettering import (
    LETTERING_VERSION,
    _avoid_overlap,
    _clamp_box,
    _clamp_pixel_box,
    _heuristic_box,
    _overlap_area,
    _safe_anchor,
    _shift_off_faces,
    detect_page_faces,
    fit_lettering_box,
    letter_finished_page,
    resolve_lettering_jobs,
)
from core.comic.page_prompt import render_finished_page_prompt
from core.comic.review_packet import ReviewPacket
from core.comic.runs import bind_audit, clear_audit, image_failure, image_intent
from core.comic.segmentation import (
    detect_character_aliases,
    merge_characters,
    segment_text,
    source_units,
)
from core.comic.stage_contract import (
    RECENT_LAYOUT_LIMIT,
    historical_identity,
    stage_source_hash,
)
from core.comic.visual_bible import (
    apply_reconcile,
    backfill_panel_characters,
    canonical_portrait_ref,
    canonicalize_page_plan,
    collect_finished_page_refs,
    finished_page_ref_policy,
    format_color_bible_block,
    l1_from_canon,
    parse_stage_ref,
    portrait_gender_era_suffix,
    refresh_bible_hash,
    resolve_canonical_name,
    resolve_character_asset,
    resolve_panel_stage_refs,
    rewrite_pageset_from_bible,
    sanitize_visual_bible_state,
    sync_characters_from_bible,
)
from core.comic.voice import sanitize_plan_voice, voice_timeline_plan_instructions
from core.config import (
    ImageConfig,
    finished_page_size,
    harness_enabled,
    l3_enabled,
    lessons_enabled,
    page_script_enabled,
    review_packet_enabled,
)
from core.config import (
    data_dir as config_data_dir,
)
from core.config import (
    genre as config_genre,
)
from core.config import harness_dir as config_harness_dir
from core.config import render_mode as config_render_mode
from core.density import PANELS_PER_CHUNK_B, DensityTier, clips_plan, get_density_plan
from core.harness.spec import (
    HarnessError,
    builtin_harness,
    harness_from_dir,
    validate_or_raise,
)
from core.lessons import LessonStore, format_craft_notes
from core.perf import PerfCollector
from core.pipelines.cancel import check_cancel
from core.pipelines.export_stages import (  # noqa: F401  (the four constants are read by migrate)
    _CBZ_STAGE_SRC,
    _DRAW_SOURCES,
    _EXPORT_STAGE_SRC,
    _LAYOUT_STAGE_SRC,
    _WEBTOON_STAGE_SRC,
    export_cbz_with_manifest,
    export_pdf_with_manifest,
    export_webtoon_with_manifest,
    layout_pages_with_manifest,
    prepare_finished_page_export,
)
from core.pipelines.reconcile import (
    is_unsupported_image_size_error,
)
from core.pipelines.reconcile import (
    is_within as _is_within,
)
from core.pipelines.reconcile import (
    reconcile_state as _reconcile_state,
)
from core.pipelines.reconcile import (
    soft_invalidate_render as _soft_invalidate_render,
)
from core.pipelines.stage_registry import validate_stage_registry
from core.pipelines.stage_runner import record, run_stage
from core.pipelines.stage_scheduler import PipelinePhase, StageGraph, WorkKind, WorkNode
from core.schemas import (
    ChunkCache,
    ComicPagePlan,
    ComicPagePlanSet,
    GeneratedPage,
    GeneratedPanel,
    KeyBeatSet,
    ModelSnapshot,
    PageScript,
    ProjectState,
    Setting,
    Storyboard,
    StoryElements,
    Tombstone,
    VisualBibleReconcileResult,
)
from core.screenwriter import (
    _budget_sentence,
    _clip_page_plan,
    build_beats_request,
    build_bible_request,
    build_extract_request,
    build_page_plan_request,
    build_page_script_request,
    build_storyboard_request,
    extract_key_beats,
    extract_story_elements,
    is_content_policy_rejection,
    plan_comic_pages,
    plan_page_script,
    plan_storyboard,
    reconcile_visual_bible,
)

logger = logging.getLogger(__name__)


@dataclass
class ComicProject:
    """Result of a comic generation run."""

    project_id: str
    state: ProjectState
    pages: list[str] = field(default_factory=list)
    pdf: str | None = None
    webtoon: str | None = None
    cbz: str | None = None


T = TypeVar("T", bound=BaseModel)


def _restore_stage_body(
    root: Path,
    key: str,
    stage: str,
    model: type[T],
) -> T | None:
    """Read and parse a verified manifest body, or return None on any miss."""
    raw = stored_output(root, key, stage=stage)
    if raw is None:
        return None
    try:
        return model.model_validate_json(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        logger.warning("corrupt manifest body for %s (%s): %s", key, stage, exc)
        return None


def _record_stage_body(
    root: Path,
    stage: str,
    key: str,
    body: BaseModel,
    *,
    stage_src: str,
    env: str,
    inputs: list[str],
) -> None:
    """Store a planning stage's parsed body under its registry manifest stage.

    The output keeps the ``<stage>.json`` name every existing project already
    has recorded, so this is a change of who writes the manifest, not of what
    the manifest says.
    """
    record(
        root,
        stage,
        key,
        [(f"{stage}.json", body.model_dump_json(indent=2).encode("utf-8"))],
        stage_src=stage_src,
        env=env,
        inputs=inputs,
    )


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


def _provider_label(provider, model: str) -> str:
    """Return a non-secret provider/model identity suitable for cache invalidation."""
    provider_type = f"{type(provider).__module__}.{type(provider).__qualname__}"
    base_url = getattr(provider, "base_url", "")
    return f"{provider_type}|{base_url}|{model}"


def _model_snapshot(chat, image) -> ModelSnapshot:
    return ModelSnapshot(
        chat=_provider_label(chat, getattr(chat, "model", "")),
        t2i=_provider_label(image, getattr(image, "model", "")),
        i2i=_provider_label(image, getattr(image, "i2i_model", "")),
    )


def _require_density(density: str | None) -> DensityTier:
    if density is None or density == "B":
        return "B"
    if density == "A":
        return "A"
    if density == "C":
        return "C"
    raise ValueError(f"density must be A, B, or C, got {density!r}")


def _structure_fingerprint(
    source_txt: str,
    *,
    density: str = "B",
    panels_per_chunk: int = PANELS_PER_CHUNK_B,
) -> str:
    canonical = source_txt.replace("\r\n", "\n").replace("\r", "\n")
    body: dict[str, object] = {"source": canonical}
    # Tier B at the historical panel count stays out of the hash, so a project
    # planned before density was stored still resumes.
    if density != "B" or panels_per_chunk != PANELS_PER_CHUNK_B:
        body["density"] = density
        body["panels_per_chunk"] = panels_per_chunk
    payload = json.dumps(
        body,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _bible_fingerprint_kwargs(visual_bible) -> tuple[str | None, str | None]:
    if visual_bible is None or not visual_bible.content_hash:
        return None, None
    return visual_bible.version, visual_bible.content_hash


def _known_character_names(state: ProjectState) -> list[str]:
    names: list[str] = list(state.characters.keys())
    bible = state.visual_bible
    if bible is None:
        return names
    for canon_name, canon in bible.characters.items():
        names.append(canon_name)
        names.extend(canon.aliases)
    return names


def _chunk_sort_key(cache_key: str) -> tuple[int, int, str]:
    """Order ``page_cache`` keys by resolved chunk index, then lexicographically.

    Keys are ``str(chunk_index)``, so plain ``sorted()`` is lexicographic and
    ranks ``"10"`` before ``"2"``. Past ten chunks the "last 8 layout intents"
    window would read the wrong chunks, which is a §9 phase 0e defect: the
    anti-templating window silently becomes the whole book. Non-numeric keys
    (only reachable if a caller writes one) sort last instead of crashing.
    """
    try:
        return (0, int(cache_key), "")
    except ValueError:
        return (1, 0, cache_key)


def _recent_layout_intents(state: ProjectState, *, limit: int = RECENT_LAYOUT_LIMIT) -> list[str]:
    """Collect recent page layout_intent strings from cached page plans.

    The returned strings are a planner hint. They are not a key input: the
    declared input is the window size plus the summarizer source hash
    (``stage_contract.historical_identity``). Putting the strings in a key
    would re-plan every later chunk when one early layout changed.
    """
    intents: list[str] = []
    for cache_key in sorted(state.page_cache.keys(), key=_chunk_sort_key):
        pageset = state.page_cache.get(cache_key)
        if pageset is None:
            continue
        for plan in pageset.pages:
            intent = (plan.layout_intent or "").strip()
            if intent:
                intents.append(intent)
    return intents[-limit:]


def _render_fingerprint(
    style_guide: str | None,
    *,
    snapshot: ModelSnapshot,
    panel_continuity: bool,
    l3_enabled: bool,
    render_mode: str = "finished_page",
    page_size: str = "1024x1536",
    bible_version: str | None = None,
    bible_hash: str | None = None,
) -> str:
    fp_payload: dict[str, object] = {
        "style_guide": style_guide or "",
        "model_snapshot": snapshot.model_dump(),
        "panel_continuity": panel_continuity,
        "l3_enabled": l3_enabled,
        "render_mode": render_mode,
        "page_size": page_size,
    }
    if bible_version is not None:
        fp_payload["visual_bible"] = bible_version
    if bible_hash is not None:
        fp_payload["bible_hash"] = bible_hash
    payload = json.dumps(
        fp_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _portrait_content_id(output_dir: Path, path: str) -> str | None:
    """Store a portrait that lives inside the project and return its content id."""
    file = Path(path)
    if not file.is_absolute():
        file = output_dir / file
    if file.is_file() and _is_within(file, output_dir):
        return put_bytes(output_dir, file.read_bytes())
    return None


def _record_portrait_reference(
    ledger: ConsistencyLedger, output_dir: Path, name: str, path: str
) -> None:
    """Copy a portrait into ``cas/`` and record that hash on the ledger.

    The path stays the file the rest of the pipeline reads. The hash is what
    ``gc`` keeps, so the authoritative reference survives after the age threshold.
    """
    ledger.record_reference(name, path, content_hash=_portrait_content_id(output_dir, path))


def _bind_missing_portrait_hashes(ledger: ConsistencyLedger, output_dir: Path) -> bool:
    """Fill ``content_hash`` for references written before portraits entered ``cas/``.

    The version and the pending pages stay as they are. A missing file, or a
    path outside the project, is left unset.
    """
    changed = False
    for entry in ledger.characters.values():
        ref = entry.reference
        if ref.content_hash or not ref.path:
            continue
        content_hash = _portrait_content_id(output_dir, ref.path)
        if content_hash is None:
            continue
        ref.content_hash = content_hash
        changed = True
    return changed


def _panel_state_key(chunk_index: int, panel_index: int) -> str:
    """Return the pipeline-owned identity for one storyboard position."""
    return f"c{chunk_index:04d}-p{panel_index:04d}"


def _stored_panel_key(state: ProjectState, chunk_index: int, panel_index: int) -> str:
    """Return the pipeline-owned identity for a current-version storyboard panel."""
    return _panel_state_key(chunk_index, panel_index)


def _page_asset_path(pages_dir: Path, chunk_index: int, page_index: int) -> Path:
    """Deterministic ``page_XX.png``-style path for one (chunk, page) position.

    Position-derived (not a running counter) so regenerating a stale/missing
    page always rewrites the same file — a counter would risk colliding with
    an unrelated page's filename when only some pages are redone on resume.
    The ``page_`` prefix and zero-padding keep ``finished_page_files`` in
    correct reading order.
    """
    return pages_dir / f"page_c{chunk_index:04d}_p{page_index:04d}.png"


# Hashed once at import. A monkeypatch of a stage function must not change the
# key of bytes already stored under the real source. Each tuple is the behaviour
# that stage draws or binds. The page grid is not part of lettering, and the
# pipeline function is not part of any of them.
_LETTER_SOURCES = (
    *_DRAW_SOURCES,
    _avoid_overlap,
    _clamp_box,
    _clamp_pixel_box,
    _heuristic_box,
    _overlap_area,
    _safe_anchor,
    _shift_off_faces,
    detect_page_faces,
    fit_lettering_box,
    letter_finished_page,
    resolve_lettering_jobs,
    sanitize_lettering_text,
    source_lettering_script,
    strip_pinyin_glosses,
    truncate_lettering,
)
# These are called by a hashed stage and left out on purpose. Batch size and the
# webtoon pixel cap do not change a file that was produced. The font file's
# bytes already enter ``h_env``, so the resolver's source does not.
_STAGE_SOURCE_EXCLUDED = (
    _pdf_batch_size,
    _webtoon_max_pixels,
    resolve_font,
)
# Lettering keeps its own sources here: the export module owns the bind keys, but
# this stage is executed by ``_letter_page_from_blank`` in this file.
_LETTER_STAGE_SRC = stage_source_hash(*_LETTER_SOURCES)


def _portrait_key_material(name: str, asset, style: str) -> str:
    """Stable portrait identity. A per-chunk source warning is not part of it."""
    return "\n".join([name, asset.portrait_prompt or "", asset.l1_prompt or "", style or ""])


def _panel_key_material(panel) -> str:
    """Panel identity. The previous panel and portrait files are not part of it."""
    names = ",".join(panel.characters_present or [])
    return "\n".join([panel.action or "", panel.setting_ref or "", names, panel.size or ""])


def _panel_render_key(panel, style: str, state_key: str, image_model: str) -> str:
    return render_panel_action_key(
        panel_json=_panel_key_material(panel),
        style=style,
        page_key=state_key,
        env=image_model,
        stage_src=_RENDER_PANEL_STAGE_SRC,
    )


def _sync_stored_panel_key(
    state: ProjectState, state_key: str, panel, style: str, image_model: str
) -> None:
    """Stamp a legacy panel, or drop it from the done set when its key misses.

    A missing file still has to be painted. A stale panel is left for the
    existing stale check.
    """
    existing = state.generated.panels.get(state_key)
    if existing is None or not Path(existing.local).is_file():
        return
    if state_key in state.stale_panels:
        return
    key = _panel_render_key(panel, style, state_key, image_model)
    if not existing.render_action_key:
        existing.render_action_key = key
        return
    if existing.render_action_key != key and state_key in state.panels_done:
        state.panels_done.remove(state_key)


_EXTRACT_STAGE_SRC = stage_source_hash(build_extract_request, extract_story_elements)
_BEATS_STAGE_SRC = stage_source_hash(build_beats_request, extract_key_beats)
_BIBLE_STAGE_SRC = stage_source_hash(build_bible_request, reconcile_visual_bible)
_PAGE_PLAN_STAGE_SRC = stage_source_hash(
    build_page_plan_request,
    plan_comic_pages,
    _clip_page_plan,
    _budget_sentence,
    source_lettering_script,
    lettering_field_mismatches,
    strip_mismatched_lettering,
    sanitize_plan_lettering,
    layout_diversity_instructions,
    voice_timeline_plan_instructions,
)
_STORYBOARD_STAGE_SRC = stage_source_hash(
    build_storyboard_request, plan_storyboard, assign_panel_beat_ids
)
_PAGE_SCRIPT_STAGE_SRC = stage_source_hash(build_page_script_request, plan_page_script)
_PORTRAIT_STAGE_SRC = stage_source_hash(_portrait_key_material)
_RENDER_PANEL_STAGE_SRC = stage_source_hash(_panel_key_material)
_RENDER_PAGE_STAGE_SRC = stage_source_hash(render_finished_page_prompt)


def _plan_json(plan: ComicPagePlan) -> str:
    payload = plan.model_dump(mode="json")
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _beats_payload(beats) -> str:
    """Canonical beats for a page-plan key. The stored action key is not an input."""
    if beats is None:
        return ""
    payload = beats.model_dump(mode="json")
    payload.pop("action_key", None)
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _model_payload(model) -> str:
    """Canonical JSON for a key input. ``None`` is an empty payload."""
    if model is None:
        return ""
    payload = model.model_dump(mode="json")
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _page_script_elements(elements) -> str:
    """Plan-facing extract. Portrait files and rewritten prompts stay out."""
    if elements is None:
        return ""
    payload = {
        "characters": [
            {
                "name": character.name,
                "portrait_prompt": character.portrait_prompt,
                "role": character.role,
            }
            for character in elements.characters
        ],
        "settings": [
            {"name": setting.name, "scene_prompt": setting.scene_prompt}
            for setting in elements.settings
        ],
        "style_guide": elements.style_guide,
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _page_script_plan(
    cache: ChunkCache | None,
    chunk: str,
    chat_model: str,
    *,
    harness=None,
    output_dir: Path | None = None,
) -> tuple[bool, str]:
    """Return whether a page script is missing, and the key for the one to store.

    An empty key is stamped. A mismatched key drops the stored script.
    """
    if not _page_script_enabled():
        return False, ""
    elements = cache.elements if cache is not None else None
    board = cache.storyboard if cache is not None else None
    key_request = (
        build_page_script_request(board, StoryElements(), chunk, harness=harness, model=chat_model)
        if board is not None
        else None
    )
    script_key = page_script_action_key(
        chunk=chunk,
        storyboard_json=_model_payload(board),
        elements_json=_page_script_elements(elements),
        env=chat_model,
        stage_src=_PAGE_SCRIPT_STAGE_SRC,
        request_digest=key_request.digest() if key_request is not None else None,
    )
    if cache is not None and cache.page_script is not None:
        stored = cache.page_script_action_key
        if stored and stored != script_key:
            cache.page_script = None
        elif not stored:
            cache.page_script_action_key = script_key
    missing = cache is None or cache.page_script is None
    if missing and cache is not None and output_dir is not None:
        restored = _restore_stage_body(output_dir, script_key, "page_script", PageScript)
        if restored is not None:
            cache.page_script = restored
            cache.page_script_action_key = script_key
            missing = False
    return missing, script_key


def _store_page_script(cache: ChunkCache, script: PageScript, action_key: str) -> None:
    cache.page_script = script
    cache.page_script_action_key = action_key


def _scrub_chunk_lettering(state: ProjectState, key: str, chunk: str) -> bool:
    """Drop stored lettering that is not a contiguous span of this chunk."""
    changed = False
    pageset = state.page_cache.get(key)
    if pageset is not None:
        for page in pageset.pages:
            for panel in page.panels:
                changed = scrub_lettering_panel(panel, chunk) or changed
    cached = state.chunk_cache.get(key)
    board = cached.storyboard if cached is not None else None
    if board is not None:
        for panel in board.panels:
            changed = scrub_lettering_panel(panel, chunk) or changed
    return changed


def _page_plan_key(
    chunk: str,
    beats,
    chat_model: str,
    beats_key: str,
    request_digest: str | None = None,
) -> str:
    return page_plan_action_key(
        chunk=chunk,
        beats_json=_beats_payload(beats),
        beats_key=beats_key,
        history=historical_identity("page_plan"),
        env=chat_model,
        stage_src=_PAGE_PLAN_STAGE_SRC,
        request_digest=request_digest,
    )


def _stamp_action_key(state: ProjectState, state_path: Path, target, key: str) -> bool:
    """Stamp a cached plan's missing action key and persist it immediately.

    A resume path changes only this stamp. Waiting for the end-of-run save
    loses it on a budget/continuity pause or a crash, and the next resume
    re-derives it. Persist at the mutation, as storyboard already does.
    """
    if target is None or target.action_key:
        return False
    target.action_key = key
    state.save(state_path)
    return True


def _letter_key(
    blank: bytes,
    plan: ComicPagePlan,
    source_text: str,
    *,
    resolved_size: str | None = None,
) -> tuple[str, str, str]:
    """Return the letter key, its environment digest, and the canonical plan.

    ``resolved_size`` is the size the provider accepted. A square fallback
    therefore misses a key that was stored for the configured size.
    """
    plan_json = _plan_json(plan)
    env = h_env(
        text=source_text or plan_json,
        resolved_size=resolved_size or None,
        scope="letter",
    )
    key = letter_action_key(blank=blank, plan_json=plan_json, env=env, stage_src=_LETTER_STAGE_SRC)
    return key, env, plan_json


def _letter_resume_status(
    blank_path: Path,
    local_path: Path,
    plan: ComicPagePlan,
    source_text: str,
    output_dir: Path,
    *,
    resolved_size: str | None = None,
) -> str:
    """``current`` when the lettered file is the stored hit for this environment.

    ``tombstone`` is a non-ok hit: do not letter and do not paint. ``miss``
    means the blank can be lettered again, including after a font change.
    """
    key, _env, _plan_json = _letter_key(
        blank_path.read_bytes(), plan, source_text, resolved_size=resolved_size
    )
    recorded = read_manifest_entry(output_dir, key, stage="letter")
    if recorded is not None and recorded.outcome != "ok":
        return "tombstone"
    cached = stored_output(output_dir, key, stage="letter")
    if cached is None or not local_path.is_file() or local_path.read_bytes() != cached:
        return "miss"
    return "current"


def _letter_page_from_blank(
    blank_path: Path,
    local_path: Path,
    plan: ComicPagePlan,
    *,
    source_text: str = "",
    output_dir: Path | None = None,
    resolved_size: str | None = None,
) -> None:
    """Render deferred lettering from a persisted blank page.

    When ``output_dir`` is set, an ``ok`` manifest for the same blank, plan,
    and environment is copied into place. A non-ok manifest is a hit too, so
    the page is not lettered again until ``rebuild`` releases it. A miss stores
    the new bytes.
    """
    root = Path(output_dir) if output_dir is not None else None
    key = ""
    env = ""
    plan_json = ""
    if root is not None:
        key, env, plan_json = _letter_key(
            blank_path.read_bytes(), plan, source_text, resolved_size=resolved_size
        )

    def _letter() -> list[tuple[str, bytes]]:
        with Image.open(blank_path) as blank:
            lettered = letter_finished_page(blank, plan, source_text=source_text)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        lettered.save(local_path)
        return [(local_path.name, local_path.read_bytes())]

    if root is None:
        _letter()
        return
    run = run_stage(
        root,
        "letter",
        key,
        _letter,
        stage_src=_LETTER_STAGE_SRC,
        env=env,
        inputs=letter_inputs(blank_path.read_bytes(), plan_json),
    )
    if run.tombstoned or not run.files:
        return
    if run.replayed:
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(run.files[0][1])


def _asset_path(root: Path, directory: str, identifier: str) -> Path:
    """Map any model-facing identifier to a portable, collision-safe asset path."""
    digest = hashlib.sha256(identifier.encode("utf-8")).hexdigest()
    target = (root / directory / f"id-{digest}.png").resolve()
    if not _is_within(target, root):  # defense in depth against future path changes
        raise ValueError(f"unsafe generated asset path for identifier {identifier!r}")
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


@contextmanager
def _project_lock(output_dir: Path):
    """Ensure one process mutates a project's state and assets at a time."""
    output_dir.mkdir(parents=True, exist_ok=True)
    with exclusive_lock(
        output_dir / ".inkstone.lock",
        blocking=False,
        busy_message=f"project is already running: {output_dir}",
    ):
        yield


def _setting_of(elements: StoryElements, ref: str | None):
    if not ref:
        return None
    for s in elements.settings:
        if s.name == ref:
            return s
    return None


def _resolve_setting(
    state: ProjectState,
    elements: StoryElements | None,
    ref: str | None,
) -> Setting | None:
    """Prefer the project-level settings registry, then the current chunk."""
    if not ref:
        return None
    if ref in state.settings:
        return state.settings[ref]
    if elements is not None:
        return _setting_of(elements, ref)
    return None


def _panel_needs_generation(state: ProjectState, state_key: str) -> bool:
    """True when a panel must be (re)generated this run."""
    if state_key in state.stale_panels:
        return True
    if state_key in state.panels_done or state_key in state.skipped:
        return False
    return True


def _mark_panel_done(state: ProjectState, state_key: str) -> None:
    if state_key in state.stale_panels:
        state.stale_panels = [k for k in state.stale_panels if k != state_key]
    if state_key not in state.panels_done:
        state.panels_done.append(state_key)


def _mark_chunk_done_if_complete(
    state: ProjectState,
    key: str,
    board: Storyboard,
    chunk_index: int,
) -> None:
    """Record chunks_done only after every panel is done or skipped."""
    for panel_index, _panel in enumerate(board.panels):
        state_key = _panel_state_key(chunk_index, panel_index)
        if state_key in state.stale_panels:
            return
        if state_key not in state.panels_done and state_key not in state.skipped:
            return
    if key not in state.chunks_done:
        state.chunks_done.append(key)


def previous_page_blank(
    state: ProjectState,
    pageset: ComicPagePlanSet,
    *,
    chunk_index: int,
    page_index: int,
) -> str | None:
    """Blank-page path for L2 continuity: prior page in-chunk, else last prior chunk."""
    if page_index > 0:
        prev_key = page_state_key(chunk_index, page_index - 1)
        prev_gen = state.generated.pages.get(prev_key)
        if prev_gen and prev_gen.blank_local:
            return prev_gen.blank_local
    candidates: list[tuple[int, int, str]] = []
    for gen in state.generated.pages.values():
        if not gen.blank_local:
            continue
        if gen.unit_index < chunk_index:
            candidates.append((gen.unit_index, gen.page_index, gen.blank_local))
    if not candidates:
        return None
    candidates.sort()
    return candidates[-1][2]


def next_page_in_book(
    state: ProjectState,
    pageset: ComicPagePlanSet,
    *,
    chunk_index: int,
    page_index: int,
) -> str | None:
    """The positional key of the page right after this one in reading order.

    ``previous_page_blank`` chains the last page of a chunk to the last page of
    the previous chunk, so a redraw at the end of a chunk does break the
    continuity of the next chunk's first page. This mirrors that rule forward:
    the in-chunk successor first, else the first page of the next planned chunk.
    """
    if page_index + 1 < len(pageset.pages):
        return page_state_key(chunk_index, page_index + 1)
    for later in sorted(state.page_cache, key=lambda c: int(c) if c.isdigit() else 0):
        try:
            later_index = int(later)
        except (TypeError, ValueError):
            continue
        if later_index <= chunk_index or not state.page_cache[later].pages:
            continue
        return page_state_key(later_index, 0)
    return None


def _page_reference_names(plan: ComicPagePlan) -> list[str]:
    """Ordered unique character names for a page's portrait refs (L2 only; no L3 here)."""
    names: list[str] = []
    seen: set[str] = set()
    candidates = list(plan.reference_characters) + [
        name for panel in plan.panels for name in panel.characters
    ]
    for name in candidates:
        if not name or name in seen:
            continue
        seen.add(name)
        names.append(name)
    return names


def _page_needs_generation(state: ProjectState, state_key: str) -> bool:
    """True when a finished page must be (re)generated this run."""
    if state_key in state.stale_pages:
        return True
    if state_key in state.pages_done or state_key in state.skipped_pages:
        return False
    return True


def _drop_pages_outside_plan(state: ProjectState, *, chunk_index: int, page_count: int) -> None:
    """Remove obsolete positional page projections from one replanned chunk.

    Only the current chunk's ``cNNNN-pNNNN`` keys past its new page range are
    derived artifacts. Stale markers, tombstones, and other chunks' state stay
    untouched so a shorter plan cannot discard a human-visible decision.
    """
    prefix = f"c{chunk_index:04d}-p"
    known = set(state.generated.pages) | set(state.pages_done)
    for page_key in sorted(known):
        suffix = page_key.removeprefix(prefix)
        if (
            not page_key.startswith(prefix)
            or len(suffix) != 4
            or not suffix.isdecimal()
            or int(suffix) < page_count
        ):
            continue
        drop_page_artifact(state, page_key)


def _plan_ready_to_letter(state: ProjectState, plan: ComicPagePlan) -> ComicPagePlan:
    """Apply the same plan rewrite the page loop uses before lettering."""
    if state.visual_bible is not None:
        plan = backfill_panel_characters(plan, _known_character_names(state))
        plan = resolve_panel_stage_refs(plan, state.visual_bible)
        plan = sanitize_plan_voice(plan, state.visual_bible)
    else:
        plan = sanitize_plan_voice(plan, None)
    return canonicalize_page_plan(
        plan,
        known_names=_known_character_names(state),
        visual_bible=state.visual_bible,
    )


def _page_chunk_complete(
    state: ProjectState,
    pageset: ComicPagePlanSet,
    output_dir: Path,
    chunk_index: int,
    source_text: str,
) -> bool:
    """True when every planned page is generated or policy-skipped.

    A finished page whose letter key misses — a font change, for example — is
    not complete, so the blank is lettered again. A rejected letter stays
    complete: it is not retried from this check.
    """
    pages_dir = output_dir / "pages"
    for page_index, plan in enumerate(pageset.pages):
        state_key = page_state_key(chunk_index, page_index)
        if state_key in state.stale_pages:
            return False
        if state_key in state.skipped_pages:
            continue
        rec = state.generated.pages.get(state_key)
        if (
            rec is None
            or state_key not in state.pages_done
            or not _is_within(rec.local, output_dir)
            or not Path(rec.local).exists()
        ):
            return False
        if not rec.blank_local or not Path(rec.blank_local).is_file():
            continue
        local = _page_asset_path(pages_dir, chunk_index, page_index)
        status = _letter_resume_status(
            Path(rec.blank_local),
            local,
            _plan_ready_to_letter(state, plan),
            source_text,
            output_dir,
            resolved_size=rec.resolved_size or None,
        )
        if status == "miss":
            return False
    return True


def _mark_page_chunk_done_if_complete(
    state: ProjectState,
    key: str,
    pageset: ComicPagePlanSet,
    chunk_index: int,
) -> None:
    """Record chunks_done only after every planned page is done or skipped."""
    for page_index in range(len(pageset.pages)):
        state_key = page_state_key(chunk_index, page_index)
        if state_key in state.stale_pages:
            return
        if state_key not in state.pages_done and state_key not in state.skipped_pages:
            return
    if key not in state.chunks_done:
        state.chunks_done.append(key)


def clear_stale_bible_stamps(state: ProjectState) -> bool:
    """Drop ``bible_action_keys`` when the bible body they described is gone.

    A stamp is a claim: "this chunk was reconciled against exactly this key, and
    here is the body that came out". With ``visual_bible`` missing the second
    half is false, so the stamp is a leftover that outlives its subject.

    The rule is not what makes a lost bible recover. ``needs_bible`` already
    includes ``state.visual_bible is None``, so the stage re-fetches either way.
    What the rule prevents is the *stamp* being read as evidence in a later run:
    the stage consults ``bible_action_keys`` before it consults any manifest, so
    a stale entry is the one path by which a body that no longer exists is
    treated as already reconciled.

    Returns whether anything was cleared, so a caller can decide to save.
    """
    if state.visual_bible is not None or not state.bible_action_keys:
        return False
    state.bible_action_keys = {}
    return True


_FALLBACK_PAGE_SIZE = "1024x1024"


def _chunk_complete(
    state: ProjectState, board: Storyboard, output_dir: Path, chunk_index: int
) -> bool:
    """True when every planned panel is generated or policy-skipped."""
    for panel_index, _panel in enumerate(board.panels):
        state_key = _stored_panel_key(state, chunk_index, panel_index)
        if state_key in state.stale_panels:
            return False
        if state_key in state.skipped:
            continue
        rec = state.generated.panels.get(state_key)
        if (
            rec is None
            or state_key not in state.panels_done
            or not _is_within(rec.local, output_dir)
            or not Path(rec.local).exists()
        ):
            return False
    return True


def _ordered_generated_panels(state: ProjectState) -> list[tuple[str, GeneratedPanel]]:
    """Recover canonical panel metadata from cached storyboards, including old state files."""
    ordered: list[tuple[str, GeneratedPanel]] = []
    seen: set[str] = set()
    digit_keys = sorted(
        (k for k in state.chunk_cache if str(k).isdigit()),
        key=lambda value: int(value),
    )
    for key in digit_keys:
        chunk_index = int(key)
        board = state.chunk_cache[key].storyboard
        if board is None:
            continue
        for panel_index, panel in enumerate(board.panels):
            state_key = _stored_panel_key(state, chunk_index, panel_index)
            generated = state.generated.panels.get(state_key)
            if generated is None:
                continue
            generated.chunk_index = chunk_index
            generated.panel_index = panel_index
            generated.source_panel_id = panel.panel_id
            generated.dialogue = panel.dialogue
            generated.caption = panel.caption
            generated.sfx = panel.sfx
            ordered.append((state_key, generated))
            seen.add(state_key)
    extras = [item for item in state.generated.panels.items() if item[0] not in seen]
    ordered.extend(sorted(extras, key=lambda item: (item[1].chunk_index, item[1].panel_index)))
    return ordered


_DEFAULT_PANELS_PER_CHUNK = 8


def panel_progress_counts(state: ProjectState, total_chunks: int | None = None) -> tuple[int, int]:
    """Return ``(finished, planned)`` panel counts for progress display."""
    if total_chunks is None:
        keys = {int(k) for k in state.chunk_cache if str(k).isdigit()}
        keys.update(int(k) for k in state.skipped_chunks if str(k).isdigit())
        total_chunks = (max(keys) + 1) if keys else 1
    total_chunks = max(1, total_chunks)

    planned = 0
    accounted: set[str] = set()
    for key in state.skipped_chunks:
        accounted.add(str(key))
    panel_counts: list[int] = []
    for key, cache in state.chunk_cache.items():
        if cache.storyboard is not None:
            n = len(cache.storyboard.panels)
            planned += n
            panel_counts.append(n)
            accounted.add(str(key))

    avg = (
        sum(panel_counts) / len(panel_counts) if panel_counts else float(_DEFAULT_PANELS_PER_CHUNK)
    )
    for i in range(total_chunks):
        if str(i) not in accounted:
            planned += avg

    finished = len(state.panels_done) + len(state.skipped)
    planned_i = max(finished, int(round(planned)))
    return finished, planned_i


def page_progress_counts(state: ProjectState, total_chunks: int | None = None) -> tuple[int, int]:
    """Return ``(finished, planned)`` finished-page counts for progress display."""
    if total_chunks is None:
        keys = {int(k) for k in state.page_cache if str(k).isdigit()}
        keys.update(int(k) for k in state.skipped_chunks if str(k).isdigit())
        total_chunks = (max(keys) + 1) if keys else 1
    total_chunks = max(1, total_chunks)

    planned = 0
    accounted: set[str] = set()
    for key in state.skipped_chunks:
        accounted.add(str(key))
    page_counts: list[int] = []
    for key, pageset in state.page_cache.items():
        n = len(pageset.pages)
        planned += n
        page_counts.append(n)
        accounted.add(str(key))

    avg = sum(page_counts) / len(page_counts) if page_counts else float(_DEFAULT_PANELS_PER_CHUNK)
    for i in range(total_chunks):
        if str(i) not in accounted:
            planned += avg

    finished = len(state.pages_done) + len(state.skipped_pages)
    planned_i = max(finished, int(round(planned)))
    return finished, planned_i


def estimate_progress(state: ProjectState, total_chunks: int | None = None) -> float:
    """Estimate overall completion in ``[0, 1)`` from checkpointed panels/pages.

    Used so a resume does not reset the UI progress bar to near-zero. Reserves
    the last 10% for layout/export.
    """
    if state.render_mode == "finished_page":
        finished, planned = page_progress_counts(state, total_chunks)
    else:
        finished, planned = panel_progress_counts(state, total_chunks)
    if planned <= 0:
        return 0.0
    return min(0.95, 0.9 * finished / planned)


@dataclass(frozen=True)
class ComicRunOptions:
    """One run's inputs, written once instead of three times.

    ``creative_comic``, ``_creative_comic`` and ``run_until_complete`` each
    carried the same eighteen-parameter list, so adding a knob meant editing
    three signatures in three files and hoping they stayed aligned. The field
    list here is the single definition; ``_creative_comic`` unpacks it into the
    local names the body already used, so no stage code had to change.

    ``chat`` / ``image`` are duck-typed providers rather than ABCs: the tests and
    the merge-cost probe pass fakes that only implement the two methods the
    pipeline calls.
    """

    source_txt: str
    output_dir: str
    project_id: str | None = None
    chat: object | None = None
    image: object | None = None
    style_guide: str | None = None
    output_format: str = "page"
    progress_callback: Callable[[str, float | None], None] | None = None
    panel_keys: list[str] | None = None
    cancel_check: Callable[[], bool] | None = None
    render_mode: str | None = None
    budget: BudgetSpec | None = None
    data_dir: str | Path | None = None
    budget_day: str | None = None
    gate_yes: bool = False
    sample_gate_size: int | None = None
    density: str | None = None
    harness_dir: str | Path | None = None


async def creative_comic(
    source_txt: str,
    *,
    output_dir: str,
    project_id: str | None = None,
    chat=None,
    image=None,
    style_guide: str | None = None,
    output_format: str = "page",
    progress_callback: Callable[[str, float | None], None] | None = None,
    panel_keys: list[str] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    render_mode: str | None = None,
    budget: BudgetSpec | None = None,
    data_dir: str | Path | None = None,
    budget_day: str | None = None,
    gate_yes: bool = False,
    sample_gate_size: int | None = None,
    density: str | None = None,
    harness_dir: str | Path | None = None,
) -> ComicProject:
    """Generate a project while holding its process-level mutation lock.

    The keyword signature is kept as the public contract: every caller (CLI,
    Web UI, supervisor, probe, examples) passes these names, and a run record is
    easier to read as a call than as a positional tuple.
    """
    options = ComicRunOptions(
        source_txt=source_txt,
        output_dir=output_dir,
        project_id=project_id,
        chat=chat,
        image=image,
        style_guide=style_guide,
        output_format=output_format,
        progress_callback=progress_callback,
        panel_keys=panel_keys,
        cancel_check=cancel_check,
        render_mode=render_mode,
        budget=budget,
        data_dir=data_dir,
        budget_day=budget_day,
        gate_yes=gate_yes,
        sample_gate_size=sample_gate_size,
        density=density,
        harness_dir=harness_dir,
    )
    with _project_lock(Path(output_dir)):
        return await _creative_comic(options)


def _required_budget_stages(mode: str, page_script: bool) -> set[str]:
    """Stages this render mode can actually charge.

    A custom BudgetSpec that omits one of these would otherwise look like
    exhaustion at the first call for that stage. Keep this in sync with every
    ``_charge`` site.
    """
    required = {"extract", "bible", "portrait", "render"}
    if mode == "finished_page":
        required.update({"beats", "page_plan"})
    else:
        required.add("storyboard")
        if page_script:
            required.add("page_script")
    return required


def _page_script_enabled() -> bool:
    return page_script_enabled()


async def _creative_comic(options: ComicRunOptions) -> ComicProject:
    """Generate a comic from ``source_txt`` into ``output_dir``.

    Args:
        source_txt: the full novel/scene text.
        output_dir: directory for panels, assets, pages, PDF, and state.json.
        project_id: identifier stored in state (defaults to the dir name).
        chat / image: provider instances (defaults from the factories).
        style_guide: optional global style appended to every panel prompt.
        output_format: ``"page"`` for a flip-page PDF (default), ``"cbz"`` for a
            zip of those pages, or ``"webtoon"`` for a single vertical strip PNG.
        progress_callback: optional ``callback(stage, percent)`` invoked during
            generation. ``percent`` is ``None`` when the stage has no reliable
            completion percentage; otherwise it ranges from 0.0 to 1.0.
        panel_keys: when set, only these panel state keys are (re)generated;
            layout/export still run over the full project. Ignored in
            ``finished_page`` mode.
        cancel_check: optional callable polled at chunk/panel checkpoints;
            raises ``PipelineCancelled`` when it returns true.
        render_mode: ``"finished_page"`` (default, one prompt/image per page,
            no LayoutEngine collage) or ``"panel_compose"`` (legacy
            storyboard -> panels -> LayoutEngine grid path). Falls back to
            ``core.config.render_mode()`` when unset.
        budget: when set, each billable call is charged against this
            reservation and a shortfall raises ``BudgetPaused`` before the
            call (§12). ``None`` preserves the unbounded run. Budget never
            enters a fingerprint.
        data_dir: shared directory for ``quota.jsonl`` and ``budget.json``.
            Defaults to ``core.config.data_dir()`` when ``budget`` is set.
        budget_day: calendar day (``YYYY-MM-DD``, UTC) the account uses.
            Tests pass this; production uses today.
        gate_yes: render past the sample gate. Audited once into
            ``runs.jsonl``. The gate never enters a fingerprint.
        sample_gate_size: pages confirmed before the remainder is released.
            ``None`` uses the default of 30, which a test holds below a
            whole book.
        density: planning tier ``A`` / ``B`` / ``C``. ``None`` is ``B``, the
            tier stored before this field existed. A different tier changes the
            structure fingerprint and the panel budget passed to planning.
        harness_dir: optional harness tree for the chat stages. ``None`` uses
            ``INKSTONE_HARNESS_DIR`` when set, otherwise the builtin tree. The
            tree is validated before the first billable call; it does not change
            the image renderer.

    Returns:
        A ``ComicProject`` with the final state, produced page paths, and PDF.
    """
    # Unpacked into the local names the body already used. Assigning 18 locals is
    # cheaper than rewriting 1,800 lines, and it keeps the closures below
    # (``_render_page_node`` and friends) closing over the same bindings.
    source_txt = options.source_txt
    output_dir = options.output_dir
    project_id = options.project_id
    chat = options.chat
    image = options.image
    style_guide = options.style_guide
    output_format = options.output_format
    progress_callback = options.progress_callback
    panel_keys = options.panel_keys
    cancel_check = options.cancel_check
    render_mode = options.render_mode
    budget = options.budget
    data_dir = options.data_dir
    budget_day = options.budget_day
    gate_yes = options.gate_yes
    sample_gate_size = options.sample_gate_size
    density = options.density
    harness_dir = options.harness_dir

    output_dir = Path(output_dir)
    clear_audit()
    (output_dir / "panels").mkdir(parents=True, exist_ok=True)
    (output_dir / "assets" / "portraits").mkdir(parents=True, exist_ok=True)
    pages_dir = output_dir / "pages"
    panel_key_filter = set(panel_keys) if panel_keys is not None else None
    mode = render_mode or config_render_mode()
    page_size = finished_page_size()
    registry_problems = validate_stage_registry()
    if registry_problems:
        raise ValueError("invalid stage registry: " + "; ".join(registry_problems))
    if budget is not None:
        required = _required_budget_stages(mode, _page_script_enabled())
        missing = budget.missing_stages(required)
        if missing:
            raise ValueError(f"budget does not fund required stages: {', '.join(sorted(missing))}")
    # Fail before the first billable call when the harness or its genre is
    # malformed. A broken component is a startup error, not a mid-book pause.
    harness_fallback = not harness_enabled()
    selected_harness_dir = (
        None
        if harness_fallback
        else (Path(harness_dir) if harness_dir is not None else config_harness_dir())
    )
    if harness_fallback:
        logger.warning(
            "INKSTONE_HARNESS=0: using the builtin harness and general genre; "
            "custom harness, genre, and craft lessons are disabled"
        )
    if selected_harness_dir is not None:
        try:
            run_harness = harness_from_dir(selected_harness_dir)
        except HarnessError as exc:
            raise ValueError(f"invalid harness: {exc}") from exc
        problems = run_harness.validate()
        if problems:
            raise ValueError("invalid harness: " + " | ".join(problems))
    else:
        validate_or_raise()
        run_harness = builtin_harness()
    run_genre = "general" if harness_fallback else config_genre()
    run_harness.genre_block(run_genre)

    perf = PerfCollector()

    def _progress_label(stage: str) -> str:
        if stage in ("panel", "panels"):
            done, planned = panel_progress_counts(state, total_chunks)
            return f"panels {done}/{planned}"
        if stage in ("page_plan", "page", "pages"):
            done, planned = page_progress_counts(state, total_chunks)
            return f"pages {done}/{planned}"
        return stage

    def _report(stage: str, percent: float | None = None) -> None:
        if progress_callback is not None:
            progress_callback(_progress_label(stage), percent)

    project_id = project_id or output_dir.name or "comic"
    session: BudgetSession | None = None
    if budget is not None:
        session = BudgetSession.open(
            data_dir=Path(data_dir) if data_dir is not None else config_data_dir(),
            project_id=project_id,
            output_dir=output_dir,
            spec=budget,
            today=budget_day,
        )

    def _charge(stage: str, item_key: str) -> None:
        # No-op when the caller did not pass a budget. A charge either commits
        # one call or raises BudgetPaused before the provider is touched.
        if session is not None:
            session.charge(stage, item_key)
        # Chat records itself from this binding. Image stages clear it so a
        # portrait or a page is not attributed to the chat item before it.
        # BudgetPaused above leaves the previous binding untouched.
        if stage in {"extract", "bible", "beats", "page_plan", "storyboard", "page_script"}:
            bind_audit(output_dir, stage, item_key)
        else:
            clear_audit()

    # The sample is decided as pages are reached: total_chunks exists only
    # after segmentation, and allow() is called from the render loops.
    gate = GateSession.open(
        data_dir=Path(data_dir) if data_dir is not None else None,
        output_dir=output_dir,
        project_id=project_id,
        yes=gate_yes,
        sample_size=SAMPLE_GATE_SIZE if sample_gate_size is None else sample_gate_size,
    )

    _report("init", 0.0)
    state_path = output_dir / "state.json"
    # Persist source so Web/CLI regen can resume without re-uploading text.
    (output_dir / "source.txt").write_text(
        source_txt.replace("\r\n", "\n").replace("\r", "\n"),
        encoding="utf-8",
    )
    image_config = ImageConfig()
    chat = chat or get_chat_provider()
    image = image or get_image_provider()
    snapshot = _model_snapshot(chat, image)
    l3_on = l3_enabled()
    # An omitted tier inherits the one the project was planned with. The flag
    # used to default to "B" all the way from argparse, which made "the caller
    # did not say" indistinguishable from "the caller asked for B": resuming a
    # tier C project without repeating --density read as a deliberate switch,
    # the structure fingerprint stopped matching, and every paid page was
    # redrawn. Only a fresh project falls back to B.
    if density is None and state_path.exists():
        try:
            density = ProjectState.load(state_path).density
        except Exception:  # noqa: BLE001 — a corrupt checkpoint is handled below
            logger.warning("could not read the stored tier from %s", state_path)
    density_plan = get_density_plan(_require_density(density))
    struct = _structure_fingerprint(
        source_txt,
        density=density_plan.tier,
        panels_per_chunk=density_plan.panels_per_chunk,
    )

    def _render_for_bible(visual_bible=None) -> str:
        bible_version, bible_hash = _bible_fingerprint_kwargs(visual_bible)
        return _render_fingerprint(
            style_guide,
            snapshot=snapshot,
            panel_continuity=image_config.panel_continuity,
            l3_enabled=l3_on,
            render_mode=mode,
            page_size=page_size,
            bible_version=bible_version,
            bible_hash=bible_hash,
        )

    def _fresh_state(*, carried: ProjectState | None = None) -> ProjectState:
        # Derived caches are rebuilt from the new source. The alias queue is a
        # human decision (§8 invariant 6) and is not among those caches.
        # Tombstones stay behind: they name pages of the discarded projection.
        fresh = ProjectState(
            project_id=project_id,
            source_file=str(output_dir),
            source_fingerprint=struct,
            structure_fingerprint=struct,
            render_fingerprint=_render_for_bible(),
            model_snapshot=snapshot,
        )
        if carried is not None:
            fresh.needs_review = list(carried.needs_review)
            fresh.dismissed_aliases = list(carried.dismissed_aliases)
            fresh.merged_aliases = list(carried.merged_aliases)
        return fresh

    soft_invalidated_this_run = False
    if state_path.exists():
        persisted = ProjectState.load(state_path)
        expected_render = _render_for_bible(persisted.visual_bible)
        if not persisted.structure_fingerprint:
            # Pre-split states carry no structure fingerprint. The legacy
            # combined fingerprint that used to adopt them in place is gone
            # (resolved item 21): they take the accepted one-time recompute,
            # and `carried` keeps the recorded human decisions.
            state = _fresh_state(carried=persisted)
        elif persisted.structure_fingerprint != struct:
            state = _fresh_state(carried=persisted)
        elif persisted.visual_bible is None:
            # ``cache.json`` is a hot copy, and the bible body lives in it. With
            # the file deleted the body is None, so a fingerprint recomputed from
            # it cannot match the recorded one — the comparison would read a lost
            # cache as a style change and repaint every page and portrait.
            #
            # The bible stage owns this decision instead, because only it knows
            # whether a manifest restore succeeded: see ``restored`` and the
            # ``prev_hash is None and had_render_assets`` guard below, which
            # invalidates exactly when the body could not be restored. A project
            # that genuinely has no bible has no render assets to protect.
            state = persisted
        elif persisted.render_fingerprint != expected_render:
            state = persisted
            _soft_invalidate_render(state)
            soft_invalidated_this_run = True
            state.render_fingerprint = expected_render
            state.model_snapshot = snapshot
        else:
            state = persisted
    else:
        state = _fresh_state()
    # A stamp describes a bible body. If the body is gone, the stamps are
    # leftovers from the run that wrote it; clear them before the bible stage
    # reads them. §Task 2 step 3.
    if clear_stale_bible_stamps(state):
        state.save(state_path)
    state.density = density_plan.tier
    state.panels_per_chunk = density_plan.panels_per_chunk
    state.source_units = source_units(source_txt)
    if soft_invalidated_this_run and panel_key_filter is not None:
        logger.info("render fingerprint changed: ignoring panel_keys filter, redrawing all panels")
        panel_key_filter = None
    state.project_id = project_id
    state.render_mode = mode
    # Phase 0d: the consistency ledger is loaded (or rebuilt from page_cache)
    # once per run and kept in sync below. It enters no key or fingerprint.
    ledger_path = output_dir / "consistency.json"
    ledger = ConsistencyLedger.load_or_rebuild(ledger_path, state)
    if _bind_missing_portrait_hashes(ledger, output_dir):
        ledger.save(ledger_path)

    image_semaphore = asyncio.Semaphore(image_config.image_concurrency)
    engine = ConsistencyEngine()

    _reconcile_state(state, state_path, output_dir)

    if sanitize_visual_bible_state(state):
        _soft_invalidate_render(state)
        state.render_fingerprint = _render_for_bible(state.visual_bible)
        state.save(state_path)

    with perf.measure("segment"):
        chunks = segment_text(source_txt)
    sync_adaptation_plans(state, chunks)
    total_chunks = len(chunks) or 1

    review = ReviewPacket(
        output_dir,
        project_id=project_id,
        enabled=review_packet_enabled(),
    )
    review.start(
        source=source_txt,
        state=state,
        genre=run_genre,
        harness_digest=run_harness.digest(),
        render_mode=mode,
        density=density_plan.tier,
        harness_fallback=harness_fallback,
    )
    lesson_store = LessonStore(config_data_dir()) if lessons_enabled() else None

    def _pct() -> float:
        return estimate_progress(state, total_chunks)

    # Resume: jump the bar to checkpointed completion instead of restarting at 0.
    _report("resume" if (state.panels_done or state.chunk_cache) else "segment", _pct())
    prev_panel_local: str | None = None

    plan_graph = _build_plan_graph(len(chunks))
    plan_graph.validate()

    def _plan_work_items():
        completed: set[str] = set()
        while ready := plan_graph.ready(completed, PipelinePhase.PLAN):
            node = ready[0]
            assert node.unit_index is not None
            yield node
            # Reaching this point means the chunk executor returned or took an
            # intentional skip path. Propagated operational errors bypass it.
            completed.add(node.node_id)

    # Plan every chunk before drawing, then expand render work from the plans.
    # The page count and the panel layout are known only after the plan pass;
    # the graph and its node ids are ephemeral and never become persisted keys.
    render_graph = StageGraph()
    render_nodes_by_chunk: dict[int, tuple[WorkNode, ...]] = {}
    batch_panel_keys: dict[str, tuple[str, ...]] = {}
    completed_render_nodes: set[str] = set()

    def _require_render_node(node: WorkNode) -> None:
        if node not in render_graph.ready(completed_render_nodes, PipelinePhase.RENDER):
            raise RuntimeError(f"render node {node.node_id} is not ready for dispatch")

    def _complete_render_chunk_nodes(chunk_index: int) -> None:
        """Advance ordered render nodes when the existing chunk path skips them."""
        for node in render_nodes_by_chunk.get(chunk_index, ()):
            _require_render_node(node)
            completed_render_nodes.add(node.node_id)

    def _schedule_export(node_id: str, *, dependencies: tuple[str, ...] = ()) -> WorkNode:
        """Add one finalization step behind completed render work.

        The export graph node is ephemeral and exists only for this run; the
        helper raises instead of silently skipping finalization when any
        render node is still incomplete.
        """
        node = WorkNode(
            node_id=node_id,
            phase=PipelinePhase.EXPORT,
            kind=WorkKind.EXPORT,
            unit_index=None,
            item_index=None,
            dependencies=dependencies,
        )
        render_graph.add(node)
        render_graph.validate()
        if node not in render_graph.ready(completed_render_nodes, PipelinePhase.EXPORT):
            raise RuntimeError(f"export node {node_id} is not ready for dispatch")
        return node

    for phase in ("plan", "render"):
        prev_panel_local = None
        if phase == "plan":
            work_items = ((node.unit_index, chunks[node.unit_index]) for node in _plan_work_items())
        else:
            # §12 invariant 11 (phase 5b): before any render work is expanded
            # or dispatched, a character whose pending pages exceed the
            # escalation threshold holds the batch for a human confirmation.
            # Below the threshold the flag stays quiet — the ledger and the
            # aggregated UI panel only, no stop, no pause.
            if ledger.rebuild_from_state(state):
                ledger.save(ledger_path)
            escalated = ledger.escalated()
            if escalated:
                ledger.record_escalation_stop(output_dir)
                state.save(state_path)
                raise ContinuityReviewPaused(escalated)
            if mode == "finished_page":
                render_nodes_by_chunk = _expand_page_render_nodes(render_graph, state, len(chunks))
            else:
                render_nodes_by_chunk, batch_panel_keys = _expand_panel_render_nodes(
                    render_graph,
                    state,
                    len(chunks),
                    continuity=image_config.panel_continuity,
                )
            render_graph.validate()
            work_items = enumerate(chunks)
        for ci, chunk in work_items:
            assert ci is not None
            check_cancel(cancel_check)
            key = str(ci)
            if key in state.skipped_chunks:
                logger.info("chunk %s skipped: previously rejected by content filter", key)
                _complete_render_chunk_nodes(ci)
                _report("skip", _pct())
                continue
            cached = state.chunk_cache.get(key)
            board = cached.storyboard if cached else None
            elements = cached.elements if cached else None
            extract_request = build_extract_request(chunk, harness=run_harness, model=snapshot.chat)
            review.record("extract", ci, extract_request)
            extract_key = extract_action_key(
                chunk=chunk,
                env=snapshot.chat,
                stage_src=_EXTRACT_STAGE_SRC,
                request_digest=extract_request.digest(),
            )
            if elements is not None and cached is not None:
                if cached.extract_action_key and cached.extract_action_key != extract_key:
                    elements = None
                elif not cached.extract_action_key:
                    cached.extract_action_key = extract_key
            pageset = state.page_cache.get(key)
            if _scrub_chunk_lettering(state, key, chunk):
                state.save(state_path)
            known_beats = key_beats_for_chunk(state, key, chunks)
            if board is not None and known_beats is not None:
                if stamp_storyboard_beats(board, known_beats):
                    state.save(state_path)
            if mode == "finished_page" and pageset is not None:
                cached_beats = state.beat_cache.get(key)
                if cached_beats is not None and cached_beats.beats:
                    if stamp_page_panel_beats(pageset, cached_beats):
                        state.save(state_path)

            # Fully planned chunk: cached, marked done, every panel/page present on
            # disk. Re-running reuses the cache so the billable chat API is never
            # re-called.
            if mode == "finished_page":
                chunk_complete = (
                    pageset is not None
                    and key in set(state.chunks_done)
                    and _page_chunk_complete(state, pageset, output_dir, ci, chunk)
                    and panel_key_filter is None
                )
                if chunk_complete:
                    # The panel branch below does not require a visual bible,
                    # and a finished page does not read one: both resume paths
                    # must short-circuit on the same evidence.
                    _complete_render_chunk_nodes(ci)
                    _report("resume", _pct())
                    continue
            elif (
                board is not None
                and key in set(state.chunks_done)
                and _chunk_complete(state, board, output_dir, ci)
                and panel_key_filter is None
            ):
                script_missing, _script_key = _page_script_plan(
                    cached, chunk, snapshot.chat, harness=run_harness
                )
                if not script_missing:
                    if image_config.panel_continuity and board.panels:
                        last_index = len(board.panels) - 1
                        state_key = _stored_panel_key(state, ci, last_index)
                        prev_panel_local = state.generated.panels[state_key].local
                    _complete_render_chunk_nodes(ci)
                    _report("resume", _pct())
                    continue

            # extraction (only when not cached)
            fresh_extract = False
            if elements is None:
                elements = _restore_stage_body(output_dir, extract_key, "extract", StoryElements)
                if elements is None:
                    fresh_extract = True
                    state.stage = "extract"
                    _charge("extract", f"c{ci:04d}")
                    try:
                        with perf.measure("extract"):
                            elements = await extract_story_elements(
                                chunk, chat=chat, request=extract_request
                            )
                    except Exception as exc:  # noqa: BLE001 — content rejections must not abort the run
                        if is_content_policy_rejection(exc):
                            logger.warning(
                                "chunk %s skipped: content filter rejected extraction (%s)", ci, exc
                            )
                            if key not in state.skipped_chunks:
                                state.skipped_chunks.append(key)
                            state.save(state_path)
                            _report("skip", _pct())
                            continue
                        raise
                    _record_stage_body(
                        output_dir,
                        "extract",
                        extract_key,
                        elements,
                        stage_src=_EXTRACT_STAGE_SRC,
                        env=snapshot.chat,
                        inputs=extract_inputs(chunk),
                    )
                # Cache so a later resume does not re-pay for extraction.
                state.chunk_cache.setdefault(key, ChunkCache()).elements = elements
                state.chunk_cache[key].extract_action_key = extract_key
                state.save(state_path)
                _report("extract", _pct())

            # Merge characters; generate a portrait only for first-seen names.
            state.characters, new_names = merge_characters(state.characters, elements.characters)
            apply_recorded_merges(state, ledger)
            # A name folded by a recorded merge is not a new character, so it does
            # not pay for another portrait or another bible reconcile.
            new_names = [name for name in new_names if name in state.characters]
            planned_elements = elements_with_recorded_merges(elements, state)
            for name in new_names:
                if name not in state.characters:
                    continue
                ensure_character_l1(state.characters[name], source_text=chunk)
            state.settings = merge_settings(state.settings, elements.settings)

            # Surface likely alias variants for human review (never auto-merged).
            hints = list(detect_character_aliases(state.characters, new_names))
            for name, cand, reason in hints:
                offer_alias_suggestion(state, suggestion_from_alias(name, cand, reason))
            # The character table, the bible prose, and the project style are
            # deliberately pinned out of the key request: a recorded merge or a
            # style change must not make every earlier chunk reconcile again.
            # The digest still covers the system prompt, stage instructions,
            # tool schema, and chunk.
            bible_key_request = build_bible_request(
                chunk,
                {},
                None,
                alias_hints=(),
                # preferred_style is not a declared hard input for bible: a
                # project style change must not re-reconcile every chunk.
                preferred_style="",
                harness=run_harness,
                model=snapshot.chat,
            )
            bible_key = bible_action_key(
                chunk=chunk,
                history=historical_identity("bible"),
                env=snapshot.chat,
                stage_src=_BIBLE_STAGE_SRC,
                request_digest=bible_key_request.digest(),
            )
            stored_bible_key = state.bible_action_keys.get(key, "")
            needs_bible = state.visual_bible is None or fresh_extract or bool(new_names)
            recon: VisualBibleReconcileResult | None = None
            restored = False
            if not needs_bible:
                if stored_bible_key == bible_key:
                    pass
                elif stored_bible_key and stored_bible_key != bible_key:
                    needs_bible = True
                else:
                    recon = _restore_stage_body(
                        output_dir, bible_key, "bible", VisualBibleReconcileResult
                    )
                    if recon is not None:
                        restored = True
                    else:
                        state.bible_action_keys[key] = bible_key
            if needs_bible:
                recon = _restore_stage_body(
                    output_dir, bible_key, "bible", VisualBibleReconcileResult
                )
                if recon is not None:
                    restored = True
                else:
                    _charge("bible", f"c{ci:04d}")
                    try:
                        bible_request = build_bible_request(
                            chunk,
                            state.characters,
                            state.visual_bible,
                            alias_hints=hints,
                            preferred_style=style_guide or elements.style_guide,
                            harness=run_harness,
                            model=snapshot.chat,
                        )
                        review.record("bible", ci, bible_request)
                        recon = await reconcile_visual_bible(
                            chunk,
                            state.characters,
                            state.visual_bible,
                            alias_hints=hints,
                            preferred_style=style_guide or elements.style_guide,
                            chat=chat,
                            request=bible_request,
                        )
                    except Exception as exc:  # noqa: BLE001 — reconcile is best-effort
                        logger.warning("visual bible reconcile failed (%s); continuing", exc)
                        recon = None
                    if recon is not None:
                        _record_stage_body(
                            output_dir,
                            "bible",
                            bible_key,
                            recon,
                            stage_src=_BIBLE_STAGE_SRC,
                            env=snapshot.chat,
                            inputs=bible_inputs(chunk),
                        )
            if recon is not None:
                prev_hash = state.visual_bible.content_hash if state.visual_bible else None
                had_render_assets = bool(
                    state.generated.pages
                    or state.generated.portraits
                    or state.pages_done
                    or state.panels_done
                )
                state = apply_reconcile(state, recon)
                sync_characters_from_bible(state)
                sanitized = sanitize_visual_bible_state(state)
                # A restored body is the reconcile result the original run
                # recorded, so it carries that run's pre-sanitize shape and
                # sanitize mutates it again — filling locks, inferring the era,
                # bumping to v3. That is a replay, not drift: the assets on disk
                # were already drawn from the sanitized version of this exact
                # body. Invalidation would repaint the book for a normalisation
                # that already happened once.
                if sanitized and not restored:
                    _soft_invalidate_render(state)
                elif state.visual_bible:
                    state.visual_bible = refresh_bible_hash(state.visual_bible)
                    if prev_hash and state.visual_bible.content_hash != prev_hash:
                        _soft_invalidate_render(state)
                    elif not restored and prev_hash is None and had_render_assets:
                        _soft_invalidate_render(state)
                if state.visual_bible:
                    new_render = _render_for_bible(state.visual_bible)
                    if state.render_fingerprint != new_render:
                        state.render_fingerprint = new_render
                state.bible_action_keys[key] = bible_key
            if state.visual_bible and state.visual_bible.style_guide:
                effective_style = state.visual_bible.style_guide
            else:
                effective_style = style_guide or elements.style_guide
            portrait_style = effective_style

            async def _render_portrait(
                name: str,
                *,
                style: str = portrait_style,
                _state: ProjectState = state,
                _chunk: str = chunk,
            ) -> tuple[str, str]:
                asset = resolve_character_asset(name, _state.characters, _state.visual_bible)
                if asset is None:
                    asset = _state.characters[name]
                ensure_character_l1(asset, source_text=_chunk)
                prompt = asset.portrait_prompt or asset.l1_prompt
                if _state.visual_bible is not None:
                    base, stage = parse_stage_ref(name)
                    canon = _state.visual_bible.characters.get(base)
                    if canon is None:
                        base = resolve_canonical_name(base, _state.visual_bible)
                        canon = _state.visual_bible.characters.get(base)
                    if canon is not None:
                        canon_prompt = l1_from_canon(canon, stage)
                        if canon_prompt:
                            prompt = canon_prompt
                        suffix = portrait_gender_era_suffix(_state.visual_bible, canon)
                        prompt = f"{prompt}, {suffix}"
                prompt = harden_human_identity_prompt(name, prompt)
                if _state.visual_bible is not None:
                    color_block = format_color_bible_block(_state.visual_bible)
                    if color_block:
                        prompt = f"{prompt}, {color_block}"
                comic_style = (
                    f"{style}, {DEFAULT_PORTRAIT_STYLE}" if style else DEFAULT_PORTRAIT_STYLE
                )
                refs: list[str] = []
                base_ref = canonical_portrait_ref(name, _state.characters, _state.visual_bible)
                if base_ref and _is_within(base_ref, output_dir) and Path(base_ref).is_file():
                    refs = [base_ref]
                async with image_semaphore:
                    _charge("portrait", name)
                    sent = f"{prompt}, {comic_style}"
                    image_intent(
                        output_dir,
                        stage="portrait",
                        key=name,
                        prompt=sent,
                        size="1024x1024",
                    )
                    try:
                        with perf.measure("portrait"):
                            out = await image.generate_single_image(
                                sent,
                                reference_image_paths=refs,
                                size="1024x1024",
                            )
                    except Exception as exc:  # noqa: BLE001 — log the class, then keep the portrait error
                        image_failure(output_dir, stage="portrait", key=name, error=exc)
                        raise
                path = _asset_path(output_dir, "assets/portraits", name)
                await asyncio.to_thread(out.save, str(path))
                asset.portrait_action_key = portrait_action_key(
                    name=name,
                    material=_portrait_key_material(name, asset, style),
                    env=snapshot.t2i,
                    stage_src=_PORTRAIT_STAGE_SRC,
                )
                return name, str(path)

            def _portrait_file_ok(asset) -> bool:
                return bool(
                    asset.portrait_local
                    and _is_within(asset.portrait_local, output_dir)
                    and Path(asset.portrait_local).is_file()
                )

            portrait_names = []
            for name, asset in state.characters.items():
                # The key must be computed from the same material ``_render_portrait``
                # will stamp, and that function normalises the prompt first
                # (``ensure_character_l1`` appends the evidence markers). Skipping it
                # here compares an unmarked prompt against a marked stamp: a restored
                # bible rewrites ``l1_prompt`` from the canon, so the check would miss
                # on every resume and repaint a portrait whose bytes already match.
                ensure_character_l1(asset, source_text=chunk)
                material = _portrait_key_material(name, asset, portrait_style)
                key_for_portrait = portrait_action_key(
                    name=name,
                    material=material,
                    env=snapshot.t2i,
                    stage_src=_PORTRAIT_STAGE_SRC,
                )
                if _portrait_file_ok(asset) and asset.portrait_action_key == key_for_portrait:
                    continue
                if _portrait_file_ok(asset) and not asset.portrait_action_key:
                    asset.portrait_action_key = key_for_portrait
                    continue
                portrait_names.append(name)
            base_names = [name for name in portrait_names if "@" not in name]
            stage_names = [name for name in portrait_names if "@" in name]
            portrait_results: list = []
            assigned_names: list[str] = []
            for group in (base_names, stage_names):
                group_results = await asyncio.gather(
                    *(_render_portrait(name) for name in group),
                    return_exceptions=True,
                )
                for name, result in zip(group, group_results, strict=True):
                    assigned_names.append(name)
                    portrait_results.append(result)
                    if isinstance(result, BudgetPaused):
                        state.save(state_path)
                        raise result
                    if isinstance(result, Exception):
                        continue
                    _, path = result
                    state.characters[name].portrait_local = path
                    state.generated.portraits[name] = path
                    # Phase 0d (§12 consequence 1): record the authoritative
                    # reference. A first-ever portrait establishes the version
                    # without flagging pages we cannot yet enumerate; a
                    # regeneration bumps the version so rebuild marks the
                    # character's pages pending for review.
                    ledger_name = name.split("@", 1)[0]
                    if not ledger.pages_for(ledger_name):
                        _record_portrait_reference(ledger, output_dir, ledger_name, path)
                        ledger.characters[ledger_name].reviewed_version = ledger.characters[
                            ledger_name
                        ].reference.version
                    else:
                        _record_portrait_reference(ledger, output_dir, ledger_name, path)
                    _report("portrait", _pct())

            policy_rejection: Exception | None = None
            operational_error: Exception | None = None
            for _name, result in zip(assigned_names, portrait_results, strict=True):
                if isinstance(result, Exception):
                    if is_content_policy_rejection(result):
                        policy_rejection = result
                    elif operational_error is None:
                        operational_error = result
                    continue

            if operational_error is not None:
                state.save(state_path)
                raise operational_error
            if policy_rejection is not None:
                logger.warning(
                    "chunk %s skipped: content filter rejected portrait (%s)", ci, policy_rejection
                )
                if key not in state.skipped_chunks:
                    state.skipped_chunks.append(key)
                state.save(state_path)
                _report("skip", _pct())
                continue
            state.save(state_path)
            _report("portrait", _pct())

            if mode == "finished_page":
                if pageset is not None and state.visual_bible is not None:
                    pageset = rewrite_pageset_from_bible(pageset, state.visual_bible)
                beats_request = build_beats_request(
                    chunk, elements, harness=run_harness, model=snapshot.chat
                )
                review.record("beats", ci, beats_request)
                beats_key_request = build_beats_request(
                    chunk, StoryElements(), harness=run_harness, model=snapshot.chat
                )
                beats_key = beats_action_key(
                    chunk=chunk,
                    extract_key=extract_key,
                    env=snapshot.chat,
                    stage_src=_BEATS_STAGE_SRC,
                    request_digest=beats_key_request.digest(),
                )
                cached_beats = state.beat_cache.get(key)
                if cached_beats is None:
                    restored_beats = _restore_stage_body(output_dir, beats_key, "beats", KeyBeatSet)
                    # An empty set on disk is evidence that one extraction
                    # answered with nothing, not that this chunk has no beats.
                    # Older runs recorded it as a stage body, so restoring it
                    # would make that answer permanent for the project. Leaving
                    # it out re-extracts, which is what the stage would do on a
                    # miss.
                    if cacheable_beats(restored_beats) is not None:
                        restored_beats.action_key = beats_key
                        state.beat_cache[key] = restored_beats
                        cached_beats = restored_beats
                if cached_beats is not None:
                    if cached_beats.action_key and cached_beats.action_key != beats_key:
                        state.beat_cache.pop(key, None)
                        pageset = None
                    else:
                        _stamp_action_key(state, state_path, cached_beats, beats_key)
                # The recent layout strings and the bible body are accumulated
                # context: they must not cascade a key through the rest of the
                # book. The key request pins them out; the sent request keeps them.
                plan_key_request = build_page_plan_request(
                    chunk,
                    StoryElements(),
                    recent_layouts=[],
                    visual_bible=None,
                    key_beats=state.beat_cache.get(key),
                    panel_budget=density_plan.panels_per_chunk,
                    enforce_budget=clips_plan(density_plan.tier),
                    tier=density_plan.tier,
                    genre=run_genre,
                    harness=run_harness,
                    model=snapshot.chat,
                )
                plan_key = _page_plan_key(
                    chunk,
                    state.beat_cache.get(key),
                    snapshot.chat,
                    beats_key,
                    request_digest=plan_key_request.digest(),
                )
                if pageset is not None:
                    if pageset.action_key and pageset.action_key != plan_key:
                        pageset = None
                    else:
                        _stamp_action_key(state, state_path, pageset, plan_key)
                if pageset is None:
                    restored_plan = _restore_stage_body(
                        output_dir, plan_key, "page_plan", ComicPagePlanSet
                    )
                    if restored_plan is not None:
                        restored_plan.action_key = plan_key
                        state.page_cache[key] = restored_plan
                        pageset = restored_plan
                if pageset is None:
                    state.stage = "page_plan"
                    try:
                        with perf.measure("page_plan"):
                            beats = state.beat_cache.get(key)
                            if beats is None:
                                _charge("beats", f"c{ci:04d}")
                                try:
                                    beats = await extract_key_beats(
                                        chunk,
                                        elements,
                                        chat=chat,
                                        request=beats_request,
                                    )
                                    beats.action_key = beats_key
                                    # An empty set is not this chunk's beats.
                                    # Caching it would make one bad answer the
                                    # chunk's permanent truth: reused for the rest
                                    # of the run and restored by every later run.
                                    # Keeping it out of the cache leaves the stage
                                    # retryable, which is what it was before.
                                    if cacheable_beats(beats) is None:
                                        logger.warning(
                                            "chunk %s key-beat extract returned no beats; "
                                            "not caching, so the stage can be retried",
                                            ci,
                                        )
                                    else:
                                        state.beat_cache[key] = beats
                                        sync_adaptation_plans(state, chunks)
                                        _record_stage_body(
                                            output_dir,
                                            "beats",
                                            beats_key,
                                            beats,
                                            stage_src=_BEATS_STAGE_SRC,
                                            env=snapshot.chat,
                                            inputs=[extract_key],
                                        )
                                except Exception as beat_exc:  # noqa: BLE001
                                    logger.warning(
                                        "chunk %s key-beat extract failed (%s); continuing",
                                        ci,
                                        beat_exc,
                                    )
                                    beats = None
                            _charge("page_plan", f"c{ci:04d}")
                            craft_notes = ""
                            if lesson_store is not None:
                                query = chunk
                                if beats is not None and beats.beats:
                                    query += "\n" + "\n".join(
                                        beat.summary for beat in beats.beats if beat.summary
                                    )
                                craft_notes = format_craft_notes(
                                    lesson_store.retrieve(
                                        genre=run_genre,
                                        query=query,
                                        applies_to="page_plan",
                                    )
                                )
                            plan_request = build_page_plan_request(
                                chunk,
                                planned_elements,
                                recent_layouts=_recent_layout_intents(state),
                                visual_bible=state.visual_bible,
                                key_beats=beats,
                                panel_budget=density_plan.panels_per_chunk,
                                enforce_budget=clips_plan(density_plan.tier),
                                tier=density_plan.tier,
                                genre=run_genre,
                                craft_notes=craft_notes,
                                harness=run_harness,
                                model=snapshot.chat,
                            )
                            review.record("page_plan", ci, plan_request)
                            pageset = await plan_comic_pages(
                                chunk,
                                planned_elements,
                                chat=chat,
                                recent_layouts=_recent_layout_intents(state),
                                visual_bible=state.visual_bible,
                                key_beats=beats,
                                panel_budget=density_plan.panels_per_chunk,
                                enforce_budget=clips_plan(density_plan.tier),
                                tier=density_plan.tier,
                                request=plan_request,
                            )
                            if beats is not None:
                                missing = uncovered_must_draw_beats(beats, pageset)
                                if missing:
                                    logger.warning(
                                        "chunk %s uncovered must_draw beats; "
                                        "retrying page plan once",
                                        ci,
                                    )
                                    _charge("page_plan", f"c{ci:04d}")
                                    pageset = await plan_comic_pages(
                                        chunk,
                                        planned_elements,
                                        chat=chat,
                                        recent_layouts=_recent_layout_intents(state),
                                        visual_bible=state.visual_bible,
                                        key_beats=beats,
                                        extra_user_note=beat_coverage_retry_note(missing),
                                        panel_budget=density_plan.panels_per_chunk,
                                        enforce_budget=clips_plan(density_plan.tier),
                                        tier=density_plan.tier,
                                    )
                    except Exception as exc:  # noqa: BLE001 — content rejections must not abort the run
                        if is_content_policy_rejection(exc):
                            logger.warning(
                                "chunk %s skipped: content filter rejected page plan (%s)", ci, exc
                            )
                            if key not in state.skipped_chunks:
                                state.skipped_chunks.append(key)
                            state.save(state_path)
                            _report("skip", _pct())
                            continue
                        raise
                    if state.visual_bible is not None:
                        pageset = rewrite_pageset_from_bible(pageset, state.visual_bible)
                    stored_plan_key_request = build_page_plan_request(
                        chunk,
                        StoryElements(),
                        recent_layouts=[],
                        visual_bible=None,
                        key_beats=state.beat_cache.get(key),
                        panel_budget=density_plan.panels_per_chunk,
                        enforce_budget=clips_plan(density_plan.tier),
                        tier=density_plan.tier,
                        genre=run_genre,
                        harness=run_harness,
                        model=snapshot.chat,
                    )
                    pageset.action_key = _page_plan_key(
                        chunk,
                        state.beat_cache.get(key),
                        snapshot.chat,
                        beats_key,
                        request_digest=stored_plan_key_request.digest(),
                    )
                    state.page_cache[key] = pageset
                    _record_stage_body(
                        output_dir,
                        "page_plan",
                        pageset.action_key,
                        pageset,
                        stage_src=_PAGE_PLAN_STAGE_SRC,
                        env=snapshot.chat,
                        inputs=[beats_key],
                    )
                    _drop_pages_outside_plan(state, chunk_index=ci, page_count=len(pageset.pages))
                    state.save(state_path)
                    _report("page_plan", _pct())
                if pageset is not None:
                    review.record_plan("page_plan", ci, pageset.model_dump(mode="json"))

                cached_beats = state.beat_cache.get(key)
                if pageset is not None and cached_beats is not None and cached_beats.beats:
                    stamp_page_panel_beats(pageset, cached_beats)

                # Model-structured output sometimes reuses page_id across chunks.
                # State keys are the raw page_id, so remint duplicates rather than
                # silently overwriting an earlier page's generated record.
                seen_page_ids: set[str] = set()
                for plan in pageset.pages:
                    if plan.page_id in seen_page_ids:
                        original = plan.page_id
                        n = 2
                        while f"{original}__{n}" in seen_page_ids:
                            n += 1
                        plan.page_id = f"{original}__{n}"
                        logger.warning(
                            "duplicate page_id %r in page plan chunk %s; remapped to %r",
                            original,
                            ci,
                            plan.page_id,
                        )
                    seen_page_ids.add(plan.page_id)

                if phase == "plan":
                    continue

                state.stage = "pages"
                check_cancel(cancel_check)

                async def _render_page_node(
                    page_index: int,
                    plan: ComicPagePlan,
                    *,
                    _state: ProjectState = state,
                    _chunk: str = chunk,
                    _pageset: ComicPagePlanSet = pageset,
                    _chunk_index: int = ci,
                    _style: str = effective_style,
                ) -> None:
                    plan = _plan_ready_to_letter(_state, plan)
                    page_id = plan.page_id
                    state_key = page_state_key(_chunk_index, page_index)
                    existing = _state.generated.pages.get(state_key)
                    blank_ok = bool(
                        existing
                        and existing.blank_local
                        and _is_within(existing.blank_local, output_dir)
                        and Path(existing.blank_local).is_file()
                    )
                    # A stale page was invalidated for a *content* reason (e.g. an
                    # alias merge changed the character's identity), so its cached
                    # blank art is wrong and must be repainted. A font or lettering
                    # change reuses that blank: the letter key misses, and only the
                    # overlay is drawn again.
                    page_is_stale = state_key in _state.stale_pages
                    if blank_ok and existing is not None and not page_is_stale:
                        local = _page_asset_path(pages_dir, _chunk_index, page_index)
                        status = _letter_resume_status(
                            Path(existing.blank_local),
                            local,
                            plan,
                            _chunk,
                            output_dir,
                            resolved_size=existing.resolved_size or None,
                        )
                        if status == "tombstone":
                            return
                        if status != "current" or existing.lettering_version != LETTERING_VERSION:
                            pages_dir.mkdir(parents=True, exist_ok=True)
                            await asyncio.to_thread(
                                partial(
                                    _letter_page_from_blank,
                                    Path(existing.blank_local),
                                    local,
                                    plan,
                                    source_text=_chunk,
                                    output_dir=output_dir,
                                    resolved_size=existing.resolved_size or None,
                                )
                            )
                            existing.local = str(local)
                            existing.mode = "finished_lettered"
                            existing.lettering_version = LETTERING_VERSION
                            record_page_artifact(_state, state_key, existing)
                            # Projection-only: no heavy cache changed since the
                            # last full save, so this stays off cache.json.
                            _state.save(state_path, caches=False)
                            _report("pages", _pct())
                        return
                    if not _page_needs_generation(_state, state_key):
                        return
                    check_cancel(cancel_check)
                    prev_blank_path = previous_page_blank(
                        _state, _pageset, chunk_index=_chunk_index, page_index=page_index
                    )
                    prompt = render_finished_page_prompt(
                        plan,
                        characters_by_name=_state.characters,
                        settings_by_name=_state.settings,
                        style_guide=_style,
                        visual_bible=_state.visual_bible,
                    )
                    if _state.visual_bible is not None:
                        ledger_refs = {
                            name: entry.reference.path
                            for name, entry in ledger.characters.items()
                            if entry.reference.path and Path(entry.reference.path).is_file()
                        }
                        refs = collect_finished_page_refs(
                            plan,
                            _state.characters,
                            _state.visual_bible,
                            prev_blank=prev_blank_path,
                            reference_paths=ledger_refs,
                            panel_continuity=image_config.panel_continuity,
                        )
                    else:
                        refs = [
                            _state.characters[name].portrait_local
                            for name in _page_reference_names(plan)
                            if name in _state.characters and _state.characters[name].portrait_local
                        ]
                    refs = [
                        ref for ref in refs if _is_within(ref, output_dir) and Path(ref).is_file()
                    ]
                    ref_policy = finished_page_ref_policy(
                        panel_continuity=image_config.panel_continuity
                    )
                    render_env = h_env(text=prompt, resolved_size=page_size, scope="render")
                    render_key = render_page_action_key(
                        plan_json=_plan_json(plan),
                        prompt=prompt,
                        resolved_size=page_size,
                        page_key=state_key,
                        ref_policy=ref_policy,
                        env=render_env,
                        stage_src=_RENDER_PAGE_STAGE_SRC,
                    )
                    recorded_render = read_manifest_entry(
                        output_dir, render_key, stage="render.page"
                    )
                    if recorded_render is not None and recorded_render.outcome != "ok":
                        return
                    if state_key not in _state.stale_pages:
                        cached_blank = stored_output(output_dir, render_key, stage="render.page")
                        if cached_blank is not None:
                            pages_dir.mkdir(parents=True, exist_ok=True)
                            blank_dir = pages_dir / "blank"
                            blank_dir.mkdir(parents=True, exist_ok=True)
                            blank_path = _page_asset_path(blank_dir, _chunk_index, page_index)
                            blank_path.write_bytes(cached_blank)
                            local = _page_asset_path(pages_dir, _chunk_index, page_index)
                            if (
                                _letter_resume_status(
                                    blank_path,
                                    local,
                                    plan,
                                    _chunk,
                                    output_dir,
                                    resolved_size=page_size,
                                )
                                == "tombstone"
                            ):
                                return
                            await asyncio.to_thread(
                                partial(
                                    _letter_page_from_blank,
                                    blank_path,
                                    local,
                                    plan,
                                    source_text=_chunk,
                                    output_dir=output_dir,
                                    resolved_size=page_size,
                                )
                            )
                            record_page_artifact(
                                _state,
                                state_key,
                                GeneratedPage(
                                    local=str(local),
                                    blank_local=str(blank_path),
                                    lettering_version=LETTERING_VERSION,
                                    resolved_size=page_size,
                                    render_action_key=render_key,
                                    page_id=page_id,
                                    unit_index=_chunk_index,
                                    page_index=page_index,
                                    mode="finished_lettered",
                                ),
                            )
                            _state.save(state_path, caches=False)
                            _report("pages", _pct())
                            return
                    # Hold past the sample without raising, so a later chunk can
                    # still take the reserved seat. The pause is raised once,
                    # after the sample has been exported.
                    if not gate.allow(state_key, _chunk_index, total_chunks=total_chunks):
                        return
                    stricter_attempted = False
                    size_fallback_attempted = False
                    active_size = page_size
                    while True:
                        _charge("render", state_key)
                        image_intent(
                            output_dir,
                            stage="render",
                            key=state_key,
                            prompt=prompt,
                            size=active_size,
                        )
                        try:
                            async with image_semaphore:
                                with perf.measure("page"):
                                    out = await image.generate_single_image(
                                        prompt, reference_image_paths=refs, size=active_size
                                    )
                            break
                        except Exception as exc:  # noqa: BLE001 — preserve policy skip behavior
                            image_failure(output_dir, stage="render", key=state_key, error=exc)
                            if is_content_policy_rejection(exc):
                                logger.warning(
                                    "page %s skipped: content filter rejected it (%s)", page_id, exc
                                )
                                if state_key not in _state.skipped_pages:
                                    _state.skipped_pages.append(state_key)
                                # §6: record outcome/reason so the rejection is a hit on
                                # resume rather than an eternal retry that burns quota.
                                _state.tombstones[state_key] = Tombstone(
                                    outcome="rejected",
                                    reason="content_policy",
                                    stage="render.page",
                                )
                                if state_key in _state.stale_pages:
                                    _state.stale_pages = [
                                        k for k in _state.stale_pages if k != state_key
                                    ]
                                _state.save(state_path, caches=False)
                                out = None
                                break
                            if (
                                not size_fallback_attempted
                                and active_size != _FALLBACK_PAGE_SIZE
                                and is_unsupported_image_size_error(exc)
                            ):
                                size_fallback_attempted = True
                                logger.warning(
                                    "page %s size %s rejected (%s); falling back to %s",
                                    page_id,
                                    active_size,
                                    exc,
                                    _FALLBACK_PAGE_SIZE,
                                )
                                active_size = _FALLBACK_PAGE_SIZE
                                continue
                            if not stricter_attempted:
                                stricter_attempted = True
                                prompt = render_finished_page_prompt(
                                    plan,
                                    characters_by_name=_state.characters,
                                    settings_by_name=_state.settings,
                                    style_guide=_style,
                                    strict=True,
                                    visual_bible=_state.visual_bible,
                                )
                                logger.warning(
                                    "page %s image failed (%s); retrying once with stricter prompt",
                                    page_id,
                                    exc,
                                )
                                continue
                            raise
                    if out is None:
                        return
                    pages_dir.mkdir(parents=True, exist_ok=True)
                    blank_dir = pages_dir / "blank"
                    blank_dir.mkdir(parents=True, exist_ok=True)
                    blank_path = _page_asset_path(blank_dir, _chunk_index, page_index)
                    await asyncio.to_thread(out.save, str(blank_path))
                    stored_render_env = h_env(
                        text=prompt, resolved_size=active_size, scope="render"
                    )
                    stored_render_inputs = render_page_inputs(_plan_json(plan), prompt)
                    stored_render_key = render_page_action_key(
                        plan_json=_plan_json(plan),
                        prompt=prompt,
                        resolved_size=active_size,
                        page_key=state_key,
                        ref_policy=ref_policy,
                        env=stored_render_env,
                        stage_src=_RENDER_PAGE_STAGE_SRC,
                    )
                    record(
                        output_dir,
                        "render.page",
                        stored_render_key,
                        [("blank.png", blank_path.read_bytes())],
                        stage_src=_RENDER_PAGE_STAGE_SRC,
                        env=stored_render_env,
                        inputs=stored_render_inputs,
                    )
                    local = _page_asset_path(pages_dir, _chunk_index, page_index)
                    if (
                        _letter_resume_status(
                            blank_path,
                            local,
                            plan,
                            _chunk,
                            output_dir,
                            resolved_size=active_size,
                        )
                        == "tombstone"
                    ):
                        return
                    await asyncio.to_thread(
                        partial(
                            _letter_page_from_blank,
                            blank_path,
                            local,
                            plan,
                            source_text=_chunk,
                            output_dir=output_dir,
                            resolved_size=active_size,
                        )
                    )
                    record_page_artifact(
                        _state,
                        state_key,
                        GeneratedPage(
                            local=str(local),
                            blank_local=str(blank_path),
                            lettering_version=LETTERING_VERSION,
                            resolved_size=active_size,
                            render_action_key=stored_render_key,
                            page_id=page_id,
                            unit_index=_chunk_index,
                            page_index=page_index,
                            mode="finished_lettered",
                        ),
                    )
                    if ledger.rebuild_from_state(_state):
                        ledger.save(ledger_path)
                    if page_is_stale:
                        # §4: this page's blank was just replaced, so the page
                        # that consumed the old one is now inconsistent. Flag
                        # it per character; quiet below the threshold.
                        successor = next_page_in_book(
                            _state,
                            _pageset,
                            chunk_index=_chunk_index,
                            page_index=page_index,
                        )
                        if successor and ledger.mark_continuity_pending(successor):
                            ledger.save(ledger_path)
                    _state.save(state_path, caches=False)
                    _report("pages", _pct())

                if _append_page_render_nodes(render_graph, state, ci, render_nodes_by_chunk):
                    render_graph.validate()
                for page_node in render_nodes_by_chunk.get(ci, ()):
                    _require_render_node(page_node)
                    assert page_node.item_index is not None
                    await _render_page_node(
                        page_node.item_index, pageset.pages[page_node.item_index]
                    )
                    completed_render_nodes.add(page_node.node_id)

                _mark_page_chunk_done_if_complete(state, key, pageset, ci)
                ledger.rebuild_from_state(state)
                ledger.save(ledger_path)
                state.save(state_path, caches=False)
                _report("pages", _pct())
                continue

            # storyboard (only when not cached)
            storyboard_cast = "\n".join(
                sorted(character.name for character in planned_elements.characters)
            )
            storyboard_beats = key_beats_for_chunk(state, key, chunks)
            storyboard_request = build_storyboard_request(
                chunk,
                planned_elements,
                key_beats=storyboard_beats,
                panel_budget=density_plan.panels_per_chunk,
                enforce_budget=clips_plan(density_plan.tier),
                tier=density_plan.tier,
                genre=run_genre,
                harness=run_harness,
                model=snapshot.chat,
            )
            review.record("storyboard", ci, storyboard_request)
            storyboard_key_request = build_storyboard_request(
                chunk,
                StoryElements(),
                key_beats=storyboard_beats,
                panel_budget=density_plan.panels_per_chunk,
                enforce_budget=clips_plan(density_plan.tier),
                tier=density_plan.tier,
                genre=run_genre,
                harness=run_harness,
                model=snapshot.chat,
            )
            storyboard_key = storyboard_action_key(
                chunk=chunk,
                cast=storyboard_cast,
                env=snapshot.chat,
                stage_src=_STORYBOARD_STAGE_SRC,
                request_digest=storyboard_key_request.digest(),
            )
            cached_chunk = state.chunk_cache.get(key)
            if board is None:
                restored_board = _restore_stage_body(
                    output_dir, storyboard_key, "storyboard", Storyboard
                )
                if restored_board is not None:
                    board = restored_board
                    cached_chunk = state.chunk_cache.setdefault(key, ChunkCache())
                    cached_chunk.storyboard = board
                    cached_chunk.storyboard_action_key = storyboard_key
            if board is not None and cached_chunk is not None:
                if (
                    cached_chunk.storyboard_action_key
                    and cached_chunk.storyboard_action_key != storyboard_key
                ):
                    board = None
                elif not cached_chunk.storyboard_action_key:
                    cached_chunk.storyboard_action_key = storyboard_key
                    # A stamp-only change is still a heavy-cache mutation;
                    # persist it before any render save can run projection-only.
                    state.save(state_path)
            if board is None:
                state.stage = "storyboard"
                _charge("storyboard", f"c{ci:04d}")
                try:
                    with perf.measure("storyboard"):
                        board = await plan_storyboard(
                            chunk,
                            planned_elements,
                            chat=chat,
                            panel_budget=density_plan.panels_per_chunk,
                            enforce_budget=clips_plan(density_plan.tier),
                            tier=density_plan.tier,
                            key_beats=storyboard_beats,
                            request=storyboard_request,
                        )
                except Exception as exc:  # noqa: BLE001 — content rejections must not abort the run
                    if is_content_policy_rejection(exc):
                        logger.warning(
                            "chunk %s skipped: content filter rejected storyboard (%s)", ci, exc
                        )
                        if key not in state.skipped_chunks:
                            state.skipped_chunks.append(key)
                        state.save(state_path)
                        _report("skip", _pct())
                        continue
                    raise
                cached_board = state.chunk_cache.setdefault(key, ChunkCache())
                cached_board.storyboard = board
                cached_board.storyboard_action_key = storyboard_key
                _record_stage_body(
                    output_dir,
                    "storyboard",
                    storyboard_key,
                    board,
                    stage_src=_STORYBOARD_STAGE_SRC,
                    env=snapshot.chat,
                    inputs=[],
                )
                state.save(state_path)
                _report("storyboard", _pct())
            if board is not None:
                review.record_plan("storyboard", ci, board.model_dump(mode="json"))

            known_beats = key_beats_for_chunk(state, key, chunks)
            if board is not None and known_beats is not None:
                assign_panel_beat_ids(board.panels, known_beats.beats)

            script_missing, script_key = _page_script_plan(
                state.chunk_cache.get(key),
                chunk,
                snapshot.chat,
                harness=run_harness,
                output_dir=output_dir,
            )
            # page-script
            # Optional legacy PageScript metadata (NOT a quality gate). Off by default.
            if _page_script_enabled() and script_missing:
                page_script_request = build_page_script_request(
                    board,
                    planned_elements,
                    chunk,
                    harness=run_harness,
                    model=snapshot.chat,
                )
                review.record("page_script", ci, page_script_request)
                _charge("page_script", f"c{ci:04d}")
                try:
                    with perf.measure("page_script"):
                        ps = await plan_page_script(
                            board,
                            planned_elements,
                            chunk,
                            chat=chat,
                            request=page_script_request,
                        )
                except Exception as exc:  # noqa: BLE001
                    if is_content_policy_rejection(exc):
                        logger.warning(
                            "chunk %s page_script rejected by policy; recording empty script",
                            ci,
                        )
                        ps = PageScript(
                            chapter_id=board.chapter_id,
                            pages=[],
                            skipped_pages=list(range((len(board.panels) + 3) // 4)),
                        )
                    else:
                        raise
                _store_page_script(state.chunk_cache[key], ps, script_key)
                _record_stage_body(
                    output_dir,
                    "page_script",
                    script_key,
                    ps,
                    stage_src=_PAGE_SCRIPT_STAGE_SRC,
                    env=snapshot.chat,
                    inputs=[],
                )
                state.save(state_path)
                _report("page_script", _pct())

            # panels
            if phase == "plan":
                continue
            _report("panels", _pct())
            seen_ids: set[str] = set()
            pending: list[tuple[str, int, object]] = []
            for panel_index, panel in enumerate(board.panels):
                # Model-structured output sometimes reuses panel_id. State keys are
                # index-based, so remint a unique source id instead of aborting the run.
                if panel.panel_id in seen_ids:
                    original = panel.panel_id
                    n = 2
                    while f"{original}__{n}" in seen_ids:
                        n += 1
                    panel.panel_id = f"{original}__{n}"
                    logger.warning(
                        "duplicate panel_id %r in storyboard chunk %s; remapped to %r",
                        original,
                        ci,
                        panel.panel_id,
                    )
                seen_ids.add(panel.panel_id)
                state_key = _stored_panel_key(state, ci, panel_index)
                if panel_key_filter is not None and state_key not in panel_key_filter:
                    continue
                _sync_stored_panel_key(state, state_key, panel, effective_style, snapshot.t2i)
                if _panel_needs_generation(state, state_key):
                    pending.append((state_key, panel_index, panel))

            panel_elements = elements
            panel_style = effective_style
            panel_chunk_index = ci

            async def _render_panel(
                state_key: str,
                panel_index: int,
                panel,
                previous: str | None,
                *,
                elements_for_panel: StoryElements = panel_elements,
                style_for_panel: str = panel_style,
                chunk_index: int = panel_chunk_index,
                _state: ProjectState = state,
            ) -> GeneratedPanel:
                # Same name set for L1 prompt subjects and L2/L3 refs so a model that
                # fills only one of characters_present / reference_characters cannot
                # silently desync text conditioning from portrait conditioning.
                panel_names = _panel_reference_names(panel)
                chars = [_state.characters[n] for n in panel_names if n in _state.characters]
                prompt = engine.build_panel_prompt(
                    characters=chars,
                    setting=_resolve_setting(_state, elements_for_panel, panel.setting_ref),
                    action=panel.action,
                    style_guide=style_for_panel,
                )
                refs = engine.collect_reference_images(
                    panel=panel,
                    characters_by_name=_state.characters,
                    prev_panel_local=previous,
                )
                refs = [ref for ref in refs if _is_within(ref, output_dir) and Path(ref).is_file()]
                async with image_semaphore:
                    _charge("render", state_key)
                    image_intent(
                        output_dir, stage="render", key=state_key, prompt=prompt, size=panel.size
                    )
                    try:
                        with perf.measure("panel"):
                            out = await image.generate_single_image(
                                prompt, reference_image_paths=refs, size=panel.size
                            )
                    except Exception as exc:  # noqa: BLE001 — log the class, then keep the panel error
                        image_failure(output_dir, stage="render", key=state_key, error=exc)
                        raise
                local = _asset_path(output_dir, "panels", state_key)
                await asyncio.to_thread(out.save, str(local))

                portrait_ref = next(
                    (
                        _state.characters[n].portrait_local
                        for n in _panel_reference_names(panel)
                        if n in _state.characters and _state.characters[n].portrait_local
                    ),
                    None,
                )
                if (
                    portrait_ref
                    and _is_within(portrait_ref, output_dir)
                    and Path(portrait_ref).is_file()
                ):
                    composited = await asyncio.to_thread(engine.apply_l3, str(local), portrait_ref)
                    await asyncio.to_thread(composited.save, str(local))
                return GeneratedPanel(
                    local=str(local),
                    chunk_index=chunk_index,
                    panel_index=panel_index,
                    source_panel_id=panel.panel_id,
                    dialogue=panel.dialogue,
                    caption=panel.caption,
                    sfx=panel.sfx,
                    render_action_key=_panel_render_key(
                        panel, style_for_panel, state_key, snapshot.t2i
                    ),
                )

            state.stage = "panels"
            check_cancel(cancel_check)

            async def _render_continuity_panel_node(
                panel_index: int,
                panel,
                *,
                _state: ProjectState = state,
                _chunk_index: int = ci,
                _render=_render_panel,
            ) -> None:
                nonlocal prev_panel_local
                state_key = _stored_panel_key(_state, _chunk_index, panel_index)
                if panel_key_filter is not None and state_key not in panel_key_filter:
                    if state_key in _state.panels_done and state_key in _state.generated.panels:
                        prev_panel_local = _state.generated.panels[state_key].local
                    return
                if not _panel_needs_generation(_state, state_key):
                    if state_key in _state.panels_done and state_key in _state.generated.panels:
                        prev_panel_local = _state.generated.panels[state_key].local
                    return
                if not gate.allow(state_key, _chunk_index, total_chunks=total_chunks):
                    return
                check_cancel(cancel_check)
                try:
                    generated = await _render(state_key, panel_index, panel, prev_panel_local)
                except Exception as exc:  # noqa: BLE001 — preserve policy skip behavior
                    if not is_content_policy_rejection(exc):
                        raise
                    logger.warning(
                        "panel %s skipped: content filter rejected it (%s)", panel.panel_id, exc
                    )
                    if state_key not in _state.skipped:
                        _state.skipped.append(state_key)
                    if state_key in _state.stale_panels:
                        _state.stale_panels = [k for k in _state.stale_panels if k != state_key]
                    _state.save(state_path, caches=False)
                    return
                _state.generated.panels[state_key] = generated
                _mark_panel_done(_state, state_key)
                prev_panel_local = generated.local
                _state.save(state_path, caches=False)
                _report("panel", _pct())

            if _append_panel_render_nodes(
                render_graph,
                state,
                ci,
                render_nodes_by_chunk,
                batch_panel_keys,
                continuity=image_config.panel_continuity,
            ):
                render_graph.validate()
            if image_config.panel_continuity:
                for panel_node in render_nodes_by_chunk.get(ci, ()):
                    _require_render_node(panel_node)
                    assert panel_node.item_index is not None
                    await _render_continuity_panel_node(
                        panel_node.item_index, board.panels[panel_node.item_index]
                    )
                    completed_render_nodes.add(panel_node.node_id)
            else:
                for panel_node in render_nodes_by_chunk.get(ci, ()):
                    _require_render_node(panel_node)
                    # Membership is scheduler-only metadata: it pins the batch to
                    # the positions planned above and never enters state.json.
                    recorded_keys = batch_panel_keys.get(panel_node.node_id)
                    positional_keys = tuple(
                        _stored_panel_key(state, ci, index) for index in range(len(board.panels))
                    )
                    if recorded_keys != positional_keys:
                        raise RuntimeError(f"panel batch {panel_node.node_id} membership drifted")
                    renderable = [
                        item
                        for item in pending
                        if gate.allow(item[0], ci, total_chunks=total_chunks)
                    ]
                    tasks = (
                        _render_panel(state_key, panel_index, panel, None)
                        for state_key, panel_index, panel in renderable
                    )
                    rendered = await asyncio.gather(*tasks, return_exceptions=True)
                    operational_error = None
                    for (state_key, _panel_index, panel), result in zip(
                        renderable, rendered, strict=True
                    ):
                        if isinstance(result, Exception):
                            if not is_content_policy_rejection(result):
                                operational_error = operational_error or result
                                continue
                            logger.warning(
                                "panel %s skipped: content filter rejected it (%s)",
                                panel.panel_id,
                                result,
                            )
                            if state_key not in state.skipped:
                                state.skipped.append(state_key)
                            if state_key in state.stale_panels:
                                state.stale_panels = [
                                    k for k in state.stale_panels if k != state_key
                                ]
                            continue
                        state.generated.panels[state_key] = result
                        _mark_panel_done(state, state_key)
                        _report("panel", _pct())
                    state.save(state_path, caches=False)
                    if operational_error is not None:
                        raise operational_error
                    completed_render_nodes.add(panel_node.node_id)

            _mark_chunk_done_if_complete(state, key, board, ci)
            state.save(state_path, caches=False)
            _report("panels", _pct())

    outstanding = sorted(
        node.node_id
        for nodes in render_nodes_by_chunk.values()
        for node in nodes
        if node.node_id not in completed_render_nodes
    )
    if outstanding:
        raise RuntimeError("render work incomplete before export: " + ", ".join(outstanding))

    pdf: str | None = None
    webtoon: str | None = None
    cbz: str | None = None
    pages: list[str] = []

    if mode == "finished_page":
        # No LayoutEngine collage for page mode: each page image was already
        # saved directly by the loop above. Webtoon mode stacks those finished
        # pages into a single vertical strip.
        state.stage = "export"
        _report("export", max(0.90, _pct()))
        page_files = prepare_finished_page_export(pages_dir) if pages_dir.exists() else []
        if page_files:
            if output_format == "webtoon":
                node = _schedule_export("export.webtoon")
                panel_imgs = [PanelImage(Image.open(p)) for p in page_files]
                with perf.measure("layout"):
                    webtoon_paths = export_webtoon_with_manifest(panel_imgs, pages_dir, output_dir)
                webtoon = webtoon_paths[0] if webtoon_paths else None
                pages = webtoon_paths
            elif output_format == "cbz":
                node = _schedule_export("export.cbz")
                with perf.measure("export"):
                    cbz = export_cbz_with_manifest(pages_dir, output_dir)
                pages = [str(p) for p in page_files]
            else:
                node = _schedule_export("export.pdf")
                with perf.measure("export"):
                    pdf = export_pdf_with_manifest(pages_dir, output_dir)
                pages = [str(p) for p in page_files]
            completed_render_nodes.add(node.node_id)
    else:
        state.stage = "layout"
        _report("layout", max(0.90, _pct()))
        items = _ordered_generated_panels(state)
        panel_imgs = []
        for panel_id, generated in items:
            if not _is_within(generated.local, output_dir) or not Path(generated.local).exists():
                logger.warning("layout: panel %s missing or outside project; omitting", panel_id)
                continue
            panel_imgs.append(
                PanelImage(
                    Image.open(generated.local),
                    dialogue=generated.dialogue,
                    caption=generated.caption,
                    sfx=generated.sfx,
                )
            )

        if panel_imgs:
            if output_format == "webtoon":
                node = _schedule_export("export.webtoon")
                with perf.measure("layout"):
                    pages = export_webtoon_with_manifest(panel_imgs, pages_dir, output_dir)
                completed_render_nodes.add(node.node_id)
                state.stage = "export"
                _report("export", 0.95)
                webtoon = pages[0] if pages else None
            else:
                layout_node = _schedule_export("export.layout")
                with perf.measure("layout"):
                    laid_out = layout_pages_with_manifest(panel_imgs, pages_dir, output_dir)
                    pages = [] if laid_out is None else laid_out
                completed_render_nodes.add(layout_node.node_id)
                state.stage = "export"
                _report("export", 0.95)
                if output_format == "cbz" and pages:
                    bind_node = _schedule_export("export.cbz", dependencies=("export.layout",))
                    with perf.measure("export"):
                        cbz = export_cbz_with_manifest(pages_dir, output_dir)
                    completed_render_nodes.add(bind_node.node_id)
                elif pages:
                    bind_node = _schedule_export("export.pdf", dependencies=("export.layout",))
                    with perf.measure("export"):
                        pdf = export_pdf_with_manifest(pages_dir, output_dir)
                    completed_render_nodes.add(bind_node.node_id)

    state.save(state_path)
    if gate.awaiting and gate.project.paused_key is not None:
        review.finish(state=state, source=source_txt, pages=pages)
        raise GatePaused(gate.project.paused_key)
    review.finish(state=state, source=source_txt, pages=pages)
    _report("done", 1.0)
    perf.log_summary()
    return ComicProject(
        project_id=project_id, state=state, pages=pages, pdf=pdf, webtoon=webtoon, cbz=cbz
    )
