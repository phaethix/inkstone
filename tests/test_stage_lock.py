# tests/test_stage_lock.py
from core.comic.page_prompt import render_finished_page_prompt
from core.comic.visual_bible import (
    default_age_look_for_stage,
    ensure_stage_locks,
    infer_stage_from_text,
    resolve_panel_stage_refs,
)
from core.pipelines.creative_comic import _render_fingerprint
from core.schemas import (
    CharacterAsset,
    CharacterCanon,
    CharacterStage,
    ColorBible,
    ComicPagePlan,
    ModelSnapshot,
    VisualBible,
)


def _bible_with_girl_stages() -> VisualBible:
    return VisualBible(
        version="bible_v3",
        style_guide="period",
        era="Vienna c.1900",
        color=ColorBible(palette=[], lighting="", forbidden=[]),
        characters={
            "少女时代的她": CharacterCanon(
                canonical_name="少女时代的她",
                gender="female",
                narrative_function="letter_writer",
                face_lock="adult woman, wistful eyes",
                stages=[
                    CharacterStage(
                        stage="child",
                        outfit_lock="dark dress with apron",
                        hair_lock="dark neat braid",
                        age_look="about 13 years old",
                        portrait_key="少女时代的她@child",
                    ),
                    CharacterStage(
                        stage="adult",
                        outfit_lock="plain dark dress",
                        hair_lock="dark hair pulled back",
                        age_look="about 28 years old",
                        portrait_key="少女时代的她@adult",
                    ),
                ],
            )
        },
        content_hash="x",
    )


def test_infer_stage_from_age_cues():
    assert infer_stage_from_text("十三岁的少女站在门口") == "child"
    assert infer_stage_from_text("临终写信的女人") == "adult"
    assert infer_stage_from_text("as a teenager at boarding school") == "teen"
    assert infer_stage_from_text("R stands in the study") is None


def test_resolve_panel_stage_refs_rewrites_bare_name():
    bible = _bible_with_girl_stages()
    plan = ComicPagePlan.model_validate(
        {
            "page_id": "p1",
            "purpose": "童年初见",
            "layout_intent": "two shot",
            "panels": [
                {
                    "panel_id": "1",
                    "characters": ["少女时代的她"],
                    "action": "十三岁的少女偷看楼上",
                }
            ],
            "reference_characters": ["少女时代的她"],
        }
    )
    out = resolve_panel_stage_refs(plan, bible)
    assert out.panels[0].characters == ["少女时代的她@child"]
    assert out.reference_characters == ["少女时代的她@child"]


def test_resolve_panel_stage_refs_skips_missing_stage():
    bible = VisualBible(
        version="bible_v3",
        style_guide="period",
        color=ColorBible(palette=[], lighting="", forbidden=[]),
        characters={
            "R": CharacterCanon(
                canonical_name="R",
                gender="male",
                face_lock="adult man, calm eyes",
                stages=[
                    CharacterStage(
                        stage="adult",
                        outfit_lock="dark suit",
                        hair_lock="dark swept hair",
                        portrait_key="R@adult",
                    )
                ],
            )
        },
        content_hash="x",
    )
    plan = ComicPagePlan.model_validate(
        {
            "page_id": "p1",
            "purpose": "childhood",
            "layout_intent": "focus",
            "panels": [
                {
                    "panel_id": "1",
                    "characters": ["R"],
                    "action": "as a child playing",
                }
            ],
        }
    )
    out = resolve_panel_stage_refs(plan, bible)
    assert out.panels[0].characters == ["R"]


def test_default_age_look_and_ensure_fills():
    assert (
        "13" in default_age_look_for_stage("child")
        or "child" in default_age_look_for_stage("child").casefold()
    )
    stage = ensure_stage_locks(
        CharacterStage(stage="teen", outfit_lock="", hair_lock="", portrait_key=""),
        canon_face="calm eyes",
        canonical_name="R",
        era="Vienna c.1900",
    )
    assert stage.age_look
    assert stage.portrait_key == "R@teen"


