"""Identity lockdown: stop planner prose names and duplicate canons from forking faces."""

from core.comic.visual_bible import (
    canonical_portrait_ref,
    canonicalize_page_plan,
    collapse_duplicate_identities,
    is_illegal_character_name,
    match_plan_character_name,
    sanitize_visual_bible_state,
)
from core.pipelines.creative_comic import previous_page_blank
from core.schemas import (
    CharacterAsset,
    CharacterCanon,
    CharacterStage,
    ColorBible,
    ComicPagePlan,
    ComicPagePlanSet,
    GeneratedAssets,
    GeneratedPage,
    ProjectState,
    VisualBible,
)


def test_illegal_cjk_prose_names_are_rejected():
    assert is_illegal_character_name("十六岁的少女，衣衫单薄，在寒冷的地板上瑟瑟发抖，眼神执着")
    assert is_illegal_character_name("管家约翰，正在费力地搬运地毯")
    assert is_illegal_character_name("母亲，神情忐忑不安")
    assert not is_illegal_character_name("她（信件作者）")
    assert not is_illegal_character_name("陌生女人（信的主人）")
    assert not is_illegal_character_name("R（小说家）")
    assert not is_illegal_character_name("老约翰")
    assert not is_illegal_character_name("十六岁的少女")


def test_match_plan_name_maps_prose_clause_to_known_character():
    bible = VisualBible(
        version="bible_v3",
        style_guide="x",
        color=ColorBible(palette=[], lighting="", forbidden=[]),
        characters={
            "约翰": CharacterCanon(
                canonical_name="约翰",
                face_lock="grey hair",
                aliases=["老约翰", "john@adult"],
                gender="male",
                narrative_function="servant",
            ),
            "少女（信的作者）": CharacterCanon(
                canonical_name="少女（信的作者）",
                face_lock="shy teen",
                aliases=["我（叙述者）"],
                gender="female",
                narrative_function="letter_writer",
            ),
        },
    )
    known = ["约翰", "少女（信的作者）", "母亲"]
    assert match_plan_character_name("管家约翰，正在费力地搬运地毯", known, bible) == "约翰"
    assert match_plan_character_name("母亲，神情忐忑不安", known, bible) == "母亲"
    mapped = match_plan_character_name(
        "十六岁的少女，衣衫单薄，在寒冷的地板上瑟瑟发抖，眼神执着",
        known,
        bible,
    )
    assert mapped == "少女（信的作者）"
    assert match_plan_character_name("搬运工人", known, bible) is None


def test_canonicalize_page_plan_rewrites_and_drops_unknown_prose():
    plan = ComicPagePlan.model_validate(
        {
            "page_id": "p1",
            "purpose": "meet at the door",
            "layout_intent": "dialogue_grid",
            "panels": [
                {
                    "panel_id": "1",
                    "characters": ["管家约翰，正在费力地搬运地毯", "搬运工人"],
                    "action": "carries a carpet",
                },
                {"panel_id": "2", "characters": [], "action": "empty"},
            ],
            "reference_characters": ["十六岁的少女，衣衫单薄，在寒冷的地板上瑟瑟发抖，眼神执着"],
        }
    )
    bible = VisualBible(
        version="bible_v3",
        style_guide="x",
        color=ColorBible(palette=[], lighting="", forbidden=[]),
        characters={
            "约翰": CharacterCanon(canonical_name="约翰", face_lock="grey", aliases=["老约翰"]),
            "少女（信的作者）": CharacterCanon(
                canonical_name="少女（信的作者）",
                face_lock="shy",
                aliases=["我（叙述者）"],
                gender="female",
                narrative_function="letter_writer",
            ),
        },
    )
    out = canonicalize_page_plan(plan, known_names=["约翰", "少女（信的作者）"], visual_bible=bible)
    assert out.panels[0].characters == ["约翰"]
    assert "搬运工人" not in out.panels[0].characters
    assert out.reference_characters == ["少女（信的作者）"]
    assert out.panels[1].characters == ["少女（信的作者）"]


def test_collapse_parenthetical_duplicate_canons():
    state = ProjectState(
        project_id="p",
        characters={
            "R": CharacterAsset(name="R", role="novelist", l1_prompt="man A"),
            "R（男人/收信人）": CharacterAsset(
                name="R（男人/收信人）", role="novelist", l1_prompt="man B"
            ),
        },
        visual_bible=VisualBible(
            version="bible_v3",
            style_guide="x",
            color=ColorBible(palette=[], lighting="", forbidden=[]),
            characters={
                "R": CharacterCanon(
                    canonical_name="R",
                    face_lock="glossy hair",
                    aliases=["李先生"],
                    gender="male",
                    narrative_function="letter_reader",
                ),
                "R（男人/收信人）": CharacterCanon(
                    canonical_name="R（男人/收信人）",
                    face_lock="different face that must not win",
                    aliases=["你（男主）"],
                    gender="male",
                    narrative_function="letter_reader",
                ),
            },
        ),
    )
    assert collapse_duplicate_identities(state) is True
    assert "R（男人/收信人）" not in state.characters
    assert "R（男人/收信人）" not in state.visual_bible.characters
    assert "R" in state.characters
    assert "你（男主）" in state.visual_bible.characters["R"].aliases
    assert state.visual_bible.characters["R"].face_lock == "glossy hair"


