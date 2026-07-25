"""tests/test_schemas.py — structured-contract (Pydantic) parsing tests.

Verifies the three JSON contracts:
- StoryElements / CharacterAsset parse the extraction payload (appearance,
  l1_prompt) and reuse-by-name shape.
- Storyboard / Panel parse the planning payload; dialogue is optional.
  ``panel_prompt`` is runtime-only (ignored for generation; hidden from tools).
- ProjectState round-trips through save/load and preserves the resume dedup key.
- to_tool_schema emits a valid function-tool definition and hides runtime-only
  fields (SkipJsonSchema) from the model-facing schema.
- Unknown fields from the model are ignored, not fatal.
"""

import json

from core.schemas import (
    CharacterAliasSuggestion,
    CharacterAsset,
    Panel,
    ProjectState,
    Setting,
    Storyboard,
    StoryElements,
    to_tool_schema,
)


def test_story_elements_coerces_stringified_lists():
    """Agnes sometimes JSON-stringifies nested arrays inside tool arguments."""
    elements = StoryElements.model_validate(
        {
            "characters": '[{"name": "张一新", "role": "protagonist", '
            '"l1_prompt": "conflict and reflection."}]',
            "settings": "[]",
            "style_guide": "manhua style",
        }
    )
    assert len(elements.characters) == 1
    assert elements.characters[0].name == "张一新"
    assert elements.settings == []


def test_storyboard_coerces_stringified_panels_and_char_lists():
    board = Storyboard.model_validate(
        {
            "chapter_id": "c1",
            "panels": (
                '[{"panel_id": "c1_p01", "action": "looks up", '
                '"characters_present": "[\\"张一新\\"]"}]'
            ),
        }
    )
    assert board.panels[0].panel_id == "c1_p01"
    assert board.panels[0].characters_present == ["张一新"]


def test_storyboard_coerces_double_encoded_panels_string():
    """Providers sometimes JSON-encode the array string a second time."""
    inner = '[{"panel_id": "1", "action": "walk", "size": "1024x1024"}]'
    board = Storyboard.model_validate(
        {
            "chapter_id": "c1",
            "panels": json.dumps(inner),
        }
    )
    assert board.panels[0].panel_id == "1"
    assert board.panels[0].size == "1024x1024"


def test_storyboard_coerces_panels_with_unescaped_quotes():
    """LLM tool strings often break JSON with raw quotes inside action/dialogue."""
    broken = '[{"panel_id": "1", "action": "says "hi"", "size": "1024x1024"}]'
    board = Storyboard.model_validate({"chapter_id": "c1", "panels": broken})
    assert board.panels[0].panel_id == "1"
    assert "hi" in board.panels[0].action


def test_storyboard_coerces_python_repr_panels_string():
    """Some providers emit Python-literal lists instead of JSON."""
    broken = "[{'panel_id': '1', 'action': 'walk', 'size': '1024x1024'}]"
    board = Storyboard.model_validate({"chapter_id": "c1", "panels": broken})
    assert board.panels[0].panel_id == "1"


def test_panel_coerces_dialogue_speaker_dict():
    """Models often return dialogue as {speaker: line} instead of a string."""
    board = Storyboard.model_validate(
        {
            "chapter_id": "c1",
            "panels": [
                {
                    "panel_id": "1",
                    "action": "looks worried",
                    "dialogue": {
                        "Passepartout": "What is that? News stops a locomotive!"
                    },
                },
                {
                    "panel_id": "2",
                    "action": "calm",
                    "dialogue": {"Fogg": "Patience is key.", "Passepartout": "Sir!"},
                },
            ],
        }
    )
    assert board.panels[0].dialogue == (
        "Passepartout: What is that? News stops a locomotive!"
    )
    assert board.panels[1].dialogue == "Fogg: Patience is key.\nPassepartout: Sir!"