def test_page_prompt_includes_hair_age_and_stability_line():
    bible = _bible_with_girl_stages()
    plan = ComicPagePlan.model_validate(
        {
            "page_id": "p1",
            "purpose": "meet",
            "layout_intent": "focus",
            "panels": [
                {
                    "panel_id": "1",
                    "characters": ["少女时代的她@child"],
                    "action": "girl watches",
                }
            ],
        }
    )
    text = render_finished_page_prompt(
        plan,
        characters_by_name={
            "少女时代的她": CharacterAsset(name="少女时代的她", l1_prompt="old"),
            "少女时代的她@child": CharacterAsset(name="少女时代的她@child", l1_prompt="old child"),
        },
        settings_by_name={},
        visual_bible=bible,
    )
    lower = text.lower()
    assert "dark neat braid" in lower or "braid" in lower
    assert "13" in text or "age" in lower
    assert "hair color" in lower or "hair length" in lower


def test_render_fingerprint_omits_the_manual_stage_lock_token():
    snapshot = ModelSnapshot(chat="chat", t2i="image", i2i="image")
    fp = _render_fingerprint(
        "style",
        snapshot=snapshot,
        panel_continuity=False,
        l3_enabled=False,
        render_mode="finished_page",
        page_size="1024x1536",
        bible_version="bible_v3",
        bible_hash="abc",
    )
    # Decode by recomputing expected payload shape
    import hashlib
    import json

    payload = {
        "style_guide": "style",
        "model_snapshot": snapshot.model_dump(),
        "panel_continuity": False,
        "l3_enabled": False,
        "render_mode": "finished_page",
        "page_size": "1024x1536",
        "visual_bible": "bible_v3",
        "bible_hash": "abc",
    }
    expected = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert fp == expected
    assert "stage_lock" not in payload


def test_infer_stage_rejects_story_specific_tokens():
    """Story-specific vocabulary must not mint stages for unrelated stories."""
    assert infer_stage_from_text("给他写信") is None
    assert infer_stage_from_text("丧子之痛") is None
    assert infer_stage_from_text("交际花出入舞厅") is None
    assert infer_stage_from_text("临别的车站") is None
    assert infer_stage_from_text("a boarding house by the sea") is None


def test_infer_stage_keeps_generic_cues():
    """Generic age vocabulary keeps working across any story."""
    assert infer_stage_from_text("十三岁的少女站在门口") == "child"
    assert infer_stage_from_text("十八岁的少年离家") == "teen"
    assert infer_stage_from_text("as a teenager") == "teen"
    assert infer_stage_from_text("他已是成年人") == "adult"
    assert infer_stage_from_text("白发苍苍的老人") == "elder"
    assert infer_stage_from_text("R stands in the study") is None


def test_infer_stage_ignores_character_names():
    """A character NAME like 少年 must not trigger stage inference when excluded."""
    assert infer_stage_from_text("少年拨响吉他", excluded_names=["少年"]) is None
    # Without the cast list the generic word still reads as a teen cue.
    assert infer_stage_from_text("少年拨响吉他") == "teen"


def test_resolve_panel_stage_refs_ignores_bare_cast_names():
    canon = CharacterCanon(
        canonical_name="少年",
        gender="male",
        face_lock="short dark hair",
        stages=[
            CharacterStage(stage="default", age_look="young boy"),
            CharacterStage(stage="teen", age_look="teenager"),
        ],
    )
    bible = VisualBible(style_guide="manga", characters={"少年": canon})
    plan = ComicPagePlan.model_validate(
        {
            "page_id": "p1",
            "purpose": "duet",
            "panels": [{"panel_id": "1", "characters": ["少年"], "action": "少年拨响吉他"}],
        }
    )
    rewritten = resolve_panel_stage_refs(plan, bible)
    assert rewritten.panels[0].characters == ["少年"]
