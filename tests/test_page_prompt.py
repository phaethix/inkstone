from core.comic.page_prompt import render_finished_page_prompt
from core.comic.visual_bible import bible_entries_for_page
from core.schemas import CharacterAsset, CharacterCanon, ComicPagePlan, VisualBible


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
    assert "portrait" in text.lower()
    assert "MIDNIGHT AT THE SKY ARCHIVE" not in text
    assert "This map is moving." not in text
    assert "CAPTION (exact):" not in text
    assert "DIALOGUE (exact):" not in text
    # Naming the ban in the positive prompt makes the model draw the thing.
    assert "speech bubble" not in text.lower()
    assert "picture only" in text.lower()
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
    assert "speech bubble" not in text.lower()
    assert "lettering" not in text.lower()
    assert "picture only" in text.lower()


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


def test_finished_page_prompt_locks_metaphorical_huniu():
    plan = ComicPagePlan.model_validate(
        {
            "page_id": "p0001",
            "purpose": "寿宴",
            "layout_intent": "single panel",
            "panels": [
                {
                    "panel_id": "1",
                    "action": "虎妞向父亲吹嘘祥子送的寿桃",
                    "characters": ["虎妞"],
                }
            ],
            "reference_characters": ["虎妞"],
        }
    )
    chars = {
        "虎妞": CharacterAsset(
            name="虎妞",
            role="factory owner's daughter",
            l1_prompt="虎妞, sturdy woman in traditional clothes",
        )
    }
    text = render_finished_page_prompt(plan, characters_by_name=chars, settings_by_name={})
    assert "CRITICAL character identity" in text
    assert "虎妞" in text
    assert "NOT a literal animal" in text
    assert "human character" in text
    assert "sturdy woman in traditional clothes" in text


def test_page_prompt_omits_a_character_who_is_not_on_the_page():
    bible = VisualBible(
        characters={
            "福贵": CharacterCanon(
                canonical_name="福贵", face_lock="weathered farmer UNIQUE_ON_PAGE"
            ),
            "春生": CharacterCanon(
                canonical_name="春生", face_lock="young landlord UNIQUE_OFF_PAGE"
            ),
        }
    )
    plan = ComicPagePlan.model_validate(
        {
            "page_id": "p1",
            "purpose": "arrive",
            "layout_intent": "wide establishing",
            "panels": [{"panel_id": "1", "action": "walks in", "characters": ["福贵"]}],
            "reference_characters": ["福贵"],
        }
    )
    text = render_finished_page_prompt(
        plan,
        characters_by_name={
            "福贵": CharacterAsset(name="福贵", l1_prompt="a farmer"),
        },
        settings_by_name={},
        visual_bible=bible,
    )
    assert "UNIQUE_ON_PAGE" in text
    assert "UNIQUE_OFF_PAGE" not in text
    assert list(bible_entries_for_page(bible, ["福贵"])) == ["福贵"]


def test_character_lock_is_written_once_even_when_spanning_many_panels():
    """A character appearing in 4 panels must be declared once, not 4 times.

    Repeating ``character X:`` / ``identity:`` per panel makes the image model
    read one character as several look-alike people. The lock is page-scoped.
    """
    plan = ComicPagePlan.model_validate(
        {
            "page_id": "p1",
            "purpose": "she watches the river, then turns, then reacts, then smiles",
            "layout_intent": "four panels on the same character",
            "panels": [
                {"panel_id": "a", "action": "gaze", "characters": ["沈知意"]},
                {"panel_id": "b", "action": "pull scarf", "characters": ["沈知意"]},
                {"panel_id": "c", "action": "turn to lighthouse", "characters": ["沈知意"]},
                {"panel_id": "d", "action": "smile", "characters": ["沈知意", "少年"]},
            ],
            "reference_characters": ["沈知意", "少年"],
        }
    )
    chars = {
        "沈知意": CharacterAsset(name="沈知意", l1_prompt="young woman, dark hair, scarf"),
        "少年": CharacterAsset(name="少年", l1_prompt="young man with guitar"),
    }
    text = render_finished_page_prompt(
        plan,
        characters_by_name=chars,
        settings_by_name={},
    )
    assert text.count("character 沈知意:") == 1
    assert text.count("character 少年:") == 1
    # The single lock still carries the description, and both names remain on the page.
    assert "young woman, dark hair, scarf" in text
    assert "young man with guitar" in text


def test_character_lock_once_with_visual_bible_identity_line():
    """With a bible, the identity line is also page-scoped, not per panel."""
    bible = VisualBible(
        characters={
            "沈知意": CharacterCanon(
                canonical_name="沈知意", gender="female", narrative_function="protagonist"
            ),
        }
    )
    plan = ComicPagePlan.model_validate(
        {
            "page_id": "p1",
            "purpose": "three beats of the same character",
            "layout_intent": "wide",
            "panels": [
                {"panel_id": "a", "action": "gaze", "characters": ["沈知意"]},
                {"panel_id": "b", "action": "turn", "characters": ["沈知意"]},
                {"panel_id": "c", "action": "smile", "characters": ["沈知意"]},
            ],
            "reference_characters": ["沈知意"],
        }
    )
    text = render_finished_page_prompt(
        plan,
        characters_by_name={
            "沈知意": CharacterAsset(name="沈知意", l1_prompt="young woman, dark hair"),
        },
        settings_by_name={},
        visual_bible=bible,
    )
    assert text.count("character 沈知意:") == 1
    assert text.count("identity: 沈知意") == 1


