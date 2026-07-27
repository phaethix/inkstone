from core.schemas import ProjectState


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
