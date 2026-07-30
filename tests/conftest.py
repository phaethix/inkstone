"""tests/conftest.py — shared pytest fixtures."""

import pytest


@pytest.fixture(autouse=True)
def _default_render_mode(monkeypatch):
    """Default tests to the pre-existing storyboard/panel/LayoutEngine path.

    ``core.config.render_mode()`` now defaults to ``finished_page``, but most
    of the existing pipeline test suite (FakeChat/FakeImage fixtures) was
    written against the ``panel_compose`` (storyboard -> panels -> LayoutEngine)
    contract. Opting the whole suite into ``panel_compose`` here avoids
    touching every ``creative_comic(...)`` call site; tests that specifically
    exercise the finished-page path (e.g. ``tests/test_finished_page_pipeline.py``)
    override this within the test body via
    ``monkeypatch.setenv("INKSTONE_RENDER_MODE", "finished_page")``.
    """
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "panel_compose")
