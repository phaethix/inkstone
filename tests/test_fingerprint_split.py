from core.pipelines.creative_comic import (
    _input_fingerprint,
    _legacy_combined_fingerprint,
    _render_fingerprint,
    _structure_fingerprint,
)
from core.schemas import ModelSnapshot, ProjectState

_SNAPSHOT = ModelSnapshot(chat="chat|url|m", t2i="t2i|url|m", i2i="i2i|url|m")


def test_structure_fingerprint_stable_for_same_source():
    source = "chapter one\npanel two"
    assert _structure_fingerprint(source) == _structure_fingerprint(source)


def test_structure_fingerprint_normalizes_line_endings():
    assert _structure_fingerprint("a\r\nb") == _structure_fingerprint("a\nb")


def test_structure_fingerprint_changes_when_source_changes():
    assert _structure_fingerprint("alpha") != _structure_fingerprint("beta")


def test_style_change_same_structure_different_render():
    source = "same source text"
    struct_a = _structure_fingerprint(source)
    struct_b = _structure_fingerprint(source)
    render_a = _render_fingerprint(
        "watercolor",
        snapshot=_SNAPSHOT,
        panel_continuity=True,
        l3_enabled=False,
    )
    render_b = _render_fingerprint(
        "ink sketch",
        snapshot=_SNAPSHOT,
        panel_continuity=True,
        l3_enabled=False,
    )
    assert struct_a == struct_b
    assert render_a != render_b


def test_render_fingerprint_none_style_matches_empty_string():
    with_empty = _render_fingerprint(
        "",
        snapshot=_SNAPSHOT,
        panel_continuity=False,
        l3_enabled=False,
    )
    with_none = _render_fingerprint(
        None,
        snapshot=_SNAPSHOT,
        panel_continuity=False,
        l3_enabled=False,
    )
    assert with_empty == with_none


def test_legacy_combined_fingerprint_aliases_input_fingerprint():
    kwargs = {
        "source_txt": "story",
        "style_guide": "manga",
        "snapshot": _SNAPSHOT,
        "panel_continuity": True,
        "l3_enabled": False,
    }
    assert _input_fingerprint(**kwargs) == _legacy_combined_fingerprint(**kwargs)


def test_project_state_roundtrip_dual_fingerprints(tmp_path):
    state = ProjectState(
        project_id="p1",
        source_fingerprint="legacy",
        structure_fingerprint="struct",
        render_fingerprint="render",
    )
    path = tmp_path / "state.json"
    state.save(path)
    loaded = ProjectState.load(path)
    assert loaded.structure_fingerprint == "struct"
    assert loaded.render_fingerprint == "render"
    assert loaded.source_fingerprint == "legacy"


def test_project_state_missing_dual_fields_loads():
    raw = '{"project_id":"p1","source_fingerprint":"only-old"}'
    loaded = ProjectState.model_validate_json(raw)
    assert loaded.structure_fingerprint == ""
    assert loaded.render_fingerprint == ""
    assert loaded.source_fingerprint == "only-old"
