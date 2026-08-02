from core.comic.visual_bible import (
    DEFAULT_OUTFIT_LOCK,
    apply_reconcile,
    ensure_canon_locks,
    is_illegal_character_name,
    roles_incompatible,
    sanitize_visual_bible_state,
)
from core.schemas import (
    CharacterAsset,
    CharacterCanon,
    CharacterStage,
    ColorBible,
    ProjectState,
    VisualBible,
    VisualBibleMerge,
    VisualBibleReconcileResult,
)


def test_illegal_english_prose_name():
    assert is_illegal_character_name(
        "41-year-old Viennese novelist, athletic elegant build, glossy dark hair"
    )
    assert not is_illegal_character_name("R（小说家）")
    assert not is_illegal_character_name("老约翰")


def test_roles_incompatible_mother_daughter_and_count_novelist():
    assert roles_incompatible("女主角母亲，寡妇", "女主角，信件叙述者")
    assert roles_incompatible("帝国伯爵，情人", "著名小说家")
    assert not roles_incompatible("著名小说家", "男主角作家")


def test_apply_reconcile_demotes_incompatible_high_merge():
    state = ProjectState(
        project_id="p",
        characters={
            "R（小说家）": CharacterAsset(name="R（小说家）", role="著名小说家"),
            "帝国伯爵": CharacterAsset(name="帝国伯爵", role="帝国伯爵，年长情人"),
        },
        visual_bible=VisualBible(
            version="bible_v1",
            style_guide="period vienna",
            color=ColorBible(palette=[], lighting="", forbidden=[]),
            characters={
                "R（小说家）": CharacterCanon(
                    canonical_name="R（小说家）",
                    role="著名小说家",
                    face_lock="handsome face",
                    stages=[
                        CharacterStage(
                            stage="adult",
                            outfit_lock="suit",
                            hair_lock="dark hair",
                            portrait_key="R（小说家）",
                        )
                    ],
                )
            },
        ),
    )
    out = apply_reconcile(
        state,
        VisualBibleReconcileResult(
            merges=[
                VisualBibleMerge(
                    alias="帝国伯爵",
                    canonical="R（小说家）",
                    confidence="high",
                    reason="wrong",
                )
            ]
        ),
    )
    assert "帝国伯爵" in out.characters
    assert any(s.new_name == "帝国伯爵" for s in out.needs_review)


def test_ensure_canon_locks_fills_empty_and_strips_outfit_from_face():
    canon = CharacterCanon(
        canonical_name="R",
        face_lock="handsome face, wearing athletic hoodie",
        stages=[CharacterStage(stage="default", outfit_lock="", hair_lock="", portrait_key="R")],
    )
    fixed = ensure_canon_locks(canon)
    assert "hoodie" not in fixed.face_lock.lower()
    assert fixed.stages[0].hair_lock == "dark hair"
    assert fixed.stages[0].outfit_lock == DEFAULT_OUTFIT_LOCK


def test_ensure_canon_locks_outfit_not_style_guide():
    style_guide = "Manhua/comic style: clean black ink line art, soft cel shading, flat colors"
    canon = CharacterCanon(
        canonical_name="R",
        face_lock="handsome face",
        stages=[
            CharacterStage(stage="default", outfit_lock="", hair_lock="dark hair", portrait_key="R")
        ],
    )
    fixed = ensure_canon_locks(canon)
    assert fixed.stages[0].outfit_lock == DEFAULT_OUTFIT_LOCK
    assert "Manhua" not in fixed.stages[0].outfit_lock
    assert style_guide not in fixed.stages[0].outfit_lock


def test_ensure_canon_locks_repairs_illegal_and_empty_portrait_key():
    prose = "41-year-old Viennese novelist, athletic elegant build, glossy dark hair"
    canon = CharacterCanon(
        canonical_name="R（小说家）",
        face_lock="handsome face",
        stages=[
            CharacterStage(
                stage="adult",
                outfit_lock="suit",
                hair_lock="dark hair",
                portrait_key="",
            ),
            CharacterStage(
                stage="teen",
                outfit_lock="school",
                hair_lock="dark hair",
                portrait_key=prose,
            ),
        ],
    )
    fixed = ensure_canon_locks(canon)
    assert fixed.stages[0].portrait_key == "R（小说家）@adult"
    assert fixed.stages[1].portrait_key == "R（小说家）@teen"


