"""A finished page must not be able to swap one character's face onto another.

The dock sample rendered 沈知意 and 少年 as the same person because the image call
handed over two unlabeled portraits, told the model to copy "the reference
portrait", and then gave both characters the same plum-scarf wardrobe. These
tests pin the three decisions that made that possible.
"""

import asyncio
from unittest.mock import patch

from core.comic.page_prompt import render_finished_page_prompt
from core.comic.visual_bible import (
    collect_finished_page_refs,
    ensure_stage_locks,
    format_color_bible_block,
    l1_from_canon,
    repair_outfit_lock,
)
from core.pipelines.creative_comic import creative_comic
from core.schemas import (
    CharacterAsset,
    CharacterCanon,
    CharacterStage,
    ColorBible,
    ColorSwatch,
    ComicPagePlan,
    ProjectState,
    VisualBible,
)
from tests.test_finished_page_pipeline import FakeChat, FakeImage, _fake_export_pdf


def _plan() -> ComicPagePlan:
    return ComicPagePlan.model_validate(
        {
            "page_id": "p2",
            "purpose": "the boy and Shen Zhiyi share the railing",
            "layout_intent": "two shot",
            "panels": [
                {
                    "panel_id": "1",
                    "action": "both lean on the railing",
                    "characters": ["沈知意", "少年"],
                }
            ],
            "reference_characters": ["沈知意", "少年"],
        }
    )


def _bible() -> VisualBible:
    return VisualBible(
        style_guide="manhua",
        color=ColorBible(
            palette=[
                ColorSwatch(
                    name="Muted Scarf Plum",
                    hex="#7A5C6E",
                    usage="scarf and clothing accent color",
                )
            ],
            lighting="soft overcast",
            forbidden=[],
        ),
        characters={
            "沈知意": CharacterCanon(
                canonical_name="沈知意",
                gender="female",
                face_lock="oval face, calm eyes",
                stages=[
                    CharacterStage(
                        stage="adult",
                        outfit_lock="cream coat, no scarf",
                        hair_lock="black hair in a low ponytail",
                        portrait_key="沈知意@adult",
                    )
                ],
            ),
            "少年": CharacterCanon(
                canonical_name="少年",
                gender="male",
                face_lock="boyish face, shy smile",
                stages=[
                    CharacterStage(
                        stage="teen",
                        outfit_lock="dark jacket",
                        hair_lock="short tousled black hair",
                        portrait_key="少年@teen",
                    )
                ],
            ),
        },
    )


def test_finished_page_prompt_numbers_each_reference_portrait():
    """'Copy the reference portrait' is ambiguous once a page has two faces."""
    text = render_finished_page_prompt(
        _plan(),
        characters_by_name={
            "沈知意": CharacterAsset(
                name="沈知意", portrait_local="/tmp/shen.png", l1_prompt="woman"
            ),
            "少年": CharacterAsset(name="少年", portrait_local="/tmp/boy.png", l1_prompt="boy"),
        },
        settings_by_name={},
        visual_bible=_bible(),
        reference_images=["/tmp/shen.png", "/tmp/boy.png"],
    )

    assert "reference image 1 is exactly 沈知意" in text
    assert "reference image 2 is exactly 少年" in text
    assert "do not give one character another's face" in text.lower()
    assert "copy the reference portrait's face" not in text.lower()


def test_a_shared_palette_swatch_cannot_enter_an_identity_prompt():
    """One plum swatch used to dress every character in the same scarf."""
    block = format_color_bible_block(_bible())

    assert "scene and lighting colors only" in block.lower()
    assert "not clothing" in block.lower()
    assert "do not dress every character" in block.lower()


