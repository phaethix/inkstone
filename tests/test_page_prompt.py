from core.comic.page_prompt import render_finished_page_prompt
from core.schemas import CharacterAsset, ComicPagePlan


def test_prompt_includes_layout_lettering_and_identity():
    plan = ComicPagePlan.model_validate(
        {
            "page_id": "p0001",
            "purpose": "Hook: map moves",
            "layout_intent": "Wide top archive; diagonal window attack; reaction close-up",
            "panels": [
                {
                    "panel_id": "1",
                    "role": "establishing",
                    "shape_hint": "wide",
                    "action": "Mira leans over glowing map",
                    "characters": ["Mira"],
                    "caption": "MIDNIGHT AT THE SKY ARCHIVE",
                    "dialogue": "This map is moving.",
                    "sfx": None,
                    "lettering_notes": "caption top-left; bubble near Mira, not on face",
                }
            ],
            "reference_characters": ["Mira"],
        }
    )
    chars = {"Mira": CharacterAsset(name="Mira", l1_prompt="young woman, dark hair, blue pendant")}
    text = render_finished_page_prompt(
        plan,
        characters_by_name=chars,
        settings_by_name={},
        style_guide="manhua comic style",
    )
    assert "A4 portrait" in text or "portrait comic page" in text.lower()
    assert "MIDNIGHT AT THE SKY ARCHIVE" in text
    assert "This map is moving." in text
    assert "young woman, dark hair" in text
    assert "2x2" not in text.lower()  # renderer must not collapse intent to grid slogan
    assert "Wide top archive" in text
