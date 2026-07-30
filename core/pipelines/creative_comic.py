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
from core.comic.consistency import (
    DEFAULT_PORTRAIT_STYLE,
    ConsistencyEngine,
    _panel_reference_names,
)
from core.comic.export import ExportEngine
from core.comic.identity import ensure_character_l1, merge_settings, suggestion_from_alias
from core.comic.layout import LayoutEngine, PanelImage
from core.comic.page_prompt import render_finished_page_prompt
from core.comic.segmentation import detect_character_aliases, merge_characters, segment_text
from core.config import ImageConfig, finished_page_size, l3_enabled, page_script_enabled
from core.config import render_mode as config_render_mode
from core.perf import PerfCollector
from core.pipelines.cancel import check_cancel
from core.schemas import (
    ChunkCache,
    ComicPagePlan,
    ComicPagePlanSet,
    GeneratedPage,
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
    plan_comic_pages,
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


# Bumped only when the *structure* fingerprint (extract/storyboard cache) must
# invalidate. Adding render_mode/finished_page_size only affects the render
# fingerprint (below), which already has its own independent invalidation path
# (``_soft_invalidate_render``), so no bump was needed for this change.
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
    render_mode: str = "finished_page",
    page_size: str = "1024x1536",
) -> str:
    payload = json.dumps(
        {
            "style_guide": style_guide or "",
            "model_snapshot": snapshot.model_dump(),
            "panel_continuity": panel_continuity,
            "l3_enabled": l3_enabled,
            "render_mode": render_mode,
            "page_size": page_size,
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


def _page_asset_path(pages_dir: Path, chunk_index: int, page_index: int) -> Path:
    """Deterministic ``page_XX.png``-style path for one (chunk, page) position.

    Position-derived (not a running counter) so regenerating a stale/missing
    page always rewrites the same file — a counter would risk colliding with
    an unrelated page's filename when only some pages are redone on resume.
    The ``page_`` prefix and zero-padding keep ``_finished_page_files`` in
    correct reading order.
    """
    return pages_dir / f"page_c{chunk_index:04d}_p{page_index:04d}.png"


def _finished_page_files(pages_dir: Path) -> list[Path]:
    """Return finished-page assets only, excluding panel-compose ``page_NN.png`` leftovers."""
    return sorted(p for p in pages_dir.glob("page_*.png") if p.match("page_c*_p*.png"))


def _prepare_finished_page_export(pages_dir: Path) -> list[Path]:
    """Collect finished-page assets and drop stale panel-compose ``page_NN.png`` files."""
    finished = _finished_page_files(pages_dir)
    for stale in pages_dir.glob("page_*.png"):
        if not stale.match("page_c*_p*.png"):
            stale.unlink()
    return finished


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


def _page_needs_generation(state: ProjectState, page_id: str) -> bool:
    """True when a finished page must be (re)generated this run."""
    if page_id in state.stale_pages:
        return True
    if page_id in state.pages_done or page_id in state.skipped_pages:
        return False
    return True


def _mark_page_done(state: ProjectState, page_id: str) -> None:
    if page_id in state.stale_pages:
        state.stale_pages = [k for k in state.stale_pages if k != page_id]
    if page_id not in state.pages_done:
        state.pages_done.append(page_id)


def _page_chunk_complete(state: ProjectState, pageset: ComicPagePlanSet, output_dir: Path) -> bool:
    """True when every planned page is generated or policy-skipped."""
    for plan in pageset.pages:
        page_id = plan.page_id
        if page_id in state.stale_pages:
            return False
        if page_id in state.skipped_pages:
            continue
        rec = state.generated.pages.get(page_id)
        if (
            rec is None
            or page_id not in state.pages_done
            or not _is_within(rec.local, output_dir)
            or not Path(rec.local).exists()
        ):
            return False
    return True


def _mark_page_chunk_done_if_complete(
    state: ProjectState, key: str, pageset: ComicPagePlanSet
) -> None:
    """Record chunks_done only after every planned page is done or skipped."""
    for plan in pageset.pages:
        page_id = plan.page_id
        if page_id in state.stale_pages:
            return
        if page_id not in state.pages_done and page_id not in state.skipped_pages:
            return
    if key not in state.chunks_done:
        state.chunks_done.append(key)


def _soft_invalidate_render(state: ProjectState) -> None:
    """Drop render-owned assets while keeping structural cache (extract/storyboard).

    Content-policy ``skipped`` entries are preserved: the source text did not
    change, so re-attempting those panels only burns quota.
    """
    state.panels_done = []
    state.stale_panels = []
    state.generated.panels = {}
    state.generated.portraits = {}
    state.pages_done = []
    state.stale_pages = []
    state.generated.pages = {}
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

    invalid_pages = [
        page_id
        for page_id, generated in state.generated.pages.items()
        if not _is_within(generated.local, output_dir) or not Path(generated.local).is_file()
    ]
    for page_id in invalid_pages:
        state.generated.pages.pop(page_id, None)
        changed = True
    stale_pages_done = [
        page_id for page_id in state.pages_done if page_id not in state.generated.pages
    ]
    for page_id in stale_pages_done:
        state.pages_done.remove(page_id)
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
    if invalid_pages or stale_pages_done:
        logger.warning(
            "reconcile: removed %d invalid page record(s)",
            len(invalid_pages) + len(stale_pages_done),
        )
    state.save(state_path)


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
            render_mode=render_mode,
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
    render_mode: str | None = None,
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
            layout/export still run over the full project. Ignored in
            ``finished_page`` mode.
        cancel_check: optional callable polled at chunk/panel checkpoints;
            raises ``PipelineCancelled`` when it returns true.
        render_mode: ``"finished_page"`` (default, one prompt/image per page,
            no LayoutEngine collage) or ``"panel_compose"`` (legacy
            storyboard -> panels -> LayoutEngine grid path). Falls back to
            ``core.config.render_mode()`` when unset.

    Returns:
        A ``ComicProject`` with the final state, produced page paths, and PDF.
    """
    output_dir = Path(output_dir)
    (output_dir / "panels").mkdir(parents=True, exist_ok=True)
    (output_dir / "assets" / "portraits").mkdir(parents=True, exist_ok=True)
    pages_dir = output_dir / "pages"
    panel_key_filter = set(panel_keys) if panel_keys is not None else None
    mode = render_mode or config_render_mode()
    page_size = finished_page_size()

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
        render_mode=mode,
        page_size=page_size,
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

    soft_invalidated_this_run = False
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
            soft_invalidated_this_run = True
            state.render_fingerprint = render
            state.model_snapshot = snapshot
        else:
            state = persisted
    else:
        state = _fresh_state()
    if soft_invalidated_this_run and panel_key_filter is not None:
        logger.info("render fingerprint changed: ignoring panel_keys filter, redrawing all panels")
        panel_key_filter = None
    state.project_id = project_id
    state.render_mode = mode

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
        pageset = state.page_cache.get(key)

        # Fully planned chunk: cached, marked done, every panel/page present on
        # disk. Re-running reuses the cache so the billable chat API is never
        # re-called.
        if mode == "finished_page":
            if (
                pageset is not None
                and key in set(state.chunks_done)
                and _page_chunk_complete(state, pageset, output_dir)
                and panel_key_filter is None
            ):
                _report("resume", _pct())
                continue
        elif (
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

        if mode == "finished_page":
            if pageset is None:
                state.stage = "page_plan"
                try:
                    with perf.measure("page_plan"):
                        pageset = await plan_comic_pages(chunk, elements, chat=chat)
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
                state.page_cache[key] = pageset
                state.save(state_path)
                _report("page_plan", _pct())

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

            state.stage = "pages"
            check_cancel(cancel_check)
            for page_index, plan in enumerate(pageset.pages):
                page_id = plan.page_id
                if not _page_needs_generation(state, page_id):
                    continue
                check_cancel(cancel_check)
                prompt = render_finished_page_prompt(
                    plan,
                    characters_by_name=state.characters,
                    settings_by_name=state.settings,
                    style_guide=effective_style,
                )
                refs = [
                    state.characters[name].portrait_local
                    for name in _page_reference_names(plan)
                    if name in state.characters and state.characters[name].portrait_local
                ]
                refs = [ref for ref in refs if _is_within(ref, output_dir) and Path(ref).is_file()]
                stricter_attempted = False
                while True:
                    try:
                        async with image_semaphore:
                            with perf.measure("page"):
                                out = await image.generate_single_image(
                                    prompt, reference_image_paths=refs, size=page_size
                                )
                        break
                    except Exception as exc:  # noqa: BLE001 — preserve policy skip behavior
                        if is_content_policy_rejection(exc):
                            logger.warning(
                                "page %s skipped: content filter rejected it (%s)", page_id, exc
                            )
                            if page_id not in state.skipped_pages:
                                state.skipped_pages.append(page_id)
                            if page_id in state.stale_pages:
                                state.stale_pages = [k for k in state.stale_pages if k != page_id]
                            state.save(state_path)
                            out = None
                            break
                        if not stricter_attempted:
                            stricter_attempted = True
                            prompt = render_finished_page_prompt(
                                plan,
                                characters_by_name=state.characters,
                                settings_by_name=state.settings,
                                style_guide=effective_style,
                                strict=True,
                            )
                            logger.warning(
                                "page %s image failed (%s); retrying once with stricter prompt",
                                page_id,
                                exc,
                            )
                            continue
                        raise
                if out is None:
                    continue
                pages_dir.mkdir(parents=True, exist_ok=True)
                local = _page_asset_path(pages_dir, ci, page_index)
                await asyncio.to_thread(out.save, str(local))
                state.generated.pages[page_id] = GeneratedPage(
                    local=str(local),
                    page_id=page_id,
                    unit_index=ci,
                    page_index=page_index,
                    mode="finished",
                )
                _mark_page_done(state, page_id)
                state.save(state_path)
                _report("pages", _pct())

            _mark_page_chunk_done_if_complete(state, key, pageset)
            state.save(state_path)
            _report("pages", _pct())
            continue

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
            # Same name set for L1 prompt subjects and L2/L3 refs so a model that
            # fills only one of characters_present / reference_characters cannot
            # silently desync text conditioning from portrait conditioning.
            panel_names = _panel_reference_names(panel)
            chars = [state.characters[n] for n in panel_names if n in state.characters]
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
                    for n in _panel_reference_names(panel)
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
                caption=panel.caption,
                sfx=panel.sfx,
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

    pdf: str | None = None
    webtoon: str | None = None
    pages: list[str] = []

    if mode == "finished_page":
        # No LayoutEngine collage: each page image was already saved directly
        # by the loop above, in reading order (page_XX.png), so export can run
        # straight over the pages directory.
        state.stage = "export"
        _report("export", max(0.90, _pct()))
        page_files = _prepare_finished_page_export(pages_dir) if pages_dir.exists() else []
        if page_files:
            with perf.measure("export"):
                pdf = ExportEngine().export_pdf(pages_dir, out=str(output_dir / "comic.pdf"))
            pages = [str(p) for p in page_files]
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
