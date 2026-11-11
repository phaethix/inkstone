"""tests/test_prompt_fidelity.py — panel prompts must pin story fidelity.

User report: generated panels contradicted the novel. Evidence traced three
leaks: (1) a cast-of-one panel still drew a second figure with a guitar;
(2) planner metaphors ("lips part between notes", "the lantern flame leans
with the note") were painted literally as a glowing note and a giant flame;
(3) portrait references carry full scene backgrounds (river, lighthouse) that
i2i copies into every panel, even indoors.
"""

from core.comic.page_prompt import render_page_panel_prompt
from core.schemas import CharacterAsset, ComicPagePlan, PagePanelSpec, Setting


def _asset(name: str, l1: str) -> CharacterAsset:
    return CharacterAsset(
        name=name,
        role="r",
        appearance={},
        l1_prompt=l1,
        portrait_prompt=f"portrait of {name}",
    )


def _inn_setting() -> Setting:
    return Setting(
        name="客栈",
        description="inn at night",
        scene_prompt="cozy traditional Chinese inn at night, wooden table, dim background",
    )


def _plan(panel: PagePanelSpec) -> ComicPagePlan:
    return ComicPagePlan(
        page_id="p",
        chapter_index=1,
        layout_intent="grid",
        panels=[panel],
        reference_characters=[],
    )


def _render(panel: PagePanelSpec) -> str:
    return render_page_panel_prompt(
        _plan(panel),
        panel,
        characters_by_name={
            "沈知意": _asset("沈知意", "a young woman"),
            "少年": _asset("少年", "a young man with an old guitar"),
        },
        settings_by_name={"客栈": _inn_setting()},
        style_guide="",
    )


def test_single_cast_panel_bans_extra_figures_positively():
    """Cast-of-one must state the sole figure and that no one else is drawn.

    c0001 p1 g1 declared cast=['沈知意'] yet the model added a guitar-carrying
    boy; the existing count line only says who to draw, never that nobody
    else may appear even as a silhouette or background passer-by.
    """
    panel = PagePanelSpec(
        panel_id="g1",
        role="reaction",
        action="Her chin rests on her palm; she smiles.",
        characters=["沈知意"],
        setting_ref="客栈",
    )
    prompt = _render(panel)
    assert "no other people" in prompt
    assert "no silhouettes" in prompt


def test_multi_cast_panel_states_exclusive_cast():
    """Multi-cast panels get the same exclusive-cast language."""
    panel = PagePanelSpec(
        panel_id="g0",
        role="dialogue",
        action="They sit across the table.",
        characters=["沈知意", "少年"],
        setting_ref="客栈",
    )
    prompt = _render(panel)
    assert "no other people" in prompt


def test_metaphor_notes_are_scrubbed_from_action():
    """Planner metaphors must not reach the image model literally.

    c0001 p0 g2 painted a glowing note at the boy's lips from "his lips part
    between notes"; "the tune unnamed" invited it. Music-note glyphs and
    floating symbol props are banned by positive instruction, and the action
    line drops the metaphor clause.
    """
    panel = PagePanelSpec(
        panel_id="g2",
        role="action",
        action=(
            "The young man tilts his head back, humming; "
            "his lips part between notes, the tune unnamed."
        ),
        characters=["少年"],
        setting_ref="客栈",
    )
    prompt = _render(panel)
    assert "no floating music notes" in prompt
    assert "no glowing symbols" in prompt
    # The metaphor clause itself is dropped from the action text.
    assert "between notes" not in prompt


def test_flame_personification_is_scrubbed():
    """ "The lantern flame leans with the note" became a waist-high magical
    flame. Flame/objects acting like living things must be rewritten to the
    plain physical fact."""
    panel = PagePanelSpec(
        panel_id="g3",
        role="detail",
        action=(
            "He lowers the guitar, still humming; "
            "the lantern flame leans with the note and settles."
        ),
        characters=["少年"],
        setting_ref="客栈",
    )
    prompt = _render(panel)
    assert "leans with the note" not in prompt
