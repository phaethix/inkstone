"""core.pipelines.creative_comic — text-to-comic orchestration.

Drives the whole flow for one source text:

    segment -> extract -> merge characters -> (portraits) -> storyboard
    -> per panel: build prompt (L1) + collect references (L2) -> generate
    -> face composite (L3) -> layout -> export PDF

State is persisted to ``state.json`` after every panel so a rerun resumes
from where it stopped and never regenerates an already-finished panel. Each
chunk's extracted ``StoryElements`` and planned ``Storyboard`` are cached in
``ProjectState.chunk_cache``, so a resume reuses them instead of re-paying the
(billable) chat API for already-planned chunks; only chunks with missing panels
are re-entered, and only the missing panels are regenerated.

Providers are injected so the pipeline can be exercised without network.
"""

import asyncio
import hashlib
import json
import logging
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - exercised only on Windows
    fcntl = None

try:
    import msvcrt
except ImportError:  # pragma: no cover - exercised only outside Windows
    msvcrt = None

from PIL import Image

from core.api import get_chat_provider, get_image_provider
from core.comic.consistency import DEFAULT_PORTRAIT_STYLE, ConsistencyEngine
from core.comic.export import ExportEngine
from core.comic.identity import ensure_character_l1, merge_settings, suggestion_from_alias
from core.comic.layout import LayoutEngine, PanelImage
from core.comic.segmentation import detect_character_aliases, merge_characters, segment_text
from core.config import ImageConfig, l3_enabled, page_script_enabled
from core.perf import PerfCollector
from core.pipelines.cancel import check_cancel
from core.schemas import (
    ChunkCache,
    GeneratedPanel,
    ModelSnapshot,
    PageScript,
    ProjectState,
    Setting,
    Storyboard,
    StoryElements,
)
from core.screenwriter import (
    extract_story_elements,
    is_content_policy_rejection,
    plan_page_script,
    plan_storyboard,
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


_PIPELINE_STATE_VERSION = "2026-07-22.3"


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


def _structure_fingerprint(source_txt: str) -> str:
    canonical = source_txt.replace("\r\n", "\n").replace("\r", "\n")
    payload = json.dumps(
        {"pipeline": _PIPELINE_STATE_VERSION, "source": canonical},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _render_fingerprint(
    style_guide: str | None,
    *,
    snapshot: ModelSnapshot,
    panel_continuity: bool,
    l3_enabled: bool,
) -> str:
    payload = json.dumps(
        {
            "style_guide": style_guide or "",
            "model_snapshot": snapshot.model_dump(),
            "panel_continuity": panel_continuity,
            "l3_enabled": l3_enabled,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _legacy_combined_fingerprint(
    source_txt: str,
    style_guide: str | None,
    *,
    snapshot: ModelSnapshot,
    panel_continuity: bool,
    l3_enabled: bool,
) -> str:
    """Legacy combined fingerprint (source + render inputs) for migration comparison."""
    canonical_source = source_txt.replace("\r\n", "\n").replace("\r", "\n")
    payload = json.dumps(
        {
            "pipeline": _PIPELINE_STATE_VERSION,
            "source": canonical_source,
            "style_guide": style_guide or "",
            "model_snapshot": snapshot.model_dump(),
            "panel_continuity": panel_continuity,
            "l3_enabled": l3_enabled,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _input_fingerprint(
    source_txt: str,
    style_guide: str | None,
    *,
    snapshot: ModelSnapshot,
    panel_continuity: bool,
    l3_enabled: bool,
) -> str:
    """Fingerprint normalized source and every option that changes generated assets."""
    return _legacy_combined_fingerprint(
        source_txt,
        style_guide,
        snapshot=snapshot,
        panel_continuity=panel_continuity,
        l3_enabled=l3_enabled,
    )


def _is_within(path: str | Path, root: Path) -> bool:
    try:
        Path(path).resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _panel_state_key(chunk_index: int, panel_index: int) -> str:
    """Return the pipeline-owned identity for one storyboard position."""
    return f"c{chunk_index:04d}-p{panel_index:04d}"


def _stored_panel_key(state: ProjectState, chunk_index: int, panel_index: int) -> str:
    """Return the pipeline-owned identity for a current-version storyboard panel."""
    return _panel_state_key(chunk_index, panel_index)


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
    """Ensure one POSIX process mutates a project's state and assets at a time."""
    output_dir.mkdir(parents=True, exist_ok=True)
    lock_path = output_dir / ".inkstone.lock"
    with lock_path.open("a+") as lock_file:
        if fcntl is not None:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError(f"project is already running: {output_dir}") from exc
        elif msvcrt is not None:  # pragma: no cover - exercised only on Windows
            lock_file.seek(0)
            lock_file.write("0")
            lock_file.flush()
            lock_file.seek(0)
            try:
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise RuntimeError(f"project is already running: {output_dir}") from exc
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            elif msvcrt is not None:  # pragma: no cover - exercised only on Windows
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)


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


def _soft_invalidate_render(state: ProjectState) -> None:
    """Drop render-owned assets while keeping structural cache (extract/storyboard)."""
    state.panels_done = []
    state.stale_panels = []
    state.skipped = []
    state.generated.panels = {}
    state.generated.portraits = {}
    for asset in state.characters.values():
        asset.portrait_local = None


def _reconcile_state(state: ProjectState, state_path: Path, output_dir: Path) -> None:
    """Remove every missing or escaped persisted asset before a resume trusts it."""
    changed = False
    invalid_panels = [
        panel_id
        for panel_id, generated in state.generated.panels.items()
        if not _is_within(generated.local, output_dir) or not Path(generated.local).is_file()
    ]
    for panel_id in invalid_panels:
        state.generated.panels.pop(panel_id, None)
        changed = True
    stale_done = [
        panel_id for panel_id in state.panels_done if panel_id not in state.generated.panels
    ]
    for panel_id in stale_done:
        state.panels_done.remove(panel_id)
        changed = True

    orphan_portraits = set(state.generated.portraits) - set(state.characters)
    for name in orphan_portraits:
        state.generated.portraits.pop(name, None)
        changed = True
    for name, asset in state.characters.items():
        portrait = asset.portrait_local
        if portrait and _is_within(portrait, output_dir) and Path(portrait).is_file():
            continue
        if portrait:
            logger.warning("reconcile: discarding invalid portrait for %s", name)
        asset.portrait_local = None
        state.generated.portraits.pop(name, None)
        changed = True

    if not changed:
        return
    if invalid_panels or stale_done:
        logger.warning(
            "reconcile: removed %d invalid panel record(s)", len(invalid_panels) + len(stale_done)
        )
    state.save(state_path)


def _chunk_complete(
    state: ProjectState, board: Storyboard, output_dir: Path, chunk_index: int
) -> bool:
    """True when every planned panel is generated, contained, and present on disk."""
    for panel_index, _panel in enumerate(board.panels):
        state_key = _stored_panel_key(state, chunk_index, panel_index)
        if state_key in state.stale_panels:
            return False
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
    for chunk_index, key in enumerate(digit_keys):
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


def estimate_progress(state: ProjectState, total_chunks: int | None = None) -> float:
    """Estimate overall completion in ``[0, 1)`` from checkpointed panels.

    Used so a resume does not reset the UI progress bar to near-zero. Reserves
    the last 10% for layout/export.
    """
    finished, planned = panel_progress_counts(state, total_chunks)
    if planned <= 0:
        return 0.0
    return min(0.95, 0.9 * finished / planned)


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
) -> ComicProject:
    """Generate a project while holding its process-level mutation lock."""
    with _project_lock(Path(output_dir)):
        return await _creative_comic(
            source_txt,
            output_dir=output_dir,
            project_id=project_id,
            chat=chat,
            image=image,
            style_guide=style_guide,
            output_format=output_format,
            progress_callback=progress_callback,
            panel_keys=panel_keys,
            cancel_check=cancel_check,
        )


def _page_script_enabled() -> bool:
    return page_script_enabled()


async def _creative_comic(
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
) -> ComicProject:
    """Generate a comic from ``source_txt`` into ``output_dir``.

    Args:
        source_txt: the full novel/scene text.
        output_dir: directory for panels, assets, pages, PDF, and state.json.
        project_id: identifier stored in state (defaults to the dir name).
        chat / image: provider instances (defaults from the factories).
        style_guide: optional global style appended to every panel prompt.
        output_format: ``"page"`` for a flip-page PDF (default) or ``"webtoon"``
            for a single vertical strip PNG (no external CLI required).
        progress_callback: optional ``callback(stage, percent)`` invoked during
            generation. ``percent`` is ``None`` when the stage has no reliable
            completion percentage; otherwise it ranges from 0.0 to 1.0.
        panel_keys: when set, only these panel state keys are (re)generated;
            layout/export still run over the full project.
        cancel_check: optional callable polled at chunk/panel checkpoints;
            raises ``PipelineCancelled`` when it returns true.

    Returns:
        A ``ComicProject`` with the final state, produced page paths, and PDF.
    """
    output_dir = Path(output_dir)
    (output_dir / "panels").mkdir(parents=True, exist_ok=True)
    (output_dir / "assets" / "portraits").mkdir(parents=True, exist_ok=True)
    pages_dir = output_dir / "pages"
    panel_key_filter = set(panel_keys) if panel_keys is not None else None

    perf = PerfCollector()

    def _progress_label(stage: str) -> str:
        if stage in ("panel", "panels"):
            done, planned = panel_progress_counts(state, total_chunks)
            return f"panels {done}/{planned}"
        return stage

    def _report(stage: str, percent: float | None = None) -> None:
        if progress_callback is not None:
            progress_callback(_progress_label(stage), percent)

    project_id = project_id or output_dir.name or "comic"
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
    fingerprint = _input_fingerprint(
        source_txt,
        style_guide,
        snapshot=snapshot,
        panel_continuity=image_config.panel_continuity,
        l3_enabled=l3_on,
    )
    struct = _structure_fingerprint(source_txt)
    render = _render_fingerprint(
        style_guide,
        snapshot=snapshot,
        panel_continuity=image_config.panel_continuity,
        l3_enabled=l3_on,
    )

    def _fresh_state() -> ProjectState:
        return ProjectState(
            project_id=project_id,
            source_file=str(output_dir),
            source_fingerprint=struct,
            structure_fingerprint=struct,
            render_fingerprint=render,
            model_snapshot=snapshot,
        )

    if state_path.exists():
        persisted = ProjectState.load(state_path)
        if not persisted.structure_fingerprint and not persisted.render_fingerprint:
            if persisted.source_fingerprint == fingerprint:
                state = persisted
                state.structure_fingerprint = struct
                state.render_fingerprint = render
                state.source_fingerprint = struct
            else:
                state = _fresh_state()
        elif persisted.structure_fingerprint != struct:
            state = _fresh_state()
        elif persisted.render_fingerprint != render:
            state = persisted
            _soft_invalidate_render(state)
            state.render_fingerprint = render
            state.model_snapshot = snapshot
        else:
            state = persisted
    else:
        state = _fresh_state()
    state.project_id = project_id

    image_semaphore = asyncio.Semaphore(image_config.image_concurrency)
    engine = ConsistencyEngine()

    _reconcile_state(state, state_path, output_dir)

    with perf.measure("segment"):
        chunks = segment_text(source_txt)
    total_chunks = len(chunks) or 1

    def _pct() -> float:
        return estimate_progress(state, total_chunks)

    # Resume: jump the bar to checkpointed completion instead of restarting at 0.
    _report("resume" if (state.panels_done or state.chunk_cache) else "segment", _pct())
    prev_panel_local: str | None = None

    for ci, chunk in enumerate(chunks):
        check_cancel(cancel_check)
        key = str(ci)
        if key in state.skipped_chunks:
            logger.info("chunk %s skipped: previously rejected by content filter", key)
            _report("skip", _pct())
            continue
        cached = state.chunk_cache.get(key)
        board = cached.storyboard if cached else None
        elements = cached.elements if cached else None

        # Fully planned chunk: cached, marked done, every panel present on disk.
        # Re-running reuses the cache so the billable chat API is never re-called.
        if (
            board is not None
            and key in set(state.chunks_done)
            and _chunk_complete(state, board, output_dir, ci)
            and panel_key_filter is None
        ):
            if image_config.panel_continuity and board.panels:
                last_index = len(board.panels) - 1
                state_key = _stored_panel_key(state, ci, last_index)
                prev_panel_local = state.generated.panels[state_key].local
            _report("resume", _pct())
            continue

        # ---- extraction (only when not cached) ----
        if elements is None:
            state.stage = "extract"
            try:
                with perf.measure("extract"):
                    elements = await extract_story_elements(chunk, chat=chat)
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
            # Cache so a later resume does not re-pay for extraction.
            state.chunk_cache.setdefault(key, ChunkCache()).elements = elements
            state.save(state_path)
            _report("extract", _pct())

        # Merge characters; generate a portrait only for first-seen names.
        state.characters, new_names = merge_characters(state.characters, elements.characters)
        for name in new_names:
            ensure_character_l1(state.characters[name])
        state.settings = merge_settings(state.settings, elements.settings)

        # Surface likely alias variants for human review (never auto-merged).
        for name, cand, reason in detect_character_aliases(state.characters, new_names):
            sugg = suggestion_from_alias(name, cand, reason)
            if sugg not in state.needs_review:
                state.needs_review.append(sugg)
        effective_style = style_guide or elements.style_guide
        portrait_style = effective_style

        async def _render_portrait(name: str, *, style: str = portrait_style) -> tuple[str, str]:
            asset = state.characters[name]
            ensure_character_l1(asset)
            prompt = asset.portrait_prompt or asset.l1_prompt
            comic_style = f"{style}, {DEFAULT_PORTRAIT_STYLE}" if style else DEFAULT_PORTRAIT_STYLE
            async with image_semaphore:
                with perf.measure("portrait"):
                    out = await image.generate_single_image(
                        f"{prompt}, {comic_style}", size="1024x1024"
                    )
            path = _asset_path(output_dir, "assets/portraits", name)
            await asyncio.to_thread(out.save, str(path))
            return name, str(path)

        portrait_names = [
            name
            for name, asset in state.characters.items()
            if not asset.portrait_local
            or not _is_within(asset.portrait_local, output_dir)
            or not Path(asset.portrait_local).is_file()
        ]
        portrait_results = await asyncio.gather(
            *(_render_portrait(name) for name in portrait_names),
            return_exceptions=True,
        )
        policy_rejection: Exception | None = None
        operational_error: Exception | None = None
        for name, result in zip(portrait_names, portrait_results, strict=True):
            if isinstance(result, Exception):
                if is_content_policy_rejection(result):
                    policy_rejection = result
                elif operational_error is None:
                    operational_error = result
                continue
            _, path = result
            state.characters[name].portrait_local = path
            state.generated.portraits[name] = path
            _report("portrait", _pct())

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

        # ---- storyboard (only when not cached) ----
        if board is None:
            state.stage = "storyboard"
            try:
                with perf.measure("storyboard"):
                    board = await plan_storyboard(chunk, elements, chat=chat)
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
            state.chunk_cache.setdefault(key, ChunkCache()).storyboard = board
            state.save(state_path)
            _report("storyboard", _pct())

        # ---- page-script ----
        # Optional legacy PageScript metadata (NOT a quality gate). Off by default.
        if _page_script_enabled() and state.chunk_cache.get(key, ChunkCache()).page_script is None:
            try:
                with perf.measure("page_script"):
                    ps = await plan_page_script(board, elements, chunk, chat=chat)
            except Exception as exc:  # noqa: BLE001
                if is_content_policy_rejection(exc):
                    logger.warning(
                        "chunk %s legacy page_script rejected by policy; recording empty script",
                        ci,
                    )
                    ps = PageScript(
                        chapter_id=board.chapter_id,
                        pages=[],
                        skipped_pages=list(range((len(board.panels) + 3) // 4)),
                    )
                else:
                    raise
            state.chunk_cache[key].page_script = ps
            state.save(state_path)
            _report("page_script", _pct())

        # ---- panels ----
        _report("panels", _pct())
        seen_ids: set[str] = set()
        pending: list[tuple[str, int, object]] = []
        for panel_index, panel in enumerate(board.panels):
            if panel.panel_id in seen_ids:
                raise ValueError(f"duplicate panel_id in storyboard: {panel.panel_id!r}")
            seen_ids.add(panel.panel_id)
            state_key = _stored_panel_key(state, ci, panel_index)
            if panel_key_filter is not None and state_key not in panel_key_filter:
                continue
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
        ) -> GeneratedPanel:
            chars = [state.characters[n] for n in panel.characters_present if n in state.characters]
            prompt = engine.build_panel_prompt(
                characters=chars,
                setting=_resolve_setting(state, elements_for_panel, panel.setting_ref),
                action=panel.action,
                style_guide=style_for_panel,
            )
            refs = engine.collect_reference_images(
                panel=panel,
                characters_by_name=state.characters,
                prev_panel_local=previous,
            )
            refs = [ref for ref in refs if _is_within(ref, output_dir) and Path(ref).is_file()]
            async with image_semaphore:
                with perf.measure("panel"):
                    out = await image.generate_single_image(
                        prompt, reference_image_paths=refs, size=panel.size
                    )
            local = _asset_path(output_dir, "panels", state_key)
            await asyncio.to_thread(out.save, str(local))

            portrait_ref = next(
                (
                    state.characters[n].portrait_local
                    for n in panel.reference_characters
                    if n in state.characters and state.characters[n].portrait_local
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
            )

        state.stage = "panels"
        check_cancel(cancel_check)
        if image_config.panel_continuity:
            for panel_index, panel in enumerate(board.panels):
                state_key = _stored_panel_key(state, ci, panel_index)
                if panel_key_filter is not None and state_key not in panel_key_filter:
                    if state_key in state.panels_done and state_key in state.generated.panels:
                        prev_panel_local = state.generated.panels[state_key].local
                    continue
                if not _panel_needs_generation(state, state_key):
                    if state_key in state.panels_done and state_key in state.generated.panels:
                        prev_panel_local = state.generated.panels[state_key].local
                    continue
                check_cancel(cancel_check)
                try:
                    generated = await _render_panel(state_key, panel_index, panel, prev_panel_local)
                except Exception as exc:  # noqa: BLE001 — preserve policy skip behavior
                    if not is_content_policy_rejection(exc):
                        raise
                    logger.warning(
                        "panel %s skipped: content filter rejected it (%s)", panel.panel_id, exc
                    )
                    if state_key not in state.skipped:
                        state.skipped.append(state_key)
                    if state_key in state.stale_panels:
                        state.stale_panels = [k for k in state.stale_panels if k != state_key]
                    state.save(state_path)
                    continue
                state.generated.panels[state_key] = generated
                _mark_panel_done(state, state_key)
                prev_panel_local = generated.local
                state.save(state_path)
                _report("panel", _pct())
        else:
            tasks = (
                _render_panel(state_key, panel_index, panel, None)
                for state_key, panel_index, panel in pending
            )
            rendered = await asyncio.gather(*tasks, return_exceptions=True)
            operational_error = None
            for (state_key, _panel_index, panel), result in zip(pending, rendered, strict=True):
                if isinstance(result, Exception):
                    if not is_content_policy_rejection(result):
                        operational_error = operational_error or result
                        continue
                    logger.warning(
                        "panel %s skipped: content filter rejected it (%s)", panel.panel_id, result
                    )
                    if state_key not in state.skipped:
                        state.skipped.append(state_key)
                    if state_key in state.stale_panels:
                        state.stale_panels = [k for k in state.stale_panels if k != state_key]
                    continue
                state.generated.panels[state_key] = result
                _mark_panel_done(state, state_key)
                _report("panel", _pct())
            state.save(state_path)
            if operational_error is not None:
                raise operational_error

        _mark_chunk_done_if_complete(state, key, board, ci)
        state.save(state_path)
        _report("panels", _pct())

    state.stage = "layout"
    _report("layout", max(0.90, _pct()))
    items = _ordered_generated_panels(state)
    panel_imgs = []
    for panel_id, generated in items:
        if not _is_within(generated.local, output_dir) or not Path(generated.local).exists():
            logger.warning("layout: panel %s missing or outside project; omitting", panel_id)
            continue
        panel_imgs.append(PanelImage(Image.open(generated.local), dialogue=generated.dialogue))

    pdf: str | None = None
    webtoon: str | None = None
    pages: list[str] = []
    if panel_imgs:
        with perf.measure("layout"):
            engine_layout = LayoutEngine()
            if output_format == "webtoon":
                pages = engine_layout.compose(panel_imgs, pages_dir, layout_mode="webtoon")
            else:
                pages = engine_layout.compose(panel_imgs, pages_dir, layout_mode="page")

        state.stage = "export"
        _report("export", 0.95)
        if output_format == "webtoon":
            webtoon = pages[0] if pages else None
        else:
            with perf.measure("export"):
                pdf = ExportEngine().export_pdf(pages_dir, out=str(output_dir / "comic.pdf"))

    state.save(state_path)
    _report("done", 1.0)
    perf.log_summary()
    return ComicProject(project_id=project_id, state=state, pages=pages, pdf=pdf, webtoon=webtoon)
