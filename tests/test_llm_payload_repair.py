"""tests/test_llm_payload_repair.py — fused-key repair + soft-drop list validation."""

import logging

from core.schemas import (
    CharacterAsset,
    Panel,
    Setting,
    Storyboard,
    StoryElements,
    coerce_model_list,
    ensure_str_field,
    repair_fused_keys,
    unwrap_quoted_fragment,
)


def test_unwrap_quoted_fragment_mixed_quotes():
    assert unwrap_quoted_fragment('"四·二八”运动现场') == "四·二八"
    assert unwrap_quoted_fragment("Courtyard, dusk") == "Courtyard"


def test_repair_fused_keys_name_and_stash_description():
    repaired = repair_fused_keys(
        {'name："四·二八”运动现场': "red flags fluttering violently."},
        {"name", "description"},
        stash_value_into="description",
    )
    assert repaired["name"] == "四·二八"
    assert "fluttering" in repaired["description"]
    assert 'name："四·二八”运动现场' not in repaired


def test_ensure_str_field_uses_aliases_then_default():
    assert (
        ensure_str_field({}, "name", aliases=("location",), default="unnamed")["name"] == "unnamed"
    )
    assert (
        ensure_str_field({"location": "花果山"}, "name", aliases=("location",))["name"] == "花果山"
    )


def test_story_elements_soft_drops_unrecoverable_character(caplog):
    with caplog.at_level(logging.WARNING):
        elements = StoryElements.model_validate(
            {
                "characters": [
                    {"name": "方鸿渐", "role": "男主"},
                    {"name": "坏项", "appearance": 123},  # Appearance cannot parse int
                    {'name："章先生”': "scholar"},
                ],
                "settings": [
                    {"name": "甲板"},
                    {'name："四·二八”现场': "flags fluttering"},
                ],
            }
        )
    names = [c.name for c in elements.characters]
    assert "方鸿渐" in names
    assert "章先生" in names
    assert "坏项" not in names
    assert any("dropping invalid CharacterAsset" in r.message for r in caplog.records)
    setting_names = [s.name for s in elements.settings]
    assert "甲板" in setting_names
    assert "四·二八" in setting_names


def test_storyboard_repairs_fused_panel_id_and_soft_drops_junk(caplog):
    with caplog.at_level(logging.WARNING):
        board = Storyboard.model_validate(
            {
                "chapter_id": "1",
                "panels": [
                    {"panel_id": "p0", "action": "walks"},
                    {"panel_id：p1": "looks up at the sky"},
                    {"not_a_panel": True, "appearance": 1},  # may still become Panel with id panel
                ],
            }
        )
    ids = [p.panel_id for p in board.panels]
    assert "p0" in ids
    assert "p1" in ids


def test_coerce_model_list_direct():
    items = coerce_model_list(
        [{"name": "ok"}, {"name": "bad", "appearance": 1}],
        CharacterAsset,
    )
    assert len(items) == 1
    assert items[0].name == "ok"


def test_panel_and_setting_helpers_via_models():
    assert Setting.model_validate({"location": "花果山"}).name == "花果山"
    assert Panel.model_validate({"panel_id：scene_01": "runs"}).panel_id == "scene_01"
    assert Panel.model_validate({"action": "runs"}).panel_id == "panel"
