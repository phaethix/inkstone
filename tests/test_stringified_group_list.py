"""tests/test_stringified_group_list.py — a grouped list must not vanish.

The real failure: the chat model returned ``characters`` as a *string* holding
``[[character, ...], [setting, ...]]`` — the two roles fused into one list and
encoded as text. ``coerce_jsonish`` decoded it to a two-item list of *lists*,
and ``coerce_object_list`` kept only ``dict`` entries, so both innermost groups
were dropped and extraction came back empty. The pipeline then rendered a page
with no character reference at all and called the run a success.
"""

import logging

from core.schemas import StoryElements, coerce_object_list


def _character(name: str) -> dict:
    return {"name": name, "role": "protagonist", "l1_prompt": "clean line art"}


def _setting(name: str) -> dict:
    return {"name": name, "description": "a station at dawn", "scene_prompt": "quiet platform"}


def test_grouped_string_payload_keeps_every_object():
    """The exact shape the API sent: two inner lists inside one encoded array."""
    import json

    payload = json.dumps(
        [
            [_character("r"), _character("仆人")],
            [_setting("火车站")],
        ],
        ensure_ascii=False,
    )

    objects = coerce_object_list(payload)

    names = [item["name"] for item in objects]
    assert names == ["r", "仆人", "火车站"], "inner groups must be flattened, not dropped"


def test_story_elements_recovers_characters_from_grouped_payload():
    import json

    payload = json.dumps(
        [[_character("r"), _character("仆人")], [_setting("火车站")]],
        ensure_ascii=False,
    )

    elements = StoryElements.model_validate({"characters": payload})

    assert [c.name for c in elements.characters] == ["r", "仆人"]
    assert [s.name for s in elements.settings] == ["火车站"]


def test_setting_fused_into_characters_does_not_become_a_portrait(caplog):
    """A station is not a character: it must not be asked to sit for a portrait."""
    import json

    payload = json.dumps(
        [[_setting("火车站"), _setting("马车")], [_character("r")]],
        ensure_ascii=False,
    )

    with caplog.at_level(logging.WARNING, logger="core.schemas"):
        elements = StoryElements.model_validate({"characters": payload})

    assert [c.name for c in elements.characters] == ["r"]
    assert sorted(s.name for s in elements.settings) == ["火车站", "马车"]
    assert any("moving them to settings" in r.message for r in caplog.records)


def test_nested_groups_survive_arbitrary_depth():
    payload = [[[_character("r")]], [_setting("车站")]]
    names = [item["name"] for item in coerce_object_list(payload)]
    assert names == ["r", "车站"]


def test_flat_lists_keep_their_order():
    payload = [_character("a"), _character("b"), _character("c")]
    assert [item["name"] for item in coerce_object_list(payload)] == ["a", "b", "c"]


def test_dropping_a_non_object_is_still_reported(caplog):
    """A group we cannot read is a warning, never a silent empty result."""
    with caplog.at_level(logging.WARNING, logger="core.schemas"):
        objects = coerce_object_list([_character("r"), 42, "noise"])

    assert [item["name"] for item in objects] == ["r"]
    assert any("dropping" in r.message.lower() for r in caplog.records)
