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
    assert block.lstrip().startswith("Domain notes for this genre (wuxia):")
    assert "Wuxia" in block
    assert spec.cjk_characters(block) == []


def test_no_shipped_component_contains_the_source_language():
    harness = spec.builtin_harness()
    for component in harness.components:
        body = harness.component_text(component.id)
        assert spec.cjk_characters(body) == [], component.id


def test_the_validator_rejects_a_component_written_in_chinese(tmp_path, monkeypatch):
    bad = tmp_path / "bad.md"
    bad.write_text("先画全景，再画近景。", encoding="utf-8")
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
    spec.clear_harness_cache()
    problems = spec.validate()
    assert any("CJK characters" in problem for problem in problems)


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


def test_page_plan_explains_every_layout_token_it_offers():
    """The catalog is a vocabulary; a bare token list teaches the model nothing."""
    body = spec.builtin_harness().component_text("stage.page_plan")
    from core.comic.layout_diversity import LAYOUT_CATALOG

    for token in sorted(LAYOUT_CATALOG):
        assert f"`{token}`" in body, token


def test_page_plan_names_the_panel_fields_that_reach_the_image_prompt():
    """role / shape_hint / shot are concatenated verbatim into the render prompt,
    so the template is the only place their vocabulary is defined."""
    body = spec.builtin_harness().component_text("stage.page_plan")
    for field in ("`role`", "`shape_hint`", "`shot`", "`action`"):
        assert field in body, field
    for role in ("establishing", "action", "reaction", "dialogue", "detail", "transition"):
        assert f"`{role}`" in body, role


def test_page_plan_says_lettering_is_dropped_unless_it_is_a_verbatim_span():
    """retain_source_lettering drops invented text, leaving an empty bubble.
    The planner has to know that before it paraphrases."""
    body = spec.builtin_harness().component_text("stage.page_plan")
    assert "contiguous span" in body
    assert "dropped" in body


def test_page_plan_closes_the_role_and_shape_vocabularies():
    """Both are free-form strings in the schema, so the template is the only
    thing stopping a planner from answering ``climax`` or ``diagonal_splash``.
    A run without the sentence produced exactly those values."""
    body = spec.builtin_harness().component_text("stage.page_plan")
    assert "take only the values listed" in body
