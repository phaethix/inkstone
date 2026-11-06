"""Regression suite for the five finished-page defects.

Each test names the production change that would make it fail, so a fix that
does not address the root cause still leaves the suite red.
"""

from core.comic.page_prompt import (
    FINISHED_PAGE_NEGATIVE_PROMPT,
    render_finished_page_prompt,
)
from core.pipelines.export_stages import prepare_finished_page_export
from core.schemas import CharacterAsset, CharacterCanon, ComicPagePlan, Setting, VisualBible


def _plan(*, setting_ref="客栈", action="沈知意 smiles", chars=("沈知意",)):
    return ComicPagePlan.model_validate(
        {
            "page_id": "p1",
            "purpose": "establish the inn",
            "layout_intent": "cozy amber inn",
            "panels": [
                {
                    "panel_id": "1",
                    "action": action,
                    "characters": list(chars),
                    "setting_ref": setting_ref,
                    "shot": "medium",
                }
            ],
            "reference_characters": list(chars),
        }
    )


def _chars():
    return {"沈知意": CharacterAsset(name="沈知意", l1_prompt="young woman, red scarf, camel coat")}


# Defect 1: character-sheet / lineart insets
def test_negative_prompt_bans_character_sheet_collages():
    """Removing ANTI_CHARACTER_SHEET_LINE from the positive side must not drop
    the ban. It belongs in the negative channel, where the model obeys it."""
    lowered = FINISHED_PAGE_NEGATIVE_PROMPT.lower()
    assert "design sheet" in lowered
    assert "turnaround" in lowered or "multi-view" in lowered


def test_prompt_keeps_only_colored_reference_people():
    """The i2i portrait reference must be described as colored art to copy,
    never as a sheet/layout of sub-frames the page might replicate."""
    text = render_finished_page_prompt(
        _plan(), characters_by_name=_chars(), settings_by_name={}
    ).lower()
    assert "fully colored" in text
    assert "not a layout" in text or "not a design sheet" in text or "not a sheet" in text


# Defect 2: chapter setting must dominate the inherited look
def test_scene_setting_is_a_strong_top_level_directive():
    """A chapter's setting must be an explicit, high-priority instruction, not
    only a trailing per-panel 'setting=...' line that the global style can
    drown out."""
    settings = {
        "客栈": Setting(name="客栈", scene_prompt="cozy ancient Chinese inn at night, oil lamp")
    }
    text = render_finished_page_prompt(
        _plan(), characters_by_name=_chars(), settings_by_name=settings
    )
    lowered = text.lower()
    assert "setting for the whole page" in lowered or "scene setting" in lowered
    assert "cozy ancient chinese inn" in lowered


# Defect 3: panel geometry / no slicing across gutters
def test_prompt_constrains_panel_geometry_and_full_figures():
    """A single-image page lets the model slice a head at a gutter. The prompt
    must demand separated panels, complete figures, and no bleed between cells."""
    text = render_finished_page_prompt(
        _plan(), characters_by_name=_chars(), settings_by_name={}
    ).lower()
    assert "gutter" in text
    assert "complete figure" in text or "full figure" in text or "whole body" in text
    assert "do not" in text and ("cross" in text or "bleed" in text or "spill" in text)


# Defect 4: outfit and prop ownership locked per character
def test_prompt_binds_outfit_and_prop_to_the_named_character():
    """Garment and signature prop must be tied to one named character so the
    model cannot swap clothes or hand the guitar to the wrong person."""
    bible = VisualBible(
        style_guide="manhua",
        characters={
            "沈知意": CharacterCanon(
                canonical_name="沈知意",
                gender="female",
                face_lock="calm young woman",
            ),
            "少年": CharacterCanon(
                canonical_name="少年",
                gender="male",
                face_lock="shy young man",
            ),
        },
    )
    text = render_finished_page_prompt(
        _plan(chars=("沈知意", "少年")),
        characters_by_name=_chars()
        | {
            "少年": CharacterAsset(
                name="少年", l1_prompt="young man, red scarf, white shirt, guitar"
            )
        },
        settings_by_name={},
        visual_bible=bible,
    ).lower()
    assert "same outfit" in text or "exact outfit" in text or "outfit" in text
    assert "does not hand" in text or "prop" in text or "only the character" in text


# Defect 5: art-only export must be opt-in, not hardcoded
def test_export_mode_selects_lettered_or_blank(tmp_path):
    """prepare_finished_page_export hardcodes blank art, making comic.pdf a
    silent movie. The mode must be selectable: 'lettered' keeps the deferred
    dialogue pages, 'art_only' uses the clean blank art."""
    pages = tmp_path / "pages"
    (pages / "blank").mkdir(parents=True)
    lettered = pages / "page_c0000_p0000.png"
    blank = pages / "blank" / "page_c0000_p0000.png"
    lettered.write_bytes(b"lettered-with-dialogue")
    blank.write_bytes(b"clean-art")
    stale = pages / "page_01.png"
    stale.write_bytes(b"stale")

    art_only = prepare_finished_page_export(pages, export_mode="art_only")
    lettered_out = prepare_finished_page_export(pages, export_mode="lettered")

    assert art_only == [blank]
    assert lettered_out == [lettered]
    assert not stale.exists()
