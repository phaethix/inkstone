"""Deterministic finished-page image prompts from ComicPagePlan."""

from __future__ import annotations

import re
from typing import Literal

from core.comic.consistency import DEFAULT_PANEL_STYLE
from core.comic.identity import (
    ensure_character_l1,
    harden_human_identity_prompt,
    metaphor_identity_lock_line,
    metaphor_names_on_page,
)
from core.comic.key_beats import covers_beats_prompt_line
from core.comic.layout_diversity import ANTI_CENTER_STANDEE_LINE
from core.comic.visual_bible import (
    ANTI_MULTI_AGE_COLLAGE_LINE,
    COSTUME_CHANGE_LOCK_LINE,
    HAIR_STABILITY_LINE,
    format_color_bible_block,
    format_identity_line,
    l1_from_canon,
    page_scoped_bible,
    parse_stage_ref,
    resolve_canonical_name,
    resolve_character_asset,
    wardrobe_banline_for_bible,
)
from core.comic.voice import timeline_prompt_lines
from core.schemas import CharacterAsset, ComicPagePlan, PagePanelSpec, Setting, VisualBible

# A positive prompt that says "NO speech bubbles" still reminds a diffusion
# model that bubbles are a thing comics have, so it draws one full of
# pseudo-script ("S:cooas t9!" on page_c0000_p0000.png). The negative channel is
# where the ban actually holds. Kept free of any plan text: naming the dialogue
# here would summon the dialogue.
#
# Diegetic props need their own entries. ``DIEGETIC_TEXT_LINE`` already asks for
# blank paper, and the model still set "WIENER MORGENPOST" on a newspaper and
# gibberish on a station sign, so the props are named explicitly here.
FINISHED_PAGE_NEGATIVE_PROMPT = (
    "speech bubbles, thought bubbles, balloon tails, caption bars, caption boxes, "
    "text boxes, lettering, subtitles, watermark, signature, logo, "
    "any readable text, letters, words, characters, glyphs, numbers, "
    "pseudo-script, fake writing, gibberish text, scribbled letters, "
    "typography, calligraphy, "
    "newspaper headlines, printed columns of text, book pages with writing, "
    "signposts with writing, station signs with writing, shop signs with writing, "
    "posters with writing, labels with writing, screen text, UI text, "
    "monochrome black-and-white character sketch insets, uncolored lineart "
    "figures, rough character doodles, half-finished or unshaded panel art, "
    "character design sheet fragments inside the finished page, character design "
    "sheets, turnarounds, multi-view reference collages, model sheets, "
    "uncolored white-background lineart boxes, small sketch sub-frames, "
    "palette swatches, color charts, hex codes, reference labels, "
    "floating portrait insets, headshot cutouts copied from a design sheet, "
    "open books, sheet music, letters, notebooks, paper props, "
    "extra unnamed people, duplicate copies of a character, crowd of bystanders"
)

_NEGATIVE_PROMPT_STRICT_SUFFIX = ", absolutely no text of any kind anywhere in the image"


def render_finished_page_negative_prompt(
    *,
    strict: bool = False,
    lettering: Literal["deferred", "in_image"] = "deferred",
) -> str:
    """The negative prompt for a finished page render.

    ``in_image`` lettering asks the model to letter the page, so banning
    bubbles would fight the positive prompt; it gets no negative prompt at all.
    ``strict`` is the retry after a failed render, where the ban is the whole
    point, so it says the same thing with less room to interpret.
    """
    if lettering != "deferred":
        return ""
    if strict:
        return FINISHED_PAGE_NEGATIVE_PROMPT + _NEGATIVE_PROMPT_STRICT_SUFFIX
    return FINISHED_PAGE_NEGATIVE_PROMPT


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