def test_llm_payload_coerces_common_shape_drifts():
    """Regression: Agnes freely mutates field shapes; validate must not crash."""
    elements = StoryElements.model_validate(
        {
            "style_guide": ["manhua", "ink"],
            "characters": [
                {
                    "name": 7,
                    "role": ["protagonist", "narrator"],
                    "l1_prompt": ["tall man", "black hair"],
                    "appearance": {
                        "hair": ["short", "black"],
                        "distinguishing": {"scar": "left cheek"},
                    },
                },
                '{"name": "Fogg", "role": "lead"}',
            ],
            "settings": {
                "name": "Train",
                "description": {"mood": "tense"},
                "scene_prompt": ["steam", "night"],
            },
        }
    )
    assert elements.style_guide == "manhua, ink"
    assert elements.characters[0].name == "7"
    assert "protagonist" in elements.characters[0].role
    assert "tall man" in elements.characters[0].l1_prompt
    assert "short" in elements.characters[0].appearance.hair
    assert elements.characters[1].name == "Fogg"
    assert elements.settings[0].name == "Train"
    assert "tense" in elements.settings[0].description

    board = Storyboard.model_validate(
        {
            "chapter_id": 3,
            "panels": [
                {
                    "panel_id": 1,
                    "action": {"shot": "wide", "verb": "run"},
                    "setting_ref": {"name": "Train"},
                    "characters_present": "Fogg, Passepartout",
                    "reference_characters": "Fogg",
                    "size": ["1024", "1024"],
                    "dialogue": [{"Fogg": "Patience."}, "Keep calm."],
                },
                '{"panel_id": "2", "action": "waits", "characters_present": ["Fogg"]}',
            ],
        }
    )
    assert board.chapter_id == "3"
    p0 = board.panels[0]
    assert p0.panel_id == "1"
    assert "wide" in p0.action and "run" in p0.action
    assert "Train" in p0.setting_ref
    assert p0.characters_present == ["Fogg", "Passepartout"]
    assert p0.reference_characters == ["Fogg"]
    assert "1024" in p0.size
    assert "x" in p0.size.lower()
    assert "Fogg: Patience." in (p0.dialogue or "")
    assert board.panels[1].panel_id == "2"
    assert board.panels[1].characters_present == ["Fogg"]



STORY_ELEMENTS_PAYLOAD = {
    "characters": [
        {
            "name": "方鸿渐",
            "role": "男主/留学生",
            "appearance": {
                "hair": "黑色短发，三七分",
                "eyewear": "圆框金属细边眼镜",
                "outfit_top": "白色立领衬衫 + 深灰马甲",
            },
            "l1_prompt": "a young Chinese man with round thin metal-framed glasses, "
            "white mandarin-collar shirt, dark grey vest, slim tall build",
            "portrait_prompt": "character design sheet of a young Chinese man, manhua style",
        }
    ],
    "settings": [
        {
            "name": "远洋邮轮甲板",
            "description": "法国邮轮甲板，清晨海风",
            "scene_prompt": "ocean liner deck at dawn, wet reflective floor, manhua",
        }
    ],
    "style_guide": "consistent manhua / comic line-art style, clean ink, soft cel shading",
}

STORYBOARD_PAYLOAD = {
    "chapter_id": "ch01",
    "panels": [
        {
            "panel_id": "ch01_p01",
            "characters_present": ["方鸿渐"],
            "setting_ref": "远洋邮轮甲板",
            "action": "方鸿渐扶着船舷眺望海面",
            "dialogue": None,
            "panel_prompt": "cinematic manhua panel: a young Chinese man with round thin "
            "metal-framed glasses, white mandarin-collar shirt, leaning on railing",
            "reference_characters": ["方鸿渐"],
            "size": "1024x1024",
        },
        {
            "panel_id": "ch01_p02",
            "characters_present": ["苏文纨"],
            "setting_ref": "邮轮休息厅",
            "action": "苏文纨戴墨镜读书",
            "dialogue": "这海上的日子，倒也清静。",
            "panel_prompt": "manhua panel: an elegant woman in sunglasses reading a book",
            "reference_characters": ["苏文纨"],
        },
    ],
}


def test_story_elements_parses_character_and_setting():
    elements = StoryElements.model_validate(STORY_ELEMENTS_PAYLOAD)
    assert len(elements.characters) == 1
    char = elements.characters[0]
    assert char.name == "方鸿渐"
    assert char.appearance.eyewear == "圆框金属细边眼镜"
    assert "metal-framed glasses" in char.l1_prompt
    # Fields not present in the payload fall back to their defaults.
    assert char.appearance.shoes == ""
    assert char.portrait_local is None
    assert elements.settings[0].name == "远洋邮轮甲板"
    assert "manhua" in elements.style_guide


