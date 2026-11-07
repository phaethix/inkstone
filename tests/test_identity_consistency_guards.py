"""Regression tests for the four identity-consistency data-layer fixes.

Root causes under test (2026-11 run, comic_out):

1. ``adult man`` hard-coded gender prefix contradicting a young ``age_look``
   (the boy rendered as a different age/person on every page).
2. Vacuous outfit locks ("no specific wardrobe detail given in source") that
   gave the "exact outfit declared below" line nothing to declare.
3. Page plans whose panel action mentions a character the cast omits, and
   setting prose that asserts extra people ("one young man ... one young
   woman") the cast lock then forbids — the model duplicates the only
   reference face it has.
4. Bible reconcile minting age stages (child/teen/elder) with no source
   evidence ("not referenced in source" written straight into age_look).
"""

from __future__ import annotations

from core.comic.page_prompt import render_finished_page_prompt
from core.comic.visual_bible import (
    deterministic_outfit_lock,
    enforce_page_cast,
    ensure_stage_locks,
    l1_from_canon,
    portrait_gender_era_suffix,
    prune_unsourced_age_stages,
    repair_outfit_lock,
)
from core.schemas import (
    CharacterAsset,
    CharacterCanon,
    CharacterStage,
    ComicPagePlan,
    ComicPagePlanSet,
    KeyBeat,
    KeyBeatSet,
    PagePanelSpec,
    ProjectState,
    Setting,
    VisualBible,
)


def _canon(name: str, gender: str, face: str, stages: list[CharacterStage]) -> CharacterCanon:
    return CharacterCanon(
        canonical_name=name,
        gender=gender,
        face_lock=face,
        stages=stages,
    )


def _bible(*canons: CharacterCanon) -> VisualBible:
    return VisualBible(
        style_guide="manga, full color",
        characters={c.canonical_name: c for c in canons},
    )


def test_l1_prefix_follows_young_stage_age():
    canon = _canon(
        "少年",
        "male",
        "adult man, shy smile, short dark hair",
        [CharacterStage(stage="default", age_look="young boy")],
    )
    text = l1_from_canon(canon, "default")
    assert "adult man" not in text.lower()
    assert text.lower().startswith("young boy")


def test_l1_prefix_follows_teen_stage_age():
    canon = _canon(
        "R",
        "male",
        "adult man, calm eyes",
        [CharacterStage(stage="default", age_look="about 16–18 years old, adolescent")],
    )
    text = l1_from_canon(canon, "default")
    assert "adult man" not in text.lower()
    assert "teenage boy" in text.lower()


def test_l1_keeps_adult_prefix_when_age_is_adult():
    canon = _canon(
        "R",
        "male",
        "adult man, calm eyes",
        [CharacterStage(stage="default", age_look="")],
    )
    assert l1_from_canon(canon, "default").lower().startswith("adult man")


def test_l1_female_teen_prefix():
    canon = _canon(
        "沈知意",
        "female",
        "adult woman, gentle eyes",
        [CharacterStage(stage="default", age_look="young girl")],
    )
    text = l1_from_canon(canon, "default")
    assert "adult woman" not in text.lower()
    assert text.lower().startswith("young girl")


def test_portrait_suffix_is_stage_aware():
    canon = _canon(
        "少年",
        "male",
        "adult man, shy smile",
        [CharacterStage(stage="default", age_look="young boy")],
    )
    suffix = portrait_gender_era_suffix(_bible(canon), canon, age_look="young boy")
    assert "adult man" not in suffix.lower()
    assert "young boy" in suffix.lower()


def test_deterministic_outfit_names_garments():
    outfit = deterministic_outfit_lock("少年")
    vague = ("no specific", "not specified", "matching the story setting and era")
    assert not any(v in outfit for v in vague)
    assert any(w in outfit for w in ("shirt", "jacket", "cardigan", "tunic", "trousers", "skirt"))


def test_deterministic_outfit_is_stable_per_name():
    assert deterministic_outfit_lock("少年") == deterministic_outfit_lock("少年")
    assert deterministic_outfit_lock("沈知意") != deterministic_outfit_lock("少年")