def test_collapse_duplicate_letter_writer_canons():
    state = ProjectState(
        project_id="p",
        characters={
            "少女（信的作者）": CharacterAsset(name="少女（信的作者）"),
            "她（信件作者）": CharacterAsset(name="她（信件作者）"),
        },
        visual_bible=VisualBible(
            version="bible_v3",
            style_guide="x",
            color=ColorBible(palette=[], lighting="", forbidden=[]),
            characters={
                "少女（信的作者）": CharacterCanon(
                    canonical_name="少女（信的作者）",
                    face_lock="teen face",
                    aliases=["我（叙述者）", "少女（信的作者）@child"],
                    gender="female",
                    narrative_function="letter_writer",
                ),
                "她（信件作者）": CharacterCanon(
                    canonical_name="她（信件作者）",
                    face_lock="other face",
                    aliases=[],
                    gender="female",
                    narrative_function="letter_writer",
                ),
            },
        ),
    )
    assert collapse_duplicate_identities(state) is True
    assert "她（信件作者）" not in state.visual_bible.characters
    keep = state.visual_bible.characters["少女（信的作者）"]
    assert "她（信件作者）" in keep.aliases
    assert keep.face_lock == "teen face"


def test_sanitize_runs_identity_collapse():
    state = ProjectState(
        project_id="p",
        characters={
            "R": CharacterAsset(name="R"),
            "R（收信人）": CharacterAsset(name="R（收信人）"),
        },
        visual_bible=VisualBible(
            version="bible_v1",
            style_guide="x",
            color=ColorBible(palette=[], lighting="", forbidden=[]),
            characters={
                "R": CharacterCanon(
                    canonical_name="R",
                    face_lock="f",
                    gender="male",
                    stages=[
                        CharacterStage(
                            stage="adult", outfit_lock="suit", hair_lock="dark", portrait_key="R"
                        )
                    ],
                ),
                "R（收信人）": CharacterCanon(
                    canonical_name="R（收信人）",
                    face_lock="g",
                    gender="male",
                    stages=[
                        CharacterStage(
                            stage="adult",
                            outfit_lock="suit",
                            hair_lock="dark",
                            portrait_key="R（收信人）",
                        )
                    ],
                ),
            },
        ),
    )
    sanitize_visual_bible_state(state)
    assert "R（收信人）" not in state.visual_bible.characters


def test_canonical_portrait_ref_uses_base_face_for_stage_keys():
    bible = VisualBible(
        version="bible_v3",
        style_guide="x",
        color=ColorBible(palette=[], lighting="", forbidden=[]),
        characters={
            "R": CharacterCanon(
                canonical_name="R",
                face_lock="glossy hair",
                stages=[
                    CharacterStage(
                        stage="adult",
                        outfit_lock="suit",
                        hair_lock="dark",
                        portrait_key="R@adult",
                    )
                ],
            )
        },
    )
    chars = {
        "R": CharacterAsset(name="R", portrait_local="/tmp/r.png"),
        "R@adult": CharacterAsset(name="R@adult", portrait_local=None),
    }
    assert canonical_portrait_ref("R@adult", chars, bible) == "/tmp/r.png"
    assert canonical_portrait_ref("R", chars, bible) is None


def test_previous_page_blank_crosses_chunks():
    pageset = ComicPagePlanSet(
        unit_id="2",
        pages=[
            ComicPagePlan.model_validate(
                {
                    "page_id": "u2_p0001",
                    "purpose": "x",
                    "layout_intent": "y",
                    "panels": [{"panel_id": "1", "action": "walks"}],
                }
            )
        ],
    )
    state = ProjectState(
        project_id="p",
        generated=GeneratedAssets(
            pages={
                "c0000:u1_p0002": GeneratedPage(
                    local="/tmp/lettered.png",
                    blank_local="/tmp/prev-chunk.png",
                    page_id="u1_p0002",
                    unit_index=0,
                    page_index=1,
                )
            }
        ),
    )
    assert previous_page_blank(state, pageset, chunk_index=1, page_index=0) == "/tmp/prev-chunk.png"
