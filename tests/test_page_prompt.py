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
    assert "MIDNIGHT AT THE SKY ARCHIVE" not in text
    assert "This map is moving." not in text
    assert "CAPTION (exact):" not in text
    assert "DIALOGUE (exact):" not in text
    assert "no readable" in text.lower() or "no speech bubbles" in text.lower()
    assert "young woman, dark hair" in text
    assert "2x2" not in text.lower()  # renderer must not collapse intent to grid slogan
    assert "Wide top archive" in text


def test_deferred_prompt_omits_glyph_strings_and_forbids_readable_text():
    plan = ComicPagePlan.model_validate(
        {
            "page_id": "p0001",
            "purpose": "establish",
            "layout_intent": "wide top",
            "panels": [
                {
                    "panel_id": "1",
                    "action": "福贵 walks",
                    "characters": ["福贵"],
                    "dialogue": "你好",
                    "caption": "傍晚",
                    "lettering_notes": "bubble near face — leave empty",
                }
            ],
            "lettering_boxes": [
                {"kind": "dialogue", "panel_id": "1", "x": 0.2, "y": 0.3, "w": 0.3, "h": 0.1}
            ],
        }
    )
    chars = {"福贵": CharacterAsset(name="福贵", l1_prompt="middle-aged farmer")}
    text = render_finished_page_prompt(plan, characters_by_name=chars, settings_by_name={})
    assert "CAPTION (exact):" not in text
    assert "DIALOGUE (exact):" not in text
    assert "你好" not in text
    assert "傍晚" not in text
    assert "no readable" in text.lower() or "no speech bubbles" in text.lower()
    assert "post-processing" in text.lower() or "leave clear space" in text.lower()


def test_in_image_lettering_mode_still_includes_exact_strings():
    plan = ComicPagePlan.model_validate(
        {
            "page_id": "p0001",
            "purpose": "x",
            "layout_intent": "y",
            "panels": [{"panel_id": "1", "dialogue": "你好"}],
        }
    )
    text = render_finished_page_prompt(
        plan, characters_by_name={}, settings_by_name={}, lettering="in_image"
    )
    assert "DIALOGUE (exact): 你好" in text