def test_ensure_canon_locks_derives_hair_lock_from_canon_face():
    canon = CharacterCanon(
        canonical_name="R",
        face_lock="glossy dark hair, handsome face",
        stages=[CharacterStage(stage="default", outfit_lock="", hair_lock="", portrait_key="R")],
    )
    fixed = ensure_canon_locks(canon)
    assert fixed.stages[0].hair_lock == "glossy dark hair"


def test_sanitize_drops_stranger_woman_alias_from_mother():
    state = ProjectState(
        project_id="p",
        characters={
            "陌生女人的母亲": CharacterAsset(
                name="陌生女人的母亲",
                role="女主角母亲，寡妇",
                aliases=["寡妇", "陌生女人"],
            ),
            "陌生女人（信中叙述者）": CharacterAsset(
                name="陌生女人（信中叙述者）",
                role="女主角，信件叙述者",
                aliases=["陌生女人"],
            ),
        },
        visual_bible=VisualBible(
            version="bible_v1",
            style_guide="Manhua/comic style: clean black ink line art",
            color=ColorBible(palette=[], lighting="", forbidden=[]),
            characters={
                "陌生女人（信中叙述者）": CharacterCanon(
                    canonical_name="陌生女人（信中叙述者）",
                    role="女主角，信件叙述者",
                    aliases=["陌生女人"],
                    face_lock="pale fragile beauty",
                    stages=[
                        CharacterStage(
                            stage="default",
                            outfit_lock="simple dress",
                            hair_lock="dark hair",
                            portrait_key="陌生女人（信中叙述者）",
                        )
                    ],
                ),
                "陌生女人的母亲": CharacterCanon(
                    canonical_name="陌生女人的母亲",
                    role="女主角母亲，寡妇",
                    aliases=["寡妇", "陌生女人"],
                    face_lock="thin somber face",
                    stages=[
                        CharacterStage(
                            stage="default",
                            outfit_lock="black mourning clothes",
                            hair_lock="dark hair",
                            portrait_key="陌生女人的母亲",
                        )
                    ],
                ),
            },
        ),
    )
    assert sanitize_visual_bible_state(state) is True
    mother_asset = state.characters["陌生女人的母亲"]
    mother_canon = state.visual_bible.characters["陌生女人的母亲"]
    assert "陌生女人" not in mother_asset.aliases
    assert "陌生女人" not in mother_canon.aliases


def test_sanitize_removes_prose_character_and_bad_alias():
    prose = "41-year-old Viennese novelist, athletic elegant build, glossy dark hair"
    state = ProjectState(
        project_id="p",
        characters={
            "R（小说家）": CharacterAsset(
                name="R（小说家）",
                role="小说家",
                aliases=["帝国伯爵", prose],
            ),
            prose: CharacterAsset(name=prose, role="小说家"),
            "帝国伯爵": CharacterAsset(name="帝国伯爵", role="伯爵情人"),
        },
        visual_bible=VisualBible(
            version="bible_v1",
            style_guide="vienna",
            color=ColorBible(palette=[], lighting="", forbidden=[]),
            characters={
                "R（小说家）": CharacterCanon(
                    canonical_name="R（小说家）",
                    role="小说家",
                    aliases=["帝国伯爵", prose],
                    face_lock="face",
                    stages=[
                        CharacterStage(
                            stage="adult",
                            hair_lock="",
                            outfit_lock="",
                            portrait_key=prose,
                        )
                    ],
                )
            },
        ),
    )
    assert sanitize_visual_bible_state(state) is True
    assert prose not in state.characters
    assert state.visual_bible.version == "bible_v2"
    assert "帝国伯爵" not in state.visual_bible.characters["R（小说家）"].aliases
    assert state.visual_bible.characters["R（小说家）"].stages[0].portrait_key.startswith("R")


def test_backfill_panel_characters_from_action_and_refs():
    from core.comic.visual_bible import backfill_panel_characters
    from core.schemas import ComicPagePlan

    plan = ComicPagePlan.model_validate(
        {
            "page_id": "p1",
            "purpose": "海滩",
            "layout_intent": "三格",
            "panels": [
                {
                    "panel_id": "1",
                    "characters": [],
                    "action": "女人牵着金发男孩在海滩散步",
                }
            ],
            "reference_characters": ["陌生女人（信中叙述者）", "死去的儿子"],
        }
    )
    fixed = backfill_panel_characters(plan, ["陌生女人（信中叙述者）", "死去的儿子", "R（小说家）"])
    assert "陌生女人（信中叙述者）" in fixed.panels[0].characters
    assert "死去的儿子" in fixed.panels[0].characters
