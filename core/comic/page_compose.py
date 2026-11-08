"""core.comic.page_compose — local composition of page-plan panels.

panel_compose renders each panel of a page plan as its own image and assembles
the page here: grid layout plus deferred lettering drawn by PIL, so no image
model ever draws text.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from core.comic.layout import LayoutEngine, PanelImage
from core.schemas import ComicPagePlan


def compose_page_panels(
    plan: ComicPagePlan,
    panel_images: list[Image.Image],
    blank_path: Path,
    lettered_path: Path,
) -> None:
    """Write the blank (art-only) and lettered page for one planned page.

    ``panel_images`` must align with ``plan.panels``; lettering text comes from
    each panel's dialogue/caption/sfx.
    """
    if len(panel_images) != len(plan.panels):
        raise ValueError(
            f"panel image count {len(panel_images)} != plan panel count {len(plan.panels)}"
        )
    engine = LayoutEngine()
    blank = engine.compose_single_page([PanelImage(img) for img in panel_images])
    lettered = engine.compose_single_page(
        [
            PanelImage(img, dialogue=spec.dialogue, caption=spec.caption, sfx=spec.sfx)
            for img, spec in zip(panel_images, plan.panels, strict=True)
        ]
    )
    blank_path.parent.mkdir(parents=True, exist_ok=True)
    lettered_path.parent.mkdir(parents=True, exist_ok=True)
    blank.save(blank_path)
    lettered.save(lettered_path)