def test_storyboard_parses_panels_and_dialogue():
    sb = Storyboard.model_validate(STORYBOARD_PAYLOAD)
    assert sb.chapter_id == "ch01"
    assert len(sb.panels) == 2
    p1, p2 = sb.panels
    # Legacy panel_prompt may still parse from cached JSON but is not tool-facing.
    assert "metal-framed glasses" in p1.panel_prompt
    assert p1.dialogue is None
    assert p2.dialogue == "这海上的日子，倒也清静。"
    # size defaults when omitted (p2 did not set it).
    assert p2.size == "1024x1024"
    assert p1.reference_characters == ["方鸿渐"]


def test_character_defaults_and_size_default():
    char = CharacterAsset.model_validate({"name": "路人甲"})
    assert char.role == ""
    assert char.appearance.hair == ""
    assert char.l1_prompt == ""
    assert char.aliases == []


def test_project_state_round_trip(tmp_path):
    state = ProjectState(
        project_id="weicheng-ch01",
        source_file="scene1.txt",
        stage="panels",
        characters={"方鸿渐": CharacterAsset(name="方鸿渐", l1_prompt="x", aliases=["鸿渐"])},
        settings={"甲板": Setting(name="甲板", scene_prompt="deck at dawn")},
        chunks_done=["ch01"],
        panels_done=["ch01_p01", "ch01_p02"],
        stale_panels=["ch01_p02"],
        needs_review=[
            CharacterAliasSuggestion(
                new_name="鸿渐",
                candidate="方鸿渐",
                reason="name variant (normalized/substring match)",
                suggested=True,
            )
        ],
    )
    path = tmp_path / "state.json"
    state.save(path)
    assert path.exists()

    loaded = ProjectState.load(path)
    assert loaded.project_id == "weicheng-ch01"
    assert loaded.stage == "panels"
    # Resume dedup key survives the round trip.
    assert loaded.panels_done == ["ch01_p01", "ch01_p02"]
    assert loaded.characters["方鸿渐"].l1_prompt == "x"
    assert loaded.characters["方鸿渐"].aliases == ["鸿渐"]
    assert loaded.settings["甲板"].scene_prompt == "deck at dawn"
    assert loaded.stale_panels == ["ch01_p02"]
    assert loaded.needs_review[0].suggested is True


def test_project_state_save_creates_parent_dirs(tmp_path):
    state = ProjectState(project_id="p")
    nested = tmp_path / "a" / "b" / "state.json"
    state.save(nested)
    assert nested.exists()


def test_to_tool_schema_shape_and_hides_runtime_fields():
    tool = to_tool_schema(StoryElements, "extract_story_elements", "Extract elements.")
    assert tool["type"] == "function"
    assert tool["function"]["name"] == "extract_story_elements"
    params = tool["function"]["parameters"]
    assert params["type"] == "object"
    assert "characters" in params["properties"]

    # Runtime-only CharacterAsset.portrait_local must not leak into the schema.
    char_tool = to_tool_schema(CharacterAsset, "x", "y")
    char_props = char_tool["function"]["parameters"]["properties"]
    assert "portrait_local" not in char_props
    assert "l1_prompt" in char_props

    # panel_prompt is pipeline-owned — must not be requested from the model.
    panel_tool = to_tool_schema(Panel, "panel", "one panel")
    panel_props = panel_tool["function"]["parameters"]["properties"]
    assert "panel_prompt" not in panel_props


def test_active_elapsed_seconds_default_and_roundtrip(tmp_path):
    from core.schemas import ProjectState

    state = ProjectState(project_id="t1")
    assert state.active_elapsed_seconds == 0.0
    path = tmp_path / "state.json"
    state.active_elapsed_seconds = 125.5
    state.save(path)
    loaded = ProjectState.load(path)
    assert loaded.active_elapsed_seconds == 125.5


def test_unknown_fields_are_ignored():
    payload = dict(STORY_ELEMENTS_PAYLOAD)
    payload["unexpected_top_level"] = "whatever"
    payload["characters"][0]["hallucinated_field"] = 123
    elements = StoryElements.model_validate(payload)
    assert elements.characters[0].name == "方鸿渐"
    assert not hasattr(elements, "unexpected_top_level")
