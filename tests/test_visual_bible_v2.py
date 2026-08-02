from core.comic.visual_bible import (
    apply_reconcile,
    is_illegal_character_name,
    roles_incompatible,
    ensure_canon_locks,
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
        stages=[
            CharacterStage(stage="default", outfit_lock="", hair_lock="", portrait_key="R")
        ],
    )
    fixed = ensure_canon_locks(canon, style_hint="early 20th century Vienna")
    assert "hoodie" not in fixed.face_lock.lower()
    assert fixed.stages[0].hair_lock == "dark hair"
    assert fixed.stages[0].outfit_lock


def test_ensure_canon_locks_repairs_illegal_and_empty_portrait_key():
    prose = "41-year-old Viennese novelist, athletic elegant build, glossy dark hair"
    canon = CharacterCanon(
        canonical_name="R（小说家）",
        face_lock="handsome face",
        stages=[
            CharacterStage(stage="adult", outfit_lock="suit", hair_lock="dark hair", portrait_key=""),
            CharacterStage(stage="teen", outfit_lock="school", hair_lock="dark hair", portrait_key=prose),
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
    from core.comic.visual_bible import sanitize_visual_bible_state

    assert sanitize_visual_bible_state(state) is True
    assert prose not in state.characters
    assert state.visual_bible.version == "bible_v2"
    assert "帝国伯爵" not in state.visual_bible.characters["R（小说家）"].aliases
    assert state.visual_bible.characters["R（小说家）"].stages[0].portrait_key.startswith("R")
