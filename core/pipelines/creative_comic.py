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

import logging
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from core.api import get_chat_provider, get_image_provider
from core.comic.consistency import ConsistencyEngine
from core.comic.export import ExportEngine
from core.comic.layout import LayoutEngine, PanelImage
from core.comic.segmentation import merge_characters, segment_text
from core.schemas import (
    ChunkCache,
    GeneratedPanel,
    ProjectState,
    Storyboard,
    StoryElements,
)
from core.screenwriter import (
    extract_story_elements,
    is_content_policy_rejection,
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


def _setting_of(elements: StoryElements, ref: str | None):
    if not ref:
        return None
    for s in elements.settings:
        if s.name == ref:
            return s
    return None


def _reconcile_state(state: ProjectState, state_path: Path) -> None:
    """Drop resume records whose panel file no longer exists on disk.

    If a generated panel is deleted between runs, its ``panel_id`` would still be
    in ``panels_done`` and get skipped on rerun — leaving a hole the layout stage
    would then fail to open. Removing only the stale panel records (not the whole
    chunk) lets the pipeline regenerate just that panel on rerun while reusing the
    cached storyboard/extraction, so the (billable) chat API is never re-called
    and every other completed panel stays skipped.
    """
    stale = [
        pid
        for pid in state.panels_done
        if pid not in state.generated.panels or not Path(state.generated.panels[pid].local).exists()
    ]
    if not stale:
        return
    for pid in stale:
        state.panels_done.remove(pid)
        state.generated.panels.pop(pid, None)
    logger.warning("reconcile: regenerating %d panel(s) with missing files: %s", len(stale), stale)
    state.save(state_path)


def _chunk_complete(state: ProjectState, board: Storyboard) -> bool:
    """True when every panel in ``board`` is generated and present on disk."""
    for p in board.panels:
        rec = state.generated.panels.get(p.panel_id)
        if rec is None or p.panel_id not in state.panels_done or not Path(rec.local).exists():
            return False
    return True


async def creative_comic(
    source_txt: str,
    *,
    output_dir: str,
    project_id: str | None = None,
    chat=None,
    image=None,
    style_guide: str | None = None,
    output_format: str = "page",
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

    Returns:
        A ``ComicProject`` with the final state, produced page paths, and PDF.
    """
    output_dir = Path(output_dir)
    (output_dir / "panels").mkdir(parents=True, exist_ok=True)
    (output_dir / "assets" / "portraits").mkdir(parents=True, exist_ok=True)
    pages_dir = output_dir / "pages"

    project_id = project_id or output_dir.name or "comic"
    state_path = output_dir / "state.json"
    state = (
        ProjectState.load(state_path)
        if state_path.exists()
        else ProjectState(project_id=project_id, source_file=str(output_dir))
    )

    chat = chat or get_chat_provider()
    image = image or get_image_provider()
    engine = ConsistencyEngine(style_guide=style_guide or "")

    _reconcile_state(state, state_path)

    chunks = segment_text(source_txt)
    prev_panel_local: str | None = None

    for ci, chunk in enumerate(chunks):
        key = str(ci)
        cached = state.chunk_cache.get(key)
        board = cached.storyboard if cached else None
        elements = cached.elements if cached else None

        # Fully planned chunk: cached, marked done, every panel present on disk.
        # Re-running reuses the cache so the billable chat API is never re-called.
        if board is not None and key in set(state.chunks_done) and _chunk_complete(state, board):
            continue

        # ---- extraction (only when not cached) ----
        if elements is None:
            state.stage = "extract"
            try:
                elements = await extract_story_elements(chunk, chat=chat)
            except Exception as exc:  # noqa: BLE001 — content rejections must not abort the run
                if is_content_policy_rejection(exc):
                    logger.warning(
                        "chunk %s skipped: content filter rejected extraction (%s)", ci, exc
                    )
                    if key not in state.skipped_chunks:
                        state.skipped_chunks.append(key)
                    state.save(state_path)
                    continue
                raise
            # Cache so a later resume does not re-pay for extraction.
            state.chunk_cache.setdefault(key, ChunkCache()).elements = elements
            state.save(state_path)

        # Merge characters; generate a portrait only for first-seen names.
        state.characters, new_names = merge_characters(state.characters, elements.characters)
        try:
            for name in new_names:
                asset = state.characters[name]
                prompt = asset.portrait_prompt or asset.l1_prompt
                out = await image.generate_single_image(prompt, size="1024x1024")
                ppath = output_dir / "assets" / "portraits" / f"{name}.png"
                out.save(str(ppath))
                asset.portrait_local = str(ppath)
                state.generated.portraits[name] = str(ppath)
        except Exception as exc:  # noqa: BLE001 — content rejections must not abort the run
            if is_content_policy_rejection(exc):
                logger.warning("chunk %s skipped: content filter rejected portrait (%s)", ci, exc)
                if key not in state.skipped_chunks:
                    state.skipped_chunks.append(key)
                state.save(state_path)
                continue
            raise
        if key not in state.chunks_done:
            state.chunks_done.append(key)
        state.save(state_path)

        # ---- storyboard (only when not cached) ----
        if board is None:
            state.stage = "storyboard"
            try:
                board = await plan_storyboard(chunk, elements, chat=chat)
            except Exception as exc:  # noqa: BLE001 — content rejections must not abort the run
                if is_content_policy_rejection(exc):
                    logger.warning(
                        "chunk %s skipped: content filter rejected storyboard (%s)", ci, exc
                    )
                    if key not in state.skipped_chunks:
                        state.skipped_chunks.append(key)
                    state.save(state_path)
                    continue
                raise
            # Cache the planned storyboard so resume reuses it (no re-planning call).
            state.chunk_cache.setdefault(key, ChunkCache()).storyboard = board
            state.save(state_path)

        # ---- panels ----
        for panel in board.panels:
            if panel.panel_id in state.panels_done:
                continue
            state.stage = "panels"
            try:
                chars = [
                    state.characters[n] for n in panel.characters_present if n in state.characters
                ]
                prompt = engine.build_panel_prompt(
                    characters=chars,
                    setting=_setting_of(elements, panel.setting_ref),
                    action=panel.action,
                    style_guide=style_guide,
                )
                refs = engine.collect_reference_images(
                    panel=panel,
                    characters_by_name=state.characters,
                    prev_panel_local=prev_panel_local,
                )
                out = await image.generate_single_image(
                    prompt, reference_image_paths=refs, size=panel.size
                )
                local = output_dir / "panels" / f"{panel.panel_id}.png"
                out.save(str(local))

                portrait_ref = next(
                    (
                        state.characters[n].portrait_local
                        for n in panel.reference_characters
                        if n in state.characters and state.characters[n].portrait_local
                    ),
                    None,
                )
                if portrait_ref:
                    composited = engine.apply_l3(str(local), portrait_ref)
                    composited.save(str(local))

                state.generated.panels[panel.panel_id] = GeneratedPanel(local=str(local))
                prev_panel_local = str(local)
            except Exception as exc:  # noqa: BLE001 — content rejections must not abort the run
                if is_content_policy_rejection(exc):
                    logger.warning(
                        "panel %s skipped: upstream content filter rejected it (%s)",
                        panel.panel_id,
                        exc,
                    )
                    if panel.panel_id not in state.skipped:
                        state.skipped.append(panel.panel_id)
                    state.save(state_path)
                    continue
                raise
            state.panels_done.append(panel.panel_id)
            state.save(state_path)

    state.stage = "layout"
    items = sorted(state.generated.panels.items(), key=lambda kv: kv[0])
    panel_imgs = []
    for pid, v in items:
        if not Path(v.local).exists():
            logger.warning("layout: panel %s missing on disk; omitting from output", pid)
            continue
        panel_imgs.append(PanelImage(Image.open(v.local)))

    pdf: str | None = None
    webtoon: str | None = None
    pages: list[str] = []
    if panel_imgs:
        engine_layout = LayoutEngine()
        if output_format == "webtoon":
            pages = engine_layout.compose(panel_imgs, pages_dir, layout_mode="webtoon")
            state.stage = "export"
            webtoon = pages[0] if pages else None
        else:
            pages = engine_layout.compose(panel_imgs, pages_dir, layout_mode="page")
            state.stage = "export"
            pdf = ExportEngine().export_pdf(pages_dir, out=str(output_dir / "comic.pdf"))

    state.save(state_path)
    return ComicProject(project_id=project_id, state=state, pages=pages, pdf=pdf, webtoon=webtoon)
