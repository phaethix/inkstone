"""core.pipelines.creative_comic — text-to-comic orchestration.

Drives the whole M2 flow for one source text:

    segment -> extract -> merge characters -> (portraits) -> storyboard
    -> per panel: build prompt (L1) + collect references (L2) -> generate
    -> face composite (L3) -> layout -> export PDF

State is persisted to ``state.json`` after every panel so a rerun resumes
from where it stopped and never regenerates an already-finished panel.

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
    GeneratedPanel,
    ProjectState,
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


def _setting_of(elements: StoryElements, ref: str | None):
    if not ref:
        return None
    for s in elements.settings:
        if s.name == ref:
            return s
    return None


async def creative_comic(
    source_txt: str,
    *,
    output_dir: str,
    project_id: str | None = None,
    chat=None,
    image=None,
    style_guide: str | None = None,
) -> ComicProject:
    """Generate a comic from ``source_txt`` into ``output_dir``.

    Args:
        source_txt: the full novel/scene text.
        output_dir: directory for panels, assets, pages, PDF, and state.json.
        project_id: identifier stored in state (defaults to the dir name).
        chat / image: provider instances (defaults from the factories).
        style_guide: optional global style appended to every panel prompt.

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

    chunks = segment_text(source_txt)
    done_chunks = set(state.chunks_done)
    prev_panel_local: str | None = None

    for ci, chunk in enumerate(chunks):
        if str(ci) in done_chunks:
            continue

        state.stage = "extract"
        elements = await extract_story_elements(chunk, chat=chat)
        state.characters, new_names = merge_characters(state.characters, elements.characters)
        # Generate a portrait only for first-seen characters.
        for name in new_names:
            asset = state.characters[name]
            prompt = asset.portrait_prompt or asset.l1_prompt
            out = await image.generate_single_image(prompt, size="1024x1024")
            ppath = output_dir / "assets" / "portraits" / f"{name}.png"
            out.save(str(ppath))
            asset.portrait_local = str(ppath)
            state.generated.portraits[name] = str(ppath)
        state.chunks_done.append(str(ci))
        state.save(state_path)

        state.stage = "storyboard"
        board = await plan_storyboard(chunk, elements, chat=chat)

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
    panel_imgs = [PanelImage(Image.open(v.local)) for _, v in items]

    pdf: str | None = None
    pages: list[str] = []
    if panel_imgs:
        pages = LayoutEngine().compose(panel_imgs, pages_dir, layout_mode="page")
        state.stage = "export"
        pdf = ExportEngine().export_pdf(pages_dir, out=str(output_dir / "comic.pdf"))

    state.save(state_path)
    return ComicProject(project_id=project_id, state=state, pages=pages, pdf=pdf)