def _reference_bindings(
    names: list[str],
    characters_by_name: dict[str, CharacterAsset],
    reference_images: list[str] | None,
    visual_bible: VisualBible | None = None,
) -> list[tuple[int, str]]:
    """Pair each sent reference image with the character who owns it.

    The provider receives portraits as an ordered array and does not label
    them. The prompt has to say which array slot is which person, in the same
    order the array is sent, or the model assigns the faces itself.
    """
    if not reference_images:
        return []
    owners: dict[str, str] = {}
    for name in names:
        asset = resolve_character_asset(name, characters_by_name, visual_bible)
        path = getattr(asset, "portrait_local", None) if asset is not None else None
        if path and path not in owners:
            owners[path] = name
    bindings: list[tuple[int, str]] = []
    for index, path in enumerate(reference_images, start=1):
        owner = owners.get(path)
        if owner:
            bindings.append((index, owner))
    return bindings


def render_finished_page_prompt(
    plan: ComicPagePlan,
    *,
    characters_by_name: dict[str, CharacterAsset],
    settings_by_name: dict[str, Setting],
    style_guide: str = "",
    strict: bool = False,
    lettering: Literal["deferred", "in_image"] = "deferred",
    visual_bible: VisualBible | None = None,
    reference_images: list[str] | None = None,
) -> str:
    # Character locks are page-scoped. Style, era, and color stay; an off-page
    # canon must not enter the prompt or a future key.
    page_names = list(plan.reference_characters)
    for panel in plan.panels:
        page_names.extend(panel.characters)
    visual_bible = page_scoped_bible(visual_bible, page_names)
    lines: list[str] = [
        "Finished readable manga/comic page, portrait single image,",
        "dynamic panel layout with gutters (not a flat labeled grid collage),",
        "every panel fully colored and shaded the same way, flat colors,",
        "each panel a separate framed composition with a clear empty gutter between",
        "cells: draw each person as a complete figure (whole body when standing),",
        "never slice a head, neck, or body at a gutter, and do not let any figure,",
        "hair, or prop cross, bleed, or spill into a neighboring panel,",
        "reference portraits are fully colored painted art of one person each, "
        "not a layout to copy and not a sheet to trace: painted face, hair, clothes, "
        "each character keeps the same outfit in every panel, and each prop belongs to",
        "its named owner only: only the character the action names may hold it; a prop",
        "does not hand itself to a different character,",
    ]
    roster = list(plan.reference_characters)
    for panel in plan.panels:
        roster.extend(panel.characters)
    unique_page_names = list(dict.fromkeys(name for name in roster if name))
    reference_bindings = _reference_bindings(
        unique_page_names, characters_by_name, reference_images, visual_bible
    )
    if reference_bindings:
        # An unlabeled list of portraits has no owner. The model then copies
        # the strongest face onto every figure and the page has one person.
        lines.append(
            "Each reference image is one specific person, in this order: "
            + "; ".join(
                f"reference image {index} is exactly {name}, and nobody else"
                for index, name in reference_bindings
            )
            + ". Draw that person with the face, hair, and clothes of their own "
            "numbered reference. Do not give one character another's face, hair, "
            "or outfit, and do not blend two references into one look."
        )
    scene_for_page = _page_scene(plan, settings_by_name)
    if scene_for_page:
        lines.append(f"Scene setting for the whole page: {scene_for_page}")
    if lettering == "deferred":
        # Naming the forbidden things here makes the model draw them. The ban
        # lives entirely in the negative prompt; the positive side only says
        # what the picture is.
        lines.append("picture only, empty panel art, no writing anywhere,")
        if strict:
            lines.append("STRICT: picture only, empty panels, nothing written.")
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
        lines.append(f"Style: {_strip_lineart_words(effective_style)}")
    if visual_bible is not None:
        color_block = _color_block_for_image(format_color_bible_block(visual_bible))
        if color_block:
            lines.append(color_block)
        lines.append(COSTUME_CHANGE_LOCK_LINE)
        lines.append(
            "Each character wears the exact outfit declared below in every panel: "
            "same garment, same color. Copy each character only from the reference "
            "image numbered for that character; do not redraw them and do not copy "
            "a different reference."
        )
        lines.append(wardrobe_banline_for_bible(visual_bible))
        lines.append(ANTI_MULTI_AGE_COLLAGE_LINE)
        lines.append(HAIR_STABILITY_LINE)
        lines.append(_image_safe(ANTI_CENTER_STANDEE_LINE))
    lines.append(f"Page purpose: {plan.purpose}")
    lines.append(f"Layout intent: {plan.layout_intent}")
    beat_line = _image_safe(covers_beats_prompt_line(plan) or "")
    if beat_line:
        lines.append(beat_line)
    for line in timeline_prompt_lines(getattr(plan, "timeline", "") or ""):
        lines.append(_image_safe(line))
    metaphor_names = metaphor_names_on_page(plan, characters_by_name)
    if metaphor_names:
        lines.append(
            "CRITICAL character identity (Chinese nicknames are metaphorical — draw HUMANS only):"
        )
        lines.extend(metaphor_identity_lock_line(name) for name in metaphor_names)
    # Character locks are page-scoped: one declaration per unique character.
    # Emitting the lock inside the per-panel loop repeats the same face for each
    # panel it appears in, which the image model reads as several look-alike
    # people instead of one recurring character.
    for name in unique_page_names:
        asset = resolve_character_asset(name, characters_by_name, visual_bible)
        if asset:
            ensure_character_l1(asset)
            desc = _character_desc_for_prompt(name, asset, visual_bible)
            if desc:
                lines.append(f"  character {name}: {desc}")
            if visual_bible is not None:
                base, _stage = parse_stage_ref(name)
                canon = visual_bible.characters.get(base)
                if canon is None:
                    base = resolve_canonical_name(base, visual_bible)
                    canon = visual_bible.characters.get(base)
                if canon is not None:
                    lines.append(f"  {format_identity_line(name, canon)}")
    if unique_page_names:
        cast = ", ".join(unique_page_names)
        lines.append(
            f"CRITICAL cast lock: the only people on this page are {cast}. "
            "Draw each of them once per panel they appear in, and keep them "
            "visually distinct: the locked gender, face, hairstyle and outfit "
            "declared above. Do not add a "
            "bystander, a reader, a second copy, or any person the panel action "
            "does not name. Never render two look-alike copies of the same "
            "person, never swap a character's gender, and never merge two "
            "characters into one look."
        )
    for i, panel in enumerate(plan.panels, start=1):
        lines.append(
            f"Panel {i} ({panel.panel_id}): role={panel.role}, shape={panel.shape_hint}, "
            f"shot={panel.shot}, action={_image_safe(panel.action)}"
        )
        panel_tl = getattr(panel, "timeline", "") or ""
        if panel_tl:
            for line in timeline_prompt_lines(panel_tl):
                lines.append(f"  {_image_safe(line)}")
        speaker = getattr(panel, "speaker", "") or ""
        if speaker:
            lines.append(f"  speaker={speaker}")
        if panel.setting_ref:
            setting = settings_by_name.get(panel.setting_ref)
            scene = getattr(setting, "scene_prompt", "") if setting else ""
            scene = _strip_lineart_words(scene)
            scene = _strip_setting_people(scene, list(panel.characters))
            lines.append(f"  setting={panel.setting_ref}: {scene}".rstrip(": "))
        if lettering == "in_image":
            if panel.caption:
                lines.append(f"  CAPTION (exact): {panel.caption}")
            if panel.dialogue:
                lines.append(f"  DIALOGUE (exact): {panel.dialogue}")
            if panel.sfx:
                lines.append(f"  SFX (exact): {panel.sfx}")
            if panel.lettering_notes:
                lines.append(f"  lettering: {panel.lettering_notes}")
    return "\n".join(lines)


