"""Deterministic finished-page image prompts from ComicPagePlan."""

from __future__ import annotations

from core.comic.identity import ensure_character_l1
from core.schemas import CharacterAsset, ComicPagePlan, Setting


def render_finished_page_prompt(
    plan: ComicPagePlan,
    *,
    characters_by_name: dict[str, CharacterAsset],
    settings_by_name: dict[str, Setting],
    style_guide: str = "",
) -> str:
    lines: list[str] = [
        "Finished readable manga/comic page, A4 portrait single image,",
        "dynamic panel layout with gutters (not a flat labeled grid collage),",
        "clean black ink line art, soft cel shading, flat colors,",
        "speech bubbles, caption boxes, and SFX lettered legibly in-image,",
        "do not cover faces, hands, or key action with text.",
    ]
    if style_guide:
        lines.append(f"Style: {style_guide}")
    lines.append(f"Page purpose: {plan.purpose}")
    lines.append(f"Layout intent: {plan.layout_intent}")
    for i, panel in enumerate(plan.panels, start=1):
        lines.append(
            f"Panel {i} ({panel.panel_id}): role={panel.role}, shape={panel.shape_hint}, "
            f"shot={panel.shot}, action={panel.action}"
        )
        if panel.setting_ref:
            setting = settings_by_name.get(panel.setting_ref)
            scene = getattr(setting, "scene_prompt", "") if setting else ""
            lines.append(f"  setting={panel.setting_ref}: {scene}".rstrip(": "))
        for name in panel.characters:
            asset = characters_by_name.get(name)
            if asset:
                ensure_character_l1(asset)
                if asset.l1_prompt:
                    lines.append(f"  character {name}: {asset.l1_prompt}")
        if panel.caption:
            lines.append(f"  CAPTION (exact): {panel.caption}")
        if panel.dialogue:
            lines.append(f"  DIALOGUE (exact): {panel.dialogue}")
        if panel.sfx:
            lines.append(f"  SFX (exact): {panel.sfx}")
        if panel.lettering_notes:
            lines.append(f"  lettering: {panel.lettering_notes}")
    return "\n".join(lines)
