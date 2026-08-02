# tests/test_visual_bible.py
from core.comic.visual_bible import (
    apply_reconcile,
    build_visual_sheet,
    collect_finished_page_refs,
    compute_bible_hash,
    ensure_stage_portrait_assets,
    l1_from_canon,
    parse_stage_ref,
    refresh_bible_hash,
    resolve_character_asset,
    rewrite_page_plan_names,
    rewrite_pageset_from_bible,
    sync_characters_from_bible,
)
from core.schemas import (
    CharacterAsset,
    CharacterCanon,
    CharacterStage,
    ColorBible,
    ColorSwatch,
    ComicPagePlan,
    ComicPagePlanSet,
    ProjectState,
    VisualBible,
    VisualBibleMerge,
    VisualBibleReconcileResult,
    VisualBibleStageLink,
)


def test_parse_stage_ref():
    assert parse_stage_ref("陌生女人@teen") == ("陌生女人", "teen")
    assert parse_stage_ref("R") == ("R", "default")


def test_bible_hash_stable_and_sensitive():
    bible = VisualBible(
        version="bible_v1",
        style_guide="manhua",
        color=ColorBible(
            palette=[ColorSwatch(name="ink", hex="#111111", usage="lines")],
            lighting="soft",
            forbidden=["neon"],
        ),
        characters={
            "R": CharacterCanon(
                canonical_name="R",
                face_lock="face A",
                palette_notes="suit",
                stages=[
                    CharacterStage(
                        stage="adult",
                        outfit_lock="dark suit",
                        hair_lock="dark hair",
                        portrait_key="R",
                    )
                ],
            )
        },
    )
    h1 = compute_bible_hash(bible)
    bible2 = bible.model_copy(update={"style_guide": "watercolor"})
    assert compute_bible_hash(bible2) != h1
    refreshed = refresh_bible_hash(bible)
    assert compute_bible_hash(refreshed) == h1
    assert refreshed.content_hash == h1


def test_apply_high_confidence_merge_and_low_to_review():
    state = ProjectState(
        project_id="p",
        characters={
            "R": CharacterAsset(name="R", role="writer"),
            "李先生": CharacterAsset(name="李先生", role="man"),
            "路人": CharacterAsset(name="路人", role="extra"),
        },
        visual_bible=VisualBible(
            version="bible_v1",
            style_guide="manhua",
            color=ColorBible(palette=[], lighting="soft", forbidden=[]),
            characters={
                "R": CharacterCanon(canonical_name="R", face_lock="f", stages=[]),
            },
        ),
    )
    result = VisualBibleReconcileResult(
        merges=[
            VisualBibleMerge(alias="李先生", canonical="R", confidence="high", reason="same"),
            VisualBibleMerge(alias="路人", canonical="R", confidence="low", reason="unsure"),
        ],
        stages=[],
        keeps=[],
    )
    out = apply_reconcile(state, result)
    assert "李先生" not in out.characters
    assert (
        "李先生" in out.characters["R"].aliases
        or "李先生" in out.visual_bible.characters["R"].aliases
    )
    assert "路人" in out.characters
    assert any(s.new_name == "路人" for s in out.needs_review)


def test_apply_stage_link():
    state = ProjectState(
        project_id="p",
        characters={
            "陌生女人": CharacterAsset(name="陌生女人"),
            "女孩（叙述者）": CharacterAsset(name="女孩（叙述者）"),
        },
        visual_bible=VisualBible(
            version="bible_v1",
            style_guide="x",
            color=ColorBible(palette=[], lighting="", forbidden=[]),
            characters={
                "陌生女人": CharacterCanon(
                    canonical_name="陌生女人",
                    face_lock="soft face",
                    stages=[
                        CharacterStage(
                            stage="adult",
                            outfit_lock="dress",
                            hair_lock="long dark",
                            portrait_key="陌生女人",
                        )
                    ],
                )
            },
        ),
    )
    result = VisualBibleReconcileResult(
        stages=[
            VisualBibleStageLink(
                name="女孩（叙述者）",
                stage="teen",
                of_canonical="陌生女人",
                reason="younger",
            )
        ]
    )
    out = apply_reconcile(state, result)
    stages = {s.stage for s in out.visual_bible.characters["陌生女人"].stages}
    assert "teen" in stages