def test_outfit_disclaimer_is_treated_as_empty():
    outfit = repair_outfit_lock(
        "no specific wardrobe detail given in source; keep simple period-neutral clothing",
        era="",
        style_guide="",
        canonical_name="少年",
    )
    assert "no specific wardrobe detail" not in outfit
    assert any(w in outfit for w in ("shirt", "jacket", "cardigan", "tunic", "trousers", "skirt"))


def test_ensure_stage_locks_fills_specific_outfit():
    stage = ensure_stage_locks(
        CharacterStage(stage="default", outfit_lock=""),
        canon_face="calm eyes",
        canonical_name="沈知意",
    )
    assert "matching the story setting and era" not in stage.outfit_lock
    assert stage.outfit_lock.strip()


def test_explicit_outfit_is_never_overridden():
    stage = ensure_stage_locks(
        CharacterStage(stage="default", outfit_lock="red qipao with gold trim"),
        canon_face="calm eyes",
        canonical_name="沈知意",
    )
    assert stage.outfit_lock == "red qipao with gold trim"


def _pageset(panels: list[PagePanelSpec], refs: list[str]) -> ComicPagePlanSet:
    plan = ComicPagePlan(
        page_id="p1",
        purpose="the boy plays guitar for his friend",
        layout_intent="2x2 grid",
        panels=panels,
        reference_characters=list(refs),
    )
    return ComicPagePlanSet(pages=[plan])


def test_enforce_page_cast_adds_mentioned_character():
    pageset = _pageset(
        [
            PagePanelSpec(
                panel_id="c0000-p0000-0",
                role="beat",
                shot="medium",
                action="沈知意坐在少年身边，少年拨响吉他",
                characters=["少年"],
            )
        ],
        refs=["少年"],
    )
    guarded = enforce_page_cast(pageset, known_names={"少年", "沈知意"})
    panel = guarded.pages[0].panels[0]
    assert "沈知意" in panel.characters
    assert "沈知意" in guarded.pages[0].reference_characters
    assert guarded.pages[0].panels[0] is not pageset.pages[0].panels[0]


def test_enforce_page_cast_adds_beat_characters():
    pageset = _pageset(
        [
            PagePanelSpec(
                panel_id="c0000-p0000-0",
                role="beat",
                shot="medium",
                action="the tune drifts over the river",
                characters=["少年"],
                beat_ids=["b1"],
            )
        ],
        refs=["少年"],
    )
    beats = KeyBeatSet(
        beats=[KeyBeat(beat_id="b1", summary="chen smiles at the tune", characters=["沈知意"])]
    )
    guarded = enforce_page_cast(pageset, known_names={"少年", "沈知意"}, beats=beats)
    assert "沈知意" in guarded.pages[0].panels[0].characters


def test_enforce_page_cast_is_idempotent():
    pageset = _pageset(
        [
            PagePanelSpec(
                panel_id="c0000-p0000-0",
                role="beat",
                shot="medium",
                action="沈知意坐在少年身边",
                characters=["少年", "沈知意"],
            )
        ],
        refs=["少年", "沈知意"],
    )
    once = enforce_page_cast(pageset, known_names={"少年", "沈知意"})
    twice = enforce_page_cast(once, known_names={"少年", "沈知意"})
    assert once.pages[0].panels[0].characters == twice.pages[0].panels[0].characters


def test_setting_people_clauses_are_stripped():
    canon = _canon(
        "少年",
        "male",
        "young boy with short dark hair",
        [CharacterStage(stage="default", age_look="young boy", outfit_lock="a plain shirt")],
    )
    plan = ComicPagePlan(
        page_id="p1",
        purpose="the boy plays guitar",
        layout_intent="2x2 grid",
        panels=[
            PagePanelSpec(
                panel_id="c0001-p0000-0",
                role="beat",
                shot="wide",
                action="the boy plays under the eaves",
                characters=["少年"],
                setting_ref="inn",
            )
        ],
        reference_characters=["少年"],
    )
    settings = {
        "inn": Setting(
            name="inn",
            scene_prompt=(
                "one young man sits at the counter, one young woman reads nearby, "
                "warm lamplight over wooden tables"
            ),
        )
    }
    prompt = render_finished_page_prompt(
        plan,
        characters_by_name={},
        settings_by_name=settings,
        visual_bible=_bible(canon),
    )
    low = prompt.lower()
    assert "one young man" not in low
    assert "one young woman" not in low
    assert "warm lamplight" in low