# A positive prompt that names comic chrome draws the chrome, even in a
# negation ("no speech bubble", "not holding a letter"). These clauses are
# stripped; a letter the action actually stages ("reads a letter") stays.
_CHROME_CLAUSE_RE = re.compile(
    r"(?ix)\b(?:"
    r"speech bubbles?|thought bubbles?|bubbles?|caption bars?|caption boxes?|"
    r"sfx glyphs?|lettering chrome|lettering|post-processing|placeholders?|"
    r"diegetic|pseudo-script|blank aged paper|letterforms?|glyphs?|"
    r"readable text|readable characters?"
    r")\b"
)
_NEGATED_PROP_RE = re.compile(
    r"(?ix)\b(?:no|not|never|without|avoid|unless)\b[^.]*\b(?:"
    r"letters?|books?|newspapers?|sheet music|notebooks?"
    r")\b"
)
_LETTER_USAGE_RE = re.compile(r"(?i)\b(outlines and lettering|lettering)\b")


def _image_safe(text: str) -> str:
    """Drop clauses that name bubbles, captions, or a forbidden paper prop.

    The planner writes actions such as "a speech bubble carries her line" and
    shared lines such as "not holding a letter". Either phrase puts the object
    on the page. A clause is one comma- or semicolon-separated piece; only the
    pieces that name the object are removed, so the staging around them stays.
    An action that really stages the prop ("reads a letter") is kept.
    """
    if not text:
        return ""
    kept: list[str] = []
    # A sentence is its own clause: "She smiles. A speech bubble carries her
    # line." must lose the second sentence, not the smile.
    for clause in re.split(r"(?<=[,;])\s+|(?<=[.!?])\s+|\s+—\s+", text):
        if _CHROME_CLAUSE_RE.search(clause) or _NEGATED_PROP_RE.search(clause):
            continue
        kept.append(clause)
    return " ".join(kept).strip(" ,;")