def test_apply_reconcile_creates_bible_from_canons():
    state = ProjectState(
        project_id="p",
        characters={"R": CharacterAsset(name="R", role="writer")},
        visual_bible=None,
    )
    result = VisualBibleReconcileResult(
        style_guide="manhua muted tones",
        color=ColorBible(
            palette=[ColorSwatch(name="ink", hex="#111111", usage="lines")],
            lighting="soft",
            forbidden=["neon"],
        ),
        canons=[
            CharacterCanon(
                canonical_name="R",
                face_lock="calm eyes",
                stages=[
                    CharacterStage(
                        stage="adult",
                        outfit_lock="dark suit",
                        hair_lock="dark hair",
                        portrait_key="R",
                    )
                ],
            )
        ],
    )
    out = apply_reconcile(state, result)
    assert out.visual_bible is not None
    assert out.visual_bible.style_guide == "manhua muted tones"
    assert out.visual_bible.color.lighting == "soft"
    assert out.visual_bible.color.palette[0].hex == "#111111"
    assert "R" in out.visual_bible.characters
    assert out.visual_bible.characters["R"].face_lock == "calm eyes"


def test_apply_reconcile_applies_color_patches_on_update():
    state = ProjectState(
        project_id="p",
        characters={"R": CharacterAsset(name="R")},
        visual_bible=VisualBible(
            version="bible_v1",
            style_guide="locked style",
            color=ColorBible(
                palette=[ColorSwatch(name="ink", hex="#111111", usage="lines")],
                lighting="soft",
                forbidden=[],
            ),
            characters={
                "R": CharacterCanon(canonical_name="R", face_lock="f", stages=[]),
            },
        ),
    )
    result = VisualBibleReconcileResult(
        style_guide="should not override",
        color_patches=[ColorSwatch(name="ink", hex="#222222", usage="lines")],
        color=ColorBible(
            palette=[ColorSwatch(name="replaced", hex="#999999", usage="bg")],
            lighting="harsh",
            forbidden=["red"],
        ),
        canons=[],
    )
    out = apply_reconcile(state, result)
    assert out.visual_bible.style_guide == "locked style"
    assert out.visual_bible.color.palette[0].hex == "#222222"
    assert out.visual_bible.color.lighting == "soft"
    assert len(out.visual_bible.color.palette) == 1


def test_apply_reconcile_merge_when_canonical_missing():
    state = ProjectState(
        project_id="p",
        characters={"李先生": CharacterAsset(name="李先生", role="man")},
        visual_bible=None,
    )
    result = VisualBibleReconcileResult(
        canons=[
            CharacterCanon(
                canonical_name="R",
                face_lock="calm eyes",
                stages=[
                    CharacterStage(
                        stage="adult",
                        outfit_lock="suit",
                        hair_lock="dark",
                        portrait_key="R",
                    )
                ],
            )
        ],
        merges=[
            VisualBibleMerge(alias="李先生", canonical="R", confidence="high", reason="same"),
        ],
    )
    out = apply_reconcile(state, result)
    assert "R" in out.characters
    assert "李先生" not in out.characters
    assert "李先生" in out.characters["R"].aliases


def test_sync_characters_from_bible_updates_l1():
    state = ProjectState(
        project_id="p",
        characters={"R": CharacterAsset(name="R", l1_prompt="stale")},
        visual_bible=VisualBible(
            version="bible_v1",
            style_guide="manhua",
            color=ColorBible(palette=[], lighting="", forbidden=[]),
            characters={
                "R": CharacterCanon(
                    canonical_name="R",
                    face_lock="locked face",
                    stages=[
                        CharacterStage(
                            stage="default",
                            outfit_lock="locked outfit",
                            hair_lock="locked hair",
                            portrait_key="R",
                        )
                    ],
                )
            },
        ),
    )
    sync_characters_from_bible(state)
    assert "locked face" in state.characters["R"].l1_prompt
    assert "locked outfit" in state.characters["R"].l1_prompt


def test_sync_characters_from_bible_rewrites_page_cache_aliases():
    plan = ComicPagePlan.model_validate(
        {
            "page_id": "p1",
            "purpose": "x",
            "layout_intent": "y",
            "panels": [{"panel_id": "1", "characters": ["李先生"], "action": "stands"}],
            "reference_characters": ["李先生"],
        }
    )
    pageset = ComicPagePlanSet(unit_id="u1", pages=[plan])
    state = ProjectState(
        project_id="p",
        characters={"R": CharacterAsset(name="R", l1_prompt="stale")},
        page_cache={"0": pageset},
        visual_bible=VisualBible(
            version="bible_v1",
            style_guide="manhua",
            color=ColorBible(palette=[], lighting="", forbidden=[]),
            characters={
                "R": CharacterCanon(
                    canonical_name="R",
                    face_lock="locked face",
                    aliases=["李先生"],
                    stages=[
                        CharacterStage(
                            stage="default",
                            outfit_lock="locked outfit",
                            hair_lock="locked hair",
                            portrait_key="R",
                        )
                    ],
                )
            },
        ),
    )
    sync_characters_from_bible(state)
    assert "李先生" in state.characters["R"].aliases
    fixed = state.page_cache["0"].pages[0]
    assert fixed.panels[0].characters == ["R"]
    assert fixed.reference_characters == ["R"]


