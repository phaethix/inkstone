"""tests/test_render_mode_config.py — render-mode selection and budget stages."""

import pytest

from core.config import panel_size, render_mode
from core.pipelines.creative_comic import _required_budget_stages


def test_render_mode_defaults_to_panel_compose(monkeypatch):
    monkeypatch.delenv("INKSTONE_RENDER_MODE", raising=False)
    assert render_mode() == "panel_compose"


def test_render_mode_recognizes_storyboard(monkeypatch):
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "storyboard")
    assert render_mode() == "storyboard"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("panel_compose", "panel_compose"),
        ("panel", "panel_compose"),
        ("compose", "panel_compose"),
        ("finished_page", "finished_page"),
        ("legacy_storyboard", "storyboard"),
        ("bogus", "panel_compose"),
    ],
)
def test_render_mode_aliases(monkeypatch, raw, expected):
    monkeypatch.setenv("INKSTONE_RENDER_MODE", raw)
    assert render_mode() == expected


def test_panel_size_default_and_override(monkeypatch):
    monkeypatch.delenv("INKSTONE_PANEL_SIZE", raising=False)
    assert panel_size() == "1024x1024"
    monkeypatch.setenv("INKSTONE_PANEL_SIZE", "1536x1024")
    assert panel_size() == "1536x1024"


def test_budget_stages_storyboard_needs_storyboard_not_page_plan():
    stages = _required_budget_stages("storyboard", False)
    assert "storyboard" in stages
    assert "page_plan" not in stages


def test_budget_stages_panel_compose_matches_page_planning():
    stages = _required_budget_stages("panel_compose", False)
    assert {"beats", "page_plan", "render"} <= stages
    assert "storyboard" not in stages