def test_a_held_prop_is_not_an_outfit_lock():
    """'抱着一把旧吉他' is an action. Treating it as clothing erased the wardrobe."""
    outfit = repair_outfit_lock(
        "holding an old acoustic guitar (source: 抱着一把旧吉他)",
        canonical_name="少年",
        gender="male",
    )

    assert "guitar" not in outfit.lower()
    assert "吉他" not in outfit
    assert "skirt" not in outfit.lower()
    assert any(word in outfit for word in ("shirt", "jacket", "tunic", "trousers"))


def test_l1_drops_a_prop_outfit_and_keeps_the_source_quote():
    canon = CharacterCanon(
        canonical_name="少年",
        gender="male",
        face_lock="boyish face",
        stages=[
            CharacterStage(
                stage="teen",
                outfit_lock="holding an old acoustic guitar",
                hair_lock="short black hair",
            )
        ],
    )

    text = l1_from_canon(canon, "teen")

    assert "guitar" not in text.lower()
    assert "short black hair" in text
    assert any(word in text for word in ("shirt", "jacket", "tunic", "trousers"))


def test_stage_repair_replaces_a_prop_outfit_before_it_is_stored():
    stage = ensure_stage_locks(
        CharacterStage(stage="teen", outfit_lock="holding an old acoustic guitar"),
        canon_face="boyish face",
        canonical_name="少年",
        gender="male",
    )

    assert "guitar" not in stage.outfit_lock.lower()
    assert "skirt" not in stage.outfit_lock.lower()


def test_reference_order_follows_the_prompt_roster():
    """Image 1 in the prompt and image 1 in the request must be the same person."""
    plan = _plan()
    plan.reference_characters = ["少年", "沈知意"]
    chars = {
        "沈知意": CharacterAsset(name="沈知意", portrait_local="/tmp/shen.png"),
        "少年": CharacterAsset(name="少年", portrait_local="/tmp/boy.png"),
    }

    refs = collect_finished_page_refs(plan, chars, _bible())

    assert refs == ["/tmp/boy.png", "/tmp/shen.png"]


def test_a_stage_name_still_binds_to_its_reference_slot():
    """Plans name 少年@teen while the portrait is stored on the stage row."""
    plan = _plan()
    plan.reference_characters = ["少年@teen"]
    plan.panels[0].characters = ["少年@teen"]
    bible = _bible()
    chars = {
        "少年": CharacterAsset(name="少年", portrait_local="/tmp/base.png"),
        "少年@teen": CharacterAsset(name="少年@teen", portrait_local="/tmp/teen.png"),
    }

    refs = collect_finished_page_refs(plan, chars, bible)
    text = render_finished_page_prompt(
        plan,
        characters_by_name=chars,
        settings_by_name={},
        visual_bible=bible,
        reference_images=refs,
    )

    assert refs == ["/tmp/teen.png"]
    assert "reference image 1 is exactly 少年@teen" in text


@patch("core.comic.export.ExportEngine.export_pdf", _fake_export_pdf)
def test_an_unlabeled_page_is_repainted_once(tmp_path, monkeypatch):
    """A page painted before references were labeled must not stay on disk.

    The second resume has the binding recorded, so it does not pay for the
    image again.
    """
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "finished_page")
    source = "第一章\n福贵在村口。"
    asyncio.run(
        creative_comic(source, output_dir=str(tmp_path), chat=FakeChat(), image=FakeImage())
    )
    state = ProjectState.load(tmp_path / "state.json")
    key = next(iter(state.generated.pages))
    page = state.generated.pages[key]
    page.reference_binding = ""
    page.prompt_inputs = []
    state.save(tmp_path / "state.json")

    first = FakeImage()
    asyncio.run(creative_comic(source, output_dir=str(tmp_path), chat=FakeChat(), image=first))
    rebound = ProjectState.load(tmp_path / "state.json")

    assert first.calls == 1
    assert rebound.generated.pages[key].reference_binding == "labeled-v1"
    assert key not in rebound.stale_pages

    second = FakeImage()
    asyncio.run(creative_comic(source, output_dir=str(tmp_path), chat=FakeChat(), image=second))
    assert second.calls == 0
