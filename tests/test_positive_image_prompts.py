"""Agnes i2i positive-only redlines for model-facing image prompts."""

from __future__ import annotations

from itertools import product

import pytest

from core.comic.page_prompt import (
    render_finished_page_negative_prompt,
    render_finished_page_prompt,
    render_page_panel_prompt,
)
from core.schemas import (
    CharacterAsset,
    CharacterCanon,
    CharacterStage,
    ColorBible,
    ColorSwatch,
    ComicPagePlan,
    PagePanelSpec,
    Setting,
    VisualBible,
)
from tests.prompt_redlines import assert_positive_image_prompt

FORBIDDEN_INPUT = (
    'source: "倚着栏杆"',
    "speech bubble",
    "dialogue",
    "caption",
    "SFX",
    "chrome",
    "design sheet",
    "line art",
)


def _visual_bible() -> VisualBible:
    return VisualBible(
        version="bible_v3",
        style_guide="manhua muted European period",
        era="Vienna c.1900-1910",
        era_forbidden_wardrobe=["hoodie", "sports shoes"],
        color=ColorBible(
            palette=[
                ColorSwatch(name="Muted Plum", hex="#7A5C6E", usage="scene accent"),
            ],
            lighting="soft overcast",
            forbidden=["neon", "chrome"],
        ),
        characters={
            "虎妞": CharacterCanon(
                canonical_name="虎妞",
                gender="female",
                face_lock="sturdy woman, calm eyes",
                stages=[
                    CharacterStage(
                        stage="adult",
                        outfit_lock="traditional embroidered jacket",
                        hair_lock="black hair in a low bun",
                        portrait_key="虎妞@adult",
                    )
                ],
            )
        },
        content_hash="redline",
    )


def _characters() -> dict[str, CharacterAsset]:
    return {
        name: CharacterAsset(
            name=name,
            l1_prompt=l1,
            portrait_local=f"/tmp/{index}_trimmed.png",
        )
        for index, (name, l1) in enumerate(
            (
                ("虎妞", "虎妞, sturdy woman in traditional clothes"),
                ("R", "adult man, calm dark eyes, dark tailored suit"),
                ("少年", "young man, short black hair, plain tunic"),
            ),
            start=1,
        )
    }


def _settings() -> dict[str, Setting]:
    return {
        "亭子": Setting(
            name="亭子",
            scene_prompt="lakeside pavilion at dusk, warm lantern light, wooden railing",
        )
    }


def _finished_plan(*, cast: list[str], timeline: str = "") -> ComicPagePlan:
    return ComicPagePlan.model_validate(
        {
            "page_id": "p1",
            "purpose": "a quiet staged memory",
            "layout_intent": "Wide top archive; diagonal window attack",
            "timeline": timeline,
            "covers_beats": ["village_gate", "quiet_reply"],
            "panels": [
                {
                    "panel_id": "1",
                    "action": "R reads a letter at the desk",
                    "characters": cast,
                    "setting_ref": "亭子",
                    "caption": "MIDNIGHT AT THE SKY ARCHIVE",
                    "dialogue": "This map is moving.",
                    "sfx": "soft rustle",
                    "lettering_notes": "bubble near face - leave empty",
                    "timeline": timeline,
                }
            ],
            "lettering_boxes": [
                {"kind": "dialogue", "panel_id": "1", "x": 0.2, "y": 0.3, "w": 0.3, "h": 0.1}
            ],
            "reference_characters": cast,
            "setting_refs": ["亭子"],
        }
    )


def _panel_plan(*, cast: list[str], action: str) -> tuple[ComicPagePlan, PagePanelSpec]:
    plan = ComicPagePlan.model_validate(
        {
            "page_id": "p2",
            "layout_intent": "environmental_wide",
            "panels": [
                {
                    "panel_id": "g1",
                    "action": action,
                    "characters": cast,
                    "setting_ref": "亭子",
                }
            ],
            "reference_characters": cast,
            "setting_refs": ["亭子"],
        }
    )
    return plan, plan.panels[0]


