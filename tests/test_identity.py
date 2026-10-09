"""tests/test_identity.py — character/setting identity helpers."""

from core.comic.identity import (
    apply_recorded_merges,
    build_l1_from_appearance,
    dismiss_character_alias,
    elements_with_recorded_merges,
    force_regen_panels,
    is_high_confidence_alias,
    merge_character_alias,
    merge_settings,
    offer_alias_suggestion,
    remember_merged_alias,
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
    StoryElements,
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
    assert [(s.new_name, s.candidate) for s in state.dismissed_aliases] == [("鸿渐", "方鸿渐")]
    dismiss_character_alias(state, "鸿渐", "方鸿渐")
    assert len(state.dismissed_aliases) == 1


def test_dismissed_alias_is_not_offered_again():
    state = ProjectState(project_id="p")
    suggestion = CharacterAliasSuggestion(new_name="鸿渐", candidate="方鸿渐", reason="x")
    state.needs_review = [suggestion]
    dismiss_character_alias(state, "鸿渐", "方鸿渐")
    offer_alias_suggestion(
        state,
        CharacterAliasSuggestion(
            new_name="鸿渐",
            candidate="方鸿渐",
            reason="name variant (normalized/substring match)",
            suggested=True,
        ),
    )
    assert state.needs_review == []


def test_recorded_merge_folds_the_alias_and_stays_off_the_queue():
    suggestion = CharacterAliasSuggestion(new_name="鸿渐", candidate="方鸿渐", reason="merged")
    state = ProjectState(
        project_id="p",
        characters={
            "方鸿渐": CharacterAsset(name="方鸿渐", l1_prompt="keep"),
            "鸿渐": CharacterAsset(name="鸿渐", l1_prompt="alias"),
        },
        merged_aliases=[suggestion],
        dismissed_aliases=[suggestion],
    )
    apply_recorded_merges(state)
    assert "鸿渐" not in state.characters
    assert "鸿渐" in state.characters["方鸿渐"].aliases
    assert state.dismissed_aliases == []
    apply_recorded_merges(state)
    assert len(state.merged_aliases) == 1
    offer_alias_suggestion(state, suggestion)
    assert state.needs_review == []

    elements = StoryElements(
        characters=[
            CharacterAsset(name="方鸿渐"),
            CharacterAsset(name="鸿渐"),
        ]
    )
    planned = elements_with_recorded_merges(elements, state)
    assert [character.name for character in planned.characters] == ["方鸿渐"]
    assert [character.name for character in elements.characters] == ["方鸿渐", "鸿渐"]
    remember_merged_alias(state, "鸿渐", "方鸿渐")
    assert len(state.merged_aliases) == 1


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


# --- character-consistency-and-source-fidelity regression tests ---


def test_verify_evidence_no_source_text_skips():
    from core.comic.identity import verify_evidence_against_source
    from core.schemas import Appearance, EvidenceQuote

    app = Appearance(
        appearance_evidence=[EvidenceQuote(field="hair", quote="anything", offset=0)],
    )
    assert verify_evidence_against_source(app, None) == []


def test_verify_evidence_passes_when_quote_in_source():
    from core.comic.identity import verify_evidence_against_source
    from core.schemas import Appearance, EvidenceQuote

    src = "祥子穿着灰布长衫,留着短短的头发"
    eq = EvidenceQuote(field="hair", quote="短短的头发", offset=src.index("短短的头发"))
    app = Appearance(appearance_evidence=[eq])
    assert verify_evidence_against_source(app, src) == []


def test_verify_evidence_flags_fabricated_quote():
    from core.comic.identity import verify_evidence_against_source
    from core.schemas import Appearance, EvidenceQuote

    src = "祥子穿着灰布长衫"
    app = Appearance(
        appearance_evidence=[
            EvidenceQuote(field="hair", quote="金发碧眼", offset=999),  # fabricated
        ],
    )
    assert verify_evidence_against_source(app, src) == ["hair"]


def test_ensure_character_l1_legacy_call_unchanged():
    """legacy callers that omit source_text must still work without warnings."""
    from core.comic.identity import ensure_character_l1
    from core.schemas import CharacterAsset

    char = CharacterAsset(name="A", l1_prompt="")
    ensure_character_l1(char)
    assert char.l1_prompt  # set from name-only fallback
    assert "⚠" not in char.l1_prompt  # no warning when no source provided


def test_ensure_character_l1_marks_unverified_evidence():
    from core.comic.identity import ensure_character_l1
    from core.schemas import Appearance, CharacterAsset, EvidenceQuote

    src = "他穿灰布长衫"
    char = CharacterAsset(
        name="祥子",
        role="车夫",
        appearance=Appearance(
            outfit_top="灰布长衫",
            appearance_evidence=[
                EvidenceQuote(field="outfit_top", quote="灰布长衫", offset=src.index("灰布长衫")),
                EvidenceQuote(field="eyewear", quote="戴墨镜", offset=999),
            ],
        ),
        l1_prompt="",
    )
    ensure_character_l1(char, source_text=src)
    assert "⚠ unverified evidence for: eyewear" in char.l1_prompt


def test_ensure_character_l1_marks_no_evidence():
    from core.comic.identity import ensure_character_l1
    from core.schemas import Appearance, CharacterAsset

    char = CharacterAsset(
        name="祥子",
        appearance=Appearance(hair="bald"),
        l1_prompt="",
    )
    ensure_character_l1(char, source_text="some unrelated text")
    assert "⚠ no source evidence" in char.l1_prompt


def test_ensure_character_l1_clean_when_all_evidence_verified():
    from core.comic.identity import ensure_character_l1
    from core.schemas import Appearance, CharacterAsset, EvidenceQuote

    src = "他穿灰布长衫,留着短短的头发"
    char = CharacterAsset(
        name="祥子",
        appearance=Appearance(
            hair="短短的头发",
            outfit_top="灰布长衫",
            appearance_evidence=[
                EvidenceQuote(field="hair", quote="短短的头发", offset=src.index("短短的头发")),
                EvidenceQuote(field="outfit_top", quote="灰布长衫", offset=src.index("灰布长衫")),
            ],
        ),
        l1_prompt="",
    )
    ensure_character_l1(char, source_text=src)
    assert "⚠" not in char.l1_prompt


def test_ensure_character_l1_injects_verified_quotes():
    """pipeline (source_text provided) must inject verified quotes into l1_prompt."""
    from core.comic.identity import ensure_character_l1
    from core.schemas import Appearance, CharacterAsset, EvidenceQuote

    src = "祥子的头不很大,头上永远剃得发亮"
    char = CharacterAsset(
        name="祥子",
        appearance=Appearance(
            hair="光头",
            appearance_evidence=[
                EvidenceQuote(
                    field="hair",
                    quote="头上永远剃得发亮",
                    offset=src.find("头上永远剃得发亮"),
                ),
            ],
        ),
        l1_prompt="",
    )
    ensure_character_l1(char, source_text=src)
    assert "头上永远剃得发亮" in char.l1_prompt
    assert "⚠" not in char.l1_prompt


def test_ensure_character_l1_excludes_fabricated_quote():
    """anti-fabrication: a quote not in the source must NOT be injected but flagged."""
    from core.comic.identity import ensure_character_l1
    from core.schemas import Appearance, CharacterAsset, EvidenceQuote

    src = "祥子的头不很大"
    char = CharacterAsset(
        name="祥子",
        appearance=Appearance(
            hair="紫发",
            appearance_evidence=[EvidenceQuote(field="hair", quote="一头紫发", offset=999)],
        ),
        l1_prompt="",
    )
    ensure_character_l1(char, source_text=src)
    assert "一头紫发" not in char.l1_prompt
    assert "⚠ unverified evidence for: hair" in char.l1_prompt


def test_ensure_character_l1_no_source_keeps_evidence():
    """legacy/no-source callers still carry evidence quotes into the prompt."""
    from core.comic.identity import ensure_character_l1
    from core.schemas import Appearance, CharacterAsset, EvidenceQuote

    char = CharacterAsset(
        name="祥子",
        appearance=Appearance(
            hair="光头",
            appearance_evidence=[EvidenceQuote(field="hair", quote="头上永远剃得发亮", offset=0)],
        ),
        l1_prompt="",
    )
    ensure_character_l1(char)
    assert "头上永远剃得发亮" in char.l1_prompt
