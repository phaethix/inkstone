# tests/test_visual_bible_v3.py
from core.comic.page_prompt import render_finished_page_prompt
from core.comic.visual_bible import (
    apply_gender_to_face_lock,
    apply_reconcile,
    classify_era,
    default_outfit_for_era,
    genders_conflict,
    infer_gender,
    infer_narrative_function,
    l1_from_canon,
    narrative_functions_incompatible,
    repair_outfit_lock,
    sanitize_visual_bible_state,
    wardrobe_banline_for_bible,
)
from core.schemas import (
    CharacterAsset,
    CharacterCanon,
    CharacterStage,
    ColorBible,
    ComicPagePlan,
    ProjectState,
    VisualBible,
    VisualBibleMerge,
    VisualBibleReconcileResult,
)


def test_infer_gender_from_role_and_aliases():
    assert (
        infer_gender(
            name="R",
            role="novelist",
            aliases=["男人（被叙述者）"],
            explicit="unknown",
        )
        == "male"
    )
    assert infer_gender(name="母亲（寡妇）", role="widow", explicit="unknown") == "female"
    assert infer_gender(name="X", role="", face_lock="calm eyes", explicit="unknown") == "unknown"


def test_apply_gender_to_face_lock_is_idempotent():
    once = apply_gender_to_face_lock("calm dark eyes", "male")
    assert once.startswith("adult man")
    twice = apply_gender_to_face_lock(once, "male")
    assert twice == once


def test_genders_and_functions_conflict():
    assert genders_conflict("male", "female") is True
    assert genders_conflict("male", "unknown") is False
    assert narrative_functions_incompatible("letter_reader", "letter_writer") is True
    assert narrative_functions_incompatible("protagonist", "extra") is False


def test_apply_reconcile_demotes_conflicting_gender_merge():
    state = ProjectState(
        project_id="t",
        characters={
            "R": CharacterAsset(name="R", role="novelist"),
            "陌生女人": CharacterAsset(name="陌生女人", role="narrator"),
        },
        visual_bible=VisualBible(
            version="bible_v2",
            style_guide="Vienna 1900 period",
            era="Vienna c.1900–1910",
            color=ColorBible(palette=[], lighting="", forbidden=[]),
            characters={
                "R": CharacterCanon(
                    canonical_name="R",
                    role="novelist",
                    gender="male",
                    narrative_function="letter_reader",
                    face_lock="calm eyes",
                    stages=[],
                ),
                "陌生女人": CharacterCanon(
                    canonical_name="陌生女人",
                    role="narrator",
                    gender="female",
                    narrative_function="letter_writer",
                    face_lock="wistful eyes",
                    stages=[],
                ),
            },
            content_hash="x",
        ),
    )
    out = apply_reconcile(
        state,
        VisualBibleReconcileResult(
            merges=[
                VisualBibleMerge(
                    alias="陌生女人",
                    canonical="R",
                    confidence="high",
                    reason="same person",
                )
            ],
            canons=[],
        ),
    )
    assert any(s.new_name == "陌生女人" and s.candidate == "R" for s in out.needs_review)
    assert "陌生女人" not in (out.visual_bible.characters["R"].aliases if out.visual_bible else [])


def test_historical_outfit_repairs_modern_tokens():
    repaired = repair_outfit_lock(
        "light-colored sporty jacket, sports shoes",
        era="Vienna c.1900–1910",
        style_guide="European period",
    )
    assert "sport" not in repaired.casefold()
    assert "period" in repaired.casefold() or "1900" in repaired


def test_contemporary_default_outfit_not_1900s():
    assert classify_era("contemporary China", "") == "contemporary"
    outfit = default_outfit_for_era("contemporary China")
    assert "20th century" not in outfit.casefold()
    assert "european" not in outfit.casefold()
    blank = repair_outfit_lock("", era="contemporary China")
    assert "20th century" not in blank.casefold()


def test_l1_leads_with_gender():
    canon = CharacterCanon(
        canonical_name="R",
        gender="male",
        face_lock="adult man, calm eyes",
        stages=[
            CharacterStage(
                stage="adult",
                outfit_lock="dark tailored suit",
                hair_lock="dark swept hair",
                portrait_key="R@adult",
            )
        ],
    )
    text = l1_from_canon(canon, "adult")
    assert text.lower().startswith("adult man")
    assert "calm eyes" in text


def test_sanitize_bumps_to_bible_v3_and_fills_gender():
    state = ProjectState(
        project_id="t",
        characters={
            "R": CharacterAsset(name="R", role="novelist", aliases=["男人（被叙述者）"]),
        },
        visual_bible=VisualBible(
            version="bible_v2",
            style_guide="Melancholic, early-20th-century European atmosphere",
            era="",
            color=ColorBible(palette=[], lighting="", forbidden=[]),
            characters={
                "R": CharacterCanon(
                    canonical_name="R",
                    role="novelist",
                    gender="unknown",
                    face_lock="calm detached expression",
                    aliases=["男人（被叙述者）"],
                    stages=[
                        CharacterStage(
                            stage="teen",
                            outfit_lock="sporty jacket and sports shoes",
                            hair_lock="",
                            portrait_key="R@teen",
                        )
                    ],
                )
            },
            content_hash="old",
        ),
    )
    assert sanitize_visual_bible_state(state) is True
    assert state.visual_bible is not None
    assert state.visual_bible.version == "bible_v3"
    assert state.visual_bible.era
    canon = state.visual_bible.characters["R"]
    assert canon.gender == "male"
    assert canon.face_lock.lower().startswith("adult man")
    assert not any("sport" in s.outfit_lock.casefold() for s in canon.stages)


def test_page_prompt_includes_era_gender_diegetic():
    bible = VisualBible(
        version="bible_v3",
        style_guide="manhua muted European period",
        era="Vienna c.1900–1910",
        color=ColorBible(palette=[], lighting="", forbidden=[]),
        characters={
            "R": CharacterCanon(
                canonical_name="R",
                gender="male",
                narrative_function="letter_reader",
                face_lock="adult man, calm dark eyes",
                stages=[],
            )
        },
        content_hash="x",
    )
    plan = ComicPagePlan.model_validate(
        {
            "page_id": "p1",
            "purpose": "reads letter",
            "layout_intent": "focus",
            "panels": [{"panel_id": "1", "characters": ["R"], "action": "R reads a letter"}],
        }
    )
    text = render_finished_page_prompt(
        plan,
        characters_by_name={"R": CharacterAsset(name="R", l1_prompt="old loose")},
        settings_by_name={},
        visual_bible=bible,
        lettering="deferred",
    )
    lower = text.lower()
    assert "diegetic" in lower or "pseudo-script" in lower or "blank aged paper" in lower
    assert "identity: r (male, letter_reader)" in lower
    assert "vienna" in lower or "period-accurate" in lower or "hoodie" in lower
    ban = wardrobe_banline_for_bible(bible)
    assert "Era lock" in ban or "period" in ban.casefold()


def test_infer_narrative_function_letter_roles():
    assert infer_narrative_function(name="陌生女人", role="叙述者") == "letter_writer"
    assert infer_narrative_function(name="老约翰", role="男仆") == "servant"