@pytest.mark.parametrize(
    ("lettering", "strict", "timeline"),
    list(product(("deferred", "in_image"), (False, True), ("", "past", "liminal"))),
)
def test_finished_page_prompts_follow_positive_redlines(
    lettering: str, strict: bool, timeline: str
) -> None:
    plan = _finished_plan(cast=["虎妞", "R"], timeline=timeline)
    prompt = render_finished_page_prompt(
        plan,
        characters_by_name=_characters(),
        settings_by_name=_settings(),
        style_guide="manhua, " + ", ".join(FORBIDDEN_INPUT),
        strict=strict,
        lettering=lettering,
        visual_bible=_visual_bible(),
        reference_images=["/tmp/1_trimmed.png", "/tmp/2_trimmed.png"],
    )
    assert_positive_image_prompt(prompt)
    lowered = prompt.lower()
    assert "edge-to-edge" in lowered
    assert "painted artwork" in lowered or "fully colored" in lowered
    assert "locked identity" in lowered or "identity" in lowered
    assert "locked outfit" in lowered or "exact outfit" in lowered
    assert "stable age" in lowered or "same age" in lowered
    assert "虎妞" in prompt
    assert "Wide top archive" in prompt
    assert "MIDNIGHT AT THE SKY ARCHIVE" not in prompt
    assert "This map is moving." not in prompt
    assert "soft rustle" not in prompt
    assert "倚着栏杆" not in prompt
    assert render_finished_page_negative_prompt(strict=strict, lettering=lettering) == ""


@pytest.mark.parametrize("cast_count", (0, 1, 2))
@pytest.mark.parametrize("strict", (False, True))
def test_panel_prompts_follow_positive_redlines(cast_count: int, strict: bool) -> None:
    cast = ["虎妞", "R"][:cast_count]
    injected = "; ".join(FORBIDDEN_INPUT)
    plan, panel = _panel_plan(
        cast=cast,
        action=f"R leans over the railing; {injected}; a quiet beat lands",
    )
    characters = _characters()
    prompt = render_page_panel_prompt(
        plan,
        panel,
        characters_by_name=characters,
        settings_by_name=_settings(),
        style_guide=f"manhua; {injected}",
        visual_bible=_visual_bible(),
        reference_images=[f"/tmp/{index}_trimmed.png" for index in range(1, cast_count + 1)],
        strict=strict,
    )
    assert_positive_image_prompt(prompt)
    lowered = prompt.lower()
    assert "edge-to-edge" in lowered
    assert "painted artwork" in lowered
    assert "fully colored" in lowered
    assert "locked identity" in lowered or "identity" in lowered
    assert "locked outfit" in lowered or "exact outfit" in lowered
    if cast_count:
        assert "stable age" in lowered or "same age" in lowered
    assert (
        "scenery only" in lowered if cast_count == 0 else "the only people in the image" in lowered
    )
    if cast_count:
        assert "only once" in lowered
        assert "pose follows the action" in lowered


def test_prompt_redlines_scrub_every_forbidden_input_channel() -> None:
    injected = "; ".join(FORBIDDEN_INPUT)
    plan = _finished_plan(cast=["R"])
    plan.purpose = injected
    plan.layout_intent = injected
    plan.panels[0].action = injected
    characters = {
        "R": CharacterAsset(
            name="R",
            l1_prompt=f"adult man, calm eyes; {injected}",
        )
    }
    settings = {"亭子": Setting(name="亭子", scene_prompt=f"lakeside pavilion; {injected}")}
    prompt = render_finished_page_prompt(
        plan,
        characters_by_name=characters,
        settings_by_name=settings,
        style_guide=injected,
        visual_bible=_visual_bible(),
        lettering="in_image",
        strict=True,
    )
    assert_positive_image_prompt(prompt)
    assert "倚着栏杆" not in prompt
    assert "MIDNIGHT AT THE SKY ARCHIVE" not in prompt
    assert "This map is moving." not in prompt
    assert "soft rustle" not in prompt

    panel_plan, panel = _panel_plan(cast=["R"], action=injected)
    panel_prompt = render_page_panel_prompt(
        panel_plan,
        panel,
        characters_by_name=characters,
        settings_by_name=settings,
        style_guide=injected,
        visual_bible=_visual_bible(),
    )
    assert_positive_image_prompt(panel_prompt)
    assert "倚着栏杆" not in panel_prompt