# "Ink line art" and "design sheet" in a positive prompt become an uncolored
# sketch panel. The finished page is fully colored; those phrases are removed
# from style and setting text before it is sent.
_LINEART_PHRASE_RE = re.compile(
    r"(?i)\b(?:clean\s+)?black\s+ink\s+line\s*art\b|\bink\s+line\s*art\b|\bline\s*art\b"
    r"|\bcharacter\s+design\s+sheets?\b|\bdesign\s+sheets?\b|\bmodel\s+sheets?\b"
    r"|\bturnarounds?\b"
)


def _strip_lineart_words(text: str) -> str:
    """Remove the phrases that make a colored page sprout a sketch panel."""
    if not text:
        return ""
    cleaned = _LINEART_PHRASE_RE.sub("", text)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    cleaned = re.sub(r"\s+([,.;:])", r"\1", cleaned)
    return re.sub(r"([,;:])\s*[,;:]+", r"\1", cleaned).strip(" ,;")


# A setting description that stages *people* ("one young man sits at the
# counter, one young woman reads nearby") fights the cast lock: the cast may
# name fewer people than the prose asserts, and the model duplicates the one
# reference face it has to satisfy both. Setting prose is scenery authority;
# people are declared by the character lines only. Clauses naming a known cast
# member are kept (they describe that character's staging), the rest of the
# scenery is kept too.
_SETTING_PERSON_RE = re.compile(
    r"(?i)\b(?:one|two|three|a|an|another)\s+"
    r"(?:[a-z-]+\s+){0,2}?"
    r"(?:man|woman|boy|girl|person|figure|male|female|gentleman|lady)s?\b"
    r"[^,;.]*"
)
_SETTING_PEOPLE_PAIR_RE = re.compile(
    r"(?i)\btwo\s+(?:[a-z-]+\s+){0,2}?(?:men|women|people|figures|boys|girls)s?\b[^,;.]*"
)


_PANEL_NO_TEXT_BAN = (
    "no text, no speech bubbles, no captions, no sound effects, "
    "no letters, no numbers, no watermarks, no subtitles"
)


