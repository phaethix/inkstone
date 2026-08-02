"""Deterministic finished-page image prompts from ComicPagePlan."""

from __future__ import annotations

from typing import Literal

from core.comic.identity import (
    ensure_character_l1,
    harden_human_identity_prompt,
    metaphor_identity_lock_line,
    metaphor_names_on_page,
)
from core.comic.visual_bible import (
    ANTI_CHARACTER_SHEET_LINE,
    ANTI_MULTI_AGE_COLLAGE_LINE,
    COSTUME_CHANGE_LOCK_LINE,
    PERIOD_WARDROBE_LINE,
    format_color_bible_block,
    l1_from_canon,
    parse_stage_ref,
    resolve_canonical_name,
    resolve_character_asset,
)
from core.schemas import CharacterAsset, ComicPagePlan, Setting, VisualBible


def _character_desc_for_prompt(
    name: str,
    asset: CharacterAsset,
    visual_bible: VisualBible | None,
) -> str:
    if visual_bible is not None:
        base, stage = parse_stage_ref(name)
        canon = visual_bible.characters.get(base)
        if canon is None:
            base = resolve_canonical_name(base, visual_bible)
            canon = visual_bible.characters.get(base)
        if canon is not None:
            canon_desc = l1_from_canon(canon, stage)
            if canon_desc:
                return harden_human_identity_prompt(name, canon_desc)
    return harden_human_identity_prompt(name, asset.l1_prompt or "")


def render_finished_page_prompt(
    plan: ComicPagePlan,
    *,
    characters_by_name: dict[str, CharacterAsset],
    settings_by_name: dict[str, Setting],
    style_guide: str = "",
    strict: bool = False,
    lettering: Literal["deferred", "in_image"] = "deferred",
    visual_bible: VisualBible | None = None,
) -> str:
    lines: list[str] = [
        "Finished readable manga/comic page, A4 portrait single image,",
        "dynamic panel layout with gutters (not a flat labeled grid collage),",
        "clean black ink line art, soft cel shading, flat colors,",
    ]
    if lettering == "deferred":
        lines.extend(
            [
                "NO speech bubbles, caption bars, SFX glyphs, or lettering chrome in the image,",
                "leave clean panel art only — text will be added in post-processing,",
                "do not render any readable text, letters, or glyphs (no Latin, no CJK),",
                "do not cover faces, hands, or key action with placeholders.",
            ]
        )
        if strict:
            lines.append(
                "STRICT: zero readable characters and zero bubble/caption chrome anywhere."
            )
    else:
        lines.extend(
            [
                "speech bubbles, caption boxes, and SFX lettered legibly in-image,",
                "do not cover faces, hands, or key action with text.",
            ]
        )
        if strict:
            lines.append(
                "STRICT: render every CAPTION, DIALOGUE, and SFX string exactly as "
                "specified; high-contrast legible lettering; do not omit any text."
            )
    effective_style = (
        visual_bible.style_guide
        if visual_bible is not None and visual_bible.style_guide
        else style_guide
    )
    if effective_style:
        lines.append(f"Style: {effective_style}")
    if visual_bible is not None:
        color_block = format_color_bible_block(visual_bible)
        if color_block:
            lines.append(color_block)
        lines.append(COSTUME_CHANGE_LOCK_LINE)
        lines.append(ANTI_CHARACTER_SHEET_LINE)
        lines.append(PERIOD_WARDROBE_LINE)
        lines.append(ANTI_MULTI_AGE_COLLAGE_LINE)
    lines.append(f"Page purpose: {plan.purpose}")
    lines.append(f"Layout intent: {plan.layout_intent}")
    metaphor_names = metaphor_names_on_page(plan, characters_by_name)
    if metaphor_names:
        lines.append(
            "CRITICAL character identity (Chinese nicknames are metaphorical — draw HUMANS only):"
        )
        lines.extend(metaphor_identity_lock_line(name) for name in metaphor_names)
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
            asset = resolve_character_asset(name, characters_by_name, visual_bible)
            if asset:
                ensure_character_l1(asset)
                desc = _character_desc_for_prompt(name, asset, visual_bible)
                if desc:
                    lines.append(f"  character {name}: {desc}")
        if lettering == "in_image":
            if panel.caption:
                lines.append(f"  CAPTION (exact): {panel.caption}")
            if panel.dialogue:
                lines.append(f"  DIALOGUE (exact): {panel.dialogue}")
            if panel.sfx:
                lines.append(f"  SFX (exact): {panel.sfx}")
            if panel.lettering_notes:
                lines.append(f"  lettering: {panel.lettering_notes}")
        else:
            if panel.lettering_notes:
                lines.append(f"  leave clear space for lettering: {panel.lettering_notes}")
    return "\n".join(lines)
