"""tests/test_schemas.py — structured-contract (Pydantic) parsing tests.

Verifies the three JSON contracts:
- StoryElements / CharacterAsset parse the extraction payload (appearance,
  l1_prompt) and reuse-by-name shape.
- Storyboard / Panel parse the planning payload; panel_prompt carries the L1 lever
  and dialogue is optional.
- ProjectState round-trips through save/load and preserves the resume dedup key.
- to_tool_schema emits a valid function-tool definition and hides runtime-only
  fields (SkipJsonSchema) from the model-facing schema.
- Unknown fields from the model are ignored, not fatal.
"""

from core.schemas import (
    CharacterAsset,
    ProjectState,
    Storyboard,
    StoryElements,
    to_tool_schema,
)

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
    # panel_prompt carries the L1 lever (hardened character description).
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


def test_project_state_round_trip(tmp_path):
    state = ProjectState(
        project_id="weicheng-ch01",
        source_file="scene1.txt",
        stage="panels",
        characters={"方鸿渐": CharacterAsset(name="方鸿渐", l1_prompt="x")},
        chunks_done=["ch01"],
        panels_done=["ch01_p01", "ch01_p02"],
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


def test_unknown_fields_are_ignored():
    payload = dict(STORY_ELEMENTS_PAYLOAD)
    payload["unexpected_top_level"] = "whatever"
    payload["characters"][0]["hallucinated_field"] = 123
    elements = StoryElements.model_validate(payload)
    assert elements.characters[0].name == "方鸿渐"
    assert not hasattr(elements, "unexpected_top_level")