def test_setting_clause_naming_cast_member_is_kept():
    canon = _canon(
        "少年",
        "male",
        "young boy with short dark hair",
        [CharacterStage(stage="default", age_look="young boy", outfit_lock="a plain shirt")],
    )
    plan = ComicPagePlan(
        page_id="p1",
        purpose="the boy plays guitar",
        layout_intent="2x2 grid",
        panels=[
            PagePanelSpec(
                panel_id="c0001-p0000-0",
                role="beat",
                shot="wide",
                action="the boy plays under the eaves",
                characters=["少年"],
                setting_ref="inn",
            )
        ],
        reference_characters=["少年"],
    )
    settings = {
        "inn": Setting(
            name="inn",
            scene_prompt="少年坐在窗边的木桌旁，窗外是雨巷",
        )
    }
    prompt = render_finished_page_prompt(
        plan,
        characters_by_name={},
        settings_by_name=settings,
        visual_bible=_bible(canon),
    )
    assert "少年坐在窗边" in prompt


def _state_with_variants() -> ProjectState:
    canon = CharacterCanon(
        canonical_name="沈知意",
        gender="female",
        face_lock="gentle eyes",
        stages=[
            CharacterStage(stage="default", age_look="age matching the story role"),
            CharacterStage(stage="child", age_look="not referenced in source"),
            CharacterStage(stage="teen", age_look="not referenced in source"),
            CharacterStage(stage="elder", age_look="not referenced in source"),
        ],
    )
    state = ProjectState(project_id="t")
    state.visual_bible = VisualBible(
        style_guide="manga",
        characters={"沈知意": canon},
    )
    state.characters = {
        "沈知意": CharacterAsset(name="沈知意", l1_prompt="gentle eyes"),
        "沈知意@child": CharacterAsset(name="沈知意@child", l1_prompt="child"),
        "沈知意@teen": CharacterAsset(name="沈知意@teen", l1_prompt="teen"),
        "沈知意@elder": CharacterAsset(name="沈知意@elder", l1_prompt="elder"),
    }
    return state


def test_prune_drops_unsourced_age_stages():
    state = _state_with_variants()
    dropped = prune_unsourced_age_stages(state)
    stages = {s.stage for s in state.visual_bible.characters["沈知意"].stages}
    assert stages == {"default"}
    assert set(dropped) == {"沈知意@child", "沈知意@teen", "沈知意@elder"}
    for orphan in ("沈知意@child", "沈知意@teen", "沈知意@elder"):
        assert orphan not in state.characters


def test_prune_rewrites_plan_stage_refs():
    state = _state_with_variants()
    plan = ComicPagePlan(
        page_id="p1",
        purpose="x",
        layout_intent="grid",
        panels=[
            PagePanelSpec(
                panel_id="c0000-p0000-0",
                role="beat",
                shot="wide",
                action="she waits",
                characters=["沈知意@teen"],
            )
        ],
        reference_characters=["沈知意@teen"],
    )
    state.page_cache["0"] = ComicPagePlanSet(pages=[plan])
    prune_unsourced_age_stages(state)
    guarded = state.page_cache["0"].pages[0]
    assert guarded.panels[0].characters == ["沈知意"]
    assert guarded.reference_characters == ["沈知意"]


def test_prune_keeps_sourced_stages():
    canon = CharacterCanon(
        canonical_name="R",
        gender="male",
        face_lock="calm eyes",
        stages=[
            CharacterStage(stage="default", age_look="age matching the story role"),
            CharacterStage(stage="teen", age_look="about 16–18 years old, adolescent"),
        ],
    )
    state = ProjectState(project_id="t")
    state.visual_bible = VisualBible(style_guide="manga", characters={"R": canon})
    dropped = prune_unsourced_age_stages(state)
    assert dropped == []
    assert {s.stage for s in state.visual_bible.characters["R"].stages} == {"default", "teen"}
