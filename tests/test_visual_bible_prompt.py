from core.comic.page_prompt import render_finished_page_prompt
from core.schemas import (
    CharacterAsset,
    CharacterCanon,
    CharacterStage,
    ColorBible,
    ColorSwatch,
    ComicPagePlan,
    VisualBible,
)


def test_finished_page_prompt_injects_color_and_face_lock():
    bible = VisualBible(
        version="bible_v1",
        style_guide="manhua muted European period",
        color=ColorBible(
            palette=[ColorSwatch(name="skin", hex="#E8C4A8", usage="skin")],
            lighting="soft even cel",
            forbidden=["neon"],
        ),
        characters={
            "R": CharacterCanon(
                canonical_name="R",
                face_lock="calm dark eyes",
                palette_notes="dark suit",
                stages=[
                    CharacterStage(
                        stage="adult",
                        outfit_lock="dark suit jacket",
                        hair_lock="dark short hair",
                        portrait_key="R",
                    )
                ],
            )
        },
        content_hash="x",
    )
    plan = ComicPagePlan.model_validate(
        {
            "page_id": "p1",
            "purpose": "meet",
            "layout_intent": "two shot",
            "panels": [{"panel_id": "1", "characters": ["R"], "action": "R sits"}],
        }
    )
    text = render_finished_page_prompt(
        plan,
        characters_by_name={"R": CharacterAsset(name="R", l1_prompt="old loose")},
        settings_by_name={},
        style_guide="IGNORE_ME_IF_BIBLE",
        visual_bible=bible,
    )
    assert "manhua muted European period" in text
    assert "#E8C4A8" in text
    assert "soft even cel" in text
    assert "neon" in text
    assert "calm dark eyes" in text
    assert "dark suit jacket" in text
    assert "do not change hair color" in text.lower() or "unless action says costume change" in text.lower()