def test_l1_from_canon_includes_locks():
    canon = CharacterCanon(
        canonical_name="R",
        face_lock="calm eyes",
        palette_notes="dark suit colors",
        stages=[
            CharacterStage(
                stage="adult",
                outfit_lock="dark suit",
                hair_lock="dark short hair",
                portrait_key="R",
            )
        ],
    )
    text = l1_from_canon(canon, "adult")
    assert "calm eyes" in text
    assert "dark suit" in text
    assert "dark short hair" in text


def test_rewrite_page_plan_names():
    plan = ComicPagePlan.model_validate(
        {
            "page_id": "p1",
            "purpose": "x",
            "layout_intent": "y",
            "panels": [{"panel_id": "1", "characters": ["李先生"], "action": "stands"}],
            "reference_characters": ["李先生"],
        }
    )
    fixed = rewrite_page_plan_names(plan, {"李先生": "R"})
    assert fixed.panels[0].characters == ["R"]
    assert fixed.reference_characters == ["R"]


def test_build_visual_sheet_noop():
    bible = VisualBible(
        version="bible_v1",
        style_guide="",
        color=ColorBible(palette=[], lighting="", forbidden=[]),
        characters={},
    )
    assert build_visual_sheet(bible) is None


def test_collect_refs_uses_panel_characters_and_sheet_first():
    plan = ComicPagePlan.model_validate(
        {
            "page_id": "p1",
            "purpose": "x",
            "layout_intent": "y",
            "panels": [{"panel_id": "1", "characters": ["R"], "action": "sits"}],
            "reference_characters": [],
        }
    )
    chars = {"R": CharacterAsset(name="R", portrait_local="/tmp/r.png")}
    bible = VisualBible(
        version="bible_v1",
        style_guide="",
        color=ColorBible(palette=[], lighting="", forbidden=[]),
        characters={},
        sheet_ref_local="/tmp/sheet.png",
    )
    refs = collect_finished_page_refs(plan, chars, bible, prev_blank="/tmp/prev.png")
    assert refs[0] == "/tmp/sheet.png"
    assert "/tmp/r.png" in refs


def test_ensure_stage_portrait_assets_creates_portrait_key_rows():
    state = ProjectState(
        project_id="p",
        characters={"陌生女人": CharacterAsset(name="陌生女人", l1_prompt="adult")},
        visual_bible=VisualBible(
            version="bible_v1",
            style_guide="x",
            color=ColorBible(palette=[], lighting="", forbidden=[]),
            characters={
                "陌生女人": CharacterCanon(
                    canonical_name="陌生女人",
                    face_lock="soft face",
                    stages=[
                        CharacterStage(
                            stage="teen",
                            outfit_lock="school uniform",
                            hair_lock="long dark",
                            portrait_key="陌生女人@teen",
                        )
                    ],
                )
            },
        ),
    )
    ensure_stage_portrait_assets(state)
    assert "陌生女人@teen" in state.characters
    assert "school uniform" in state.characters["陌生女人@teen"].l1_prompt


def test_resolve_character_asset_via_alias():
    bible = VisualBible(
        version="bible_v1",
        style_guide="x",
        color=ColorBible(palette=[], lighting="", forbidden=[]),
        characters={
            "R": CharacterCanon(
                canonical_name="R",
                face_lock="f",
                aliases=["李先生"],
                stages=[],
            )
        },
    )
    chars = {"R": CharacterAsset(name="R", l1_prompt="canonical")}
    asset = resolve_character_asset("李先生", chars, bible)
    assert asset is not None
    assert asset.name == "R"


def test_rewrite_pageset_from_bible():
    plan = ComicPagePlan.model_validate(
        {
            "page_id": "p1",
            "purpose": "x",
            "layout_intent": "y",
            "panels": [{"panel_id": "1", "characters": ["李先生"], "action": "stands"}],
            "reference_characters": ["李先生"],
        }
    )
    pageset = ComicPagePlanSet(unit_id="u1", pages=[plan])
    bible = VisualBible(
        version="bible_v1",
        style_guide="x",
        color=ColorBible(palette=[], lighting="", forbidden=[]),
        characters={
            "R": CharacterCanon(
                canonical_name="R",
                aliases=["李先生"],
                face_lock="f",
                stages=[],
            )
        },
    )
    fixed = rewrite_pageset_from_bible(pageset, bible)
    assert fixed.pages[0].panels[0].characters == ["R"]