def test_multi_character_page_forces_visual_distinction_rule():
    """A page with two locked genders must forbid look-alike duplication.

    The image model collapsed 沈知意 (female) and 少年 (male) into two identical
    scarf-wearing girls. A diffuser needs an explicit rule to keep multiple
    characters on one page visually distinct and gender-locked.
    """
    plan = ComicPagePlan.model_validate(
        {
            "page_id": "p1",
            "purpose": "two people lean on the railing",
            "layout_intent": "wide two-shot",
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
    chars = {
        "沈知意": CharacterAsset(name="沈知意", l1_prompt="young woman, scarf"),
        "少年": CharacterAsset(name="少年", l1_prompt="young man with guitar"),
    }
    text = render_finished_page_prompt(plan, characters_by_name=chars, settings_by_name={})
    lowered = text.lower()
    assert "visually distinct" in lowered
    assert "gender" in lowered
    assert "look-alike" in lowered or "lookalike" in lowered


def test_single_character_page_still_forbids_a_second_person():
    """One declared character is exactly where the extra reader appeared.

    The dock page named only 沈知意 and the model drew her twice, once as a
    hoodie-wearing reader. A one-person page needs the cast lock too.
    """
    plan = ComicPagePlan.model_validate(
        {
            "page_id": "p1",
            "purpose": "one woman watches the river",
            "layout_intent": "single shot",
            "panels": [{"panel_id": "1", "action": "gaze", "characters": ["沈知意"]}],
            "reference_characters": ["沈知意"],
        }
    )
    text = render_finished_page_prompt(
        plan,
        characters_by_name={
            "沈知意": CharacterAsset(name="沈知意", l1_prompt="young woman, scarf"),
        },
        settings_by_name={},
    )
    lowered = text.lower()
    assert "only people on this page are 沈知意" in lowered
    assert "second copy" in lowered or "bystander" in lowered


def test_deferred_prompt_drops_the_words_that_summon_chrome():
    """A clause that names a bubble or a forbidden letter draws that object."""
    plan = ComicPagePlan.model_validate(
        {
            "page_id": "p1",
            "purpose": "she answers",
            "layout_intent": "close",
            "covers_beats": ["smile"],
            "panels": [
                {
                    "panel_id": "1",
                    "action": (
                        "She smiles. A single clean speech bubble carries her line, "
                        "top-right of the panel."
                    ),
                    "characters": ["沈知意"],
                    "lettering_notes": "Dialogue bubble in the top-right.",
                }
            ],
            "reference_characters": ["沈知意"],
        }
    )
    text = render_finished_page_prompt(
        plan,
        characters_by_name={
            "沈知意": CharacterAsset(name="沈知意", l1_prompt="young woman"),
        },
        settings_by_name={},
    )
    lowered = text.lower()
    assert "speech bubble" not in lowered
    assert "lettering" not in lowered
    assert "holding a letter" not in lowered
    assert "she smiles" in lowered


def test_a_letter_the_action_stages_is_kept():
    plan = ComicPagePlan.model_validate(
        {
            "page_id": "p1",
            "purpose": "reads letter",
            "layout_intent": "close",
            "panels": [{"panel_id": "1", "action": "R reads a letter at the desk"}],
        }
    )
    text = render_finished_page_prompt(plan, characters_by_name={}, settings_by_name={})
    assert "reads a letter" in text


def test_finished_page_prompt_does_not_ask_for_a_sketch():
    """ "Line art" and "design sheet" became an uncolored panel of a stranger."""
    plan = ComicPagePlan.model_validate(
        {
            "page_id": "p1",
            "purpose": "dock",
            "layout_intent": "wide",
            "panels": [
                {
                    "panel_id": "1",
                    "action": "the boy plays",
                    "characters": ["少年"],
                    "setting_ref": "dock",
                }
            ],
            "reference_characters": ["少年"],
        }
    )
    text = render_finished_page_prompt(
        plan,
        characters_by_name={
            "少年": CharacterAsset(name="少年", l1_prompt="young man, red scarf"),
        },
        settings_by_name={},
        style_guide="clean black ink line art, soft cel shading",
        visual_bible=VisualBible(
            style_guide="manhua, clean black ink line art",
            characters={
                "少年": CharacterCanon(canonical_name="少年", gender="male", face_lock="shy smile")
            },
        ),
    )
    lowered = text.lower()
    assert "line art" not in lowered
    assert "design sheet" not in lowered
    assert "turnaround" not in lowered
    assert "fully colored" in lowered
    assert "exact outfit" in lowered


def test_page_with_no_characters_omits_the_cast_lock():
    plan = ComicPagePlan.model_validate(
        {
            "page_id": "p1",
            "purpose": "the river only",
            "layout_intent": "wide",
            "panels": [{"panel_id": "1", "action": "water", "characters": []}],
        }
    )
    text = render_finished_page_prompt(plan, characters_by_name={}, settings_by_name={})
    assert "only people on this page" not in text.lower()