def render_page_panel_prompt(
    plan: ComicPagePlan,
    panel: PagePanelSpec,
    *,
    characters_by_name: dict[str, CharacterAsset],
    settings_by_name: dict[str, Setting],
    style_guide: str,
    visual_bible: VisualBible | None = None,
    reference_images: list[str] | None = None,
    strict: bool = False,
) -> str:
    """Prompt for ONE panel of a page plan, drawn as its own image.

    Identity comes from the character descriptions plus the reference images;
    the cast count is asserted so the model cannot invent extra figures, the
    action is scrubbed of people-assertions the cast does not name, and beat
    bookkeeping never reaches the image prompt.
    """
    scene = _page_scene(plan, settings_by_name)
    cast_names = [parse_stage_ref(name)[0] for name in panel.characters if name]
    descs: list[str] = []
    for name in panel.characters:
        asset = resolve_character_asset(name, characters_by_name, visual_bible)
        if asset is not None:
            descs.append(_character_desc_for_prompt(name, asset, visual_bible))
    count = len(cast_names)
    if count == 0:
        count_line = "No people in this panel; scenery only"
    elif count == 1:
        count_line = f"Exactly 1 character in this panel: {cast_names[0]}"
    else:
        count_line = f"Exactly {count} characters in this panel: {', '.join(cast_names)}"
    action = _strip_setting_people(panel.action or "", cast_names)
    if strict:
        ban = "absolutely no text or lettering of any kind anywhere in the image"
    else:
        ban = _PANEL_NO_TEXT_BAN
    if reference_images:
        bindings = _reference_bindings(
            list(panel.characters), characters_by_name, reference_images, visual_bible
        )
        owners = ", ".join(f"reference image {index} is {name}" for index, name in bindings)
        if owners:
            ban = f"{ban}; {owners}"
    style = f"{style_guide}, {DEFAULT_PANEL_STYLE}" if style_guide else DEFAULT_PANEL_STYLE
    parts = [p for p in (scene, *descs, count_line, action, ban, style) if p]
    return ", ".join(parts)


def _strip_setting_people(text: str, cast_names: list[str] | None = None) -> str:
    """Drop clauses from *setting* prose that assert extra people."""
    if not text:
        return ""
    cast = [name for name in (cast_names or []) if name]
    kept: list[str] = []
    for clause in re.split(r"(?<=[,;.])\s+", text):
        if _SETTING_PEOPLE_PAIR_RE.search(clause) or _SETTING_PERSON_RE.search(clause):
            # A clause that names a cast member describes that character's
            # staging, not an extra person: keep it.
            if not any(name and name in clause for name in cast):
                continue
        kept.append(clause)
    cleaned = " ".join(kept).strip(" ,;")
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    return cleaned


def _page_scene(plan: ComicPagePlan, settings_by_name: dict[str, Setting]) -> str:
    """One top-level setting directive for the whole page.

    A chapter's setting used to appear only as a trailing ``setting=`` line on
    each panel, where the global riverside look drowned it out and the second
    chapter still rendered a river. Lifting the distinct scene descriptions to
    the top makes the current chapter's location dominate the inherited look.
    """
    cast_names = list(plan.reference_characters)
    for panel in plan.panels:
        cast_names.extend(panel.characters)
    scenes: list[str] = []
    for panel in plan.panels:
        if not panel.setting_ref:
            continue
        setting = settings_by_name.get(panel.setting_ref)
        scene = getattr(setting, "scene_prompt", "") if setting else ""
        scene = _strip_lineart_words(scene).strip()
        scene = _strip_setting_people(scene, cast_names)
        if scene and scene not in scenes:
            scenes.append(scene)
    return "; ".join(scenes)


def _color_block_for_image(block: str) -> str:
    """A palette swatch labelled "for lettering" makes the page draw a chart."""
    if not block:
        return ""
    lines = []
    for line in block.splitlines():
        cleaned = _LETTER_USAGE_RE.sub("", line)
        cleaned = re.sub(r"\(\s*\)", "", cleaned)
        cleaned = re.sub(r"\s{2,}", " ", cleaned).rstrip()
        if cleaned.strip():
            lines.append(cleaned)
    return "\n".join(lines)
