"""The harness tree, its validator, and the quality-verdict tripwire."""

import json

import pytest

from core.harness import spec
from core.harness.tripwire import assert_no_quality_verdicts


@pytest.fixture(autouse=True)
def _clear_harness_cache():
    """A test may point DATA_DIR at a temp tree; never leak that into the next one."""
    spec.clear_harness_cache()
    spec.load_manifest.cache_clear()
    spec.system_prompt.cache_clear()
    spec.harness_digest.cache_clear()
    yield
    spec.clear_harness_cache()
    spec.load_manifest.cache_clear()
    spec.system_prompt.cache_clear()
    spec.harness_digest.cache_clear()


def test_the_shipped_harness_validates():
    assert spec.validate() == []
    assert spec.harness_digest().startswith("sha256:")
    assert "general" in spec.available_genres()
    assert "wuxia" in spec.available_genres()


def test_general_genre_adds_nothing():
    assert spec.genre_block(None) == ""
    assert spec.genre_block("general") == ""


def test_wuxia_genre_is_injected():
    block = spec.genre_block("wuxia")
    assert "武侠题材" in block
    assert "Domain notes for this genre (wuxia)" in block


def test_unknown_genre_is_an_actionable_error():
    with pytest.raises(spec.HarnessError, match="unknown genre"):
        spec.genre_block("steampunk")


def test_the_validator_rejects_a_measurement_marker(tmp_path, monkeypatch):
    bad = tmp_path / "bad.md"
    bad.write_text("Use the acceptance rate to pick a page.", encoding="utf-8")
    monkeypatch.setattr(spec, "DATA_DIR", tmp_path)
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "version": "t",
                "components": [
                    {"id": "system", "path": "bad.md", "kind": "invariant", "max_chars": 8000}
                ],
                "genres": ["general"],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "genres").mkdir()
    (tmp_path / "genres" / "general.md").write_text("general", encoding="utf-8")
    spec.load_manifest.cache_clear()
    spec.system_prompt.cache_clear()
    spec.harness_digest.cache_clear()
    problems = spec.validate()
    assert any("acceptance rate" in problem for problem in problems)


def test_tripwire_rejects_a_quality_marker_in_the_system_prompt():
    with pytest.raises(spec.HarnessError, match="quality marker"):
        assert_no_quality_verdicts(system_text="aim for a high acceptance rate")


def test_tripwire_rejects_a_marker_in_a_tool_schema():
    with pytest.raises(spec.HarnessError, match="quality marker"):
        assert_no_quality_verdicts(
            system_text="draw the page",
            tools=[{"description": "structure_report says this page failed"}],
        )


def test_tripwire_allows_the_shipped_system_prompt():
    assert_no_quality_verdicts(system_text=spec.system_prompt(), tools=[])


def test_every_stage_has_a_template_and_slots():
    for stage in spec.STAGES:
        assert spec.stage_slots(stage), stage


def test_render_stage_substitutes_and_rejects_a_mismatch():
    assert spec.render_stage("extract", {"source_chunk": "第一回"}) == "第一回"
    with pytest.raises(spec.HarnessError, match="missing values"):
        spec.render_stage("extract", {})
    with pytest.raises(spec.HarnessError, match="unused values"):
        spec.render_stage("extract", {"source_chunk": "x", "extra": "y"})
