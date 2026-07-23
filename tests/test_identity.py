"""tests/test_identity.py — character/setting identity helpers."""

from core.comic.identity import (
    build_l1_from_appearance,
    dismiss_character_alias,
    force_regen_panels,
    is_high_confidence_alias,
    merge_character_alias,
    merge_settings,
)
from core.schemas import (
    Appearance,
    CharacterAliasSuggestion,
    CharacterAsset,
    ChunkCache,
    Panel,
    ProjectState,
    Setting,
    Storyboard,
)


def test_build_l1_includes_appearance_fields():
    app = Appearance(
        hair="black short hair",
        eyewear="round glasses",
        outfit_top="white shirt",
    )
    text = build_l1_from_appearance("Fang", app, role="protagonist")
    assert "Fang" in text
    assert "black short hair" in text
    assert "round glasses" in text
    assert "white shirt" in text


def test_merge_settings_keeps_first_nonempty_scene():
    existing = {"Cafe": Setting(name="Cafe", scene_prompt="warm cafe")}
    merged = merge_settings(
        existing,
        [
            Setting(name="Cafe", description="later"),
            Setting(name="Street", scene_prompt="rainy street"),
        ],
    )
    assert merged["Cafe"].scene_prompt == "warm cafe"
    assert merged["Cafe"].description == "later"
    assert merged["Street"].scene_prompt == "rainy street"


def test_high_confidence_substring_reason():
    assert is_high_confidence_alias("name variant (normalized/substring match)") is True
    assert is_high_confidence_alias("similar name (difflib match)") is False


def test_merge_alias_rewrites_storyboard_and_marks_stale():
    board = Storyboard(
        chapter_id="0",
        panels=[
            Panel(
                panel_id="p1",
                characters_present=["鸿渐"],
                reference_characters=["鸿渐"],
                action="looks at sea",
            )
        ],
    )
    state = ProjectState(
        project_id="p",
        characters={
            "方鸿渐": CharacterAsset(name="方鸿渐", l1_prompt="keep"),
            "鸿渐": CharacterAsset(name="鸿渐", l1_prompt="alias"),
        },
        chunk_cache={"0": ChunkCache(storyboard=board)},
        panels_done=["c0000-p0000"],
        needs_review=[
            CharacterAliasSuggestion(
                new_name="鸿渐",
                candidate="方鸿渐",
                reason="name variant (normalized/substring match)",
                suggested=True,
            )
        ],
    )
    stale = merge_character_alias(state, "鸿渐", "方鸿渐")
    assert "鸿渐" not in state.characters
    assert "鸿渐" in state.characters["方鸿渐"].aliases
    assert state.chunk_cache["0"].storyboard.panels[0].characters_present == ["方鸿渐"]
    assert "c0000-p0000" in stale
    assert "c0000-p0000" in state.stale_panels
    assert "c0000-p0000" not in state.panels_done
    assert state.needs_review == []


def test_dismiss_alias_only_clears_review():
    state = ProjectState(
        project_id="p",
        characters={
            "方鸿渐": CharacterAsset(name="方鸿渐"),
            "鸿渐": CharacterAsset(name="鸿渐"),
        },
        needs_review=[
            CharacterAliasSuggestion(new_name="鸿渐", candidate="方鸿渐", reason="x"),
        ],
    )
    dismiss_character_alias(state, "鸿渐", "方鸿渐")
    assert state.needs_review == []
    assert "鸿渐" in state.characters


def test_force_regen_panels_clears_done_and_skipped():
    state = ProjectState(
        project_id="p",
        panels_done=["c0000-p0000", "c0000-p0001"],
        skipped=["c0000-p0000"],
    )
    force_regen_panels(state, ["c0000-p0000"])
    assert "c0000-p0000" not in state.panels_done
    assert "c0000-p0000" not in state.skipped
    assert "c0000-p0000" in state.stale_panels
    assert "c0000-p0001" in state.panels_done
