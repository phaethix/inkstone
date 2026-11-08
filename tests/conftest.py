"""tests/conftest.py — shared pytest fixtures."""

import pytest


@pytest.fixture(autouse=True)
def _default_render_mode(monkeypatch):
    """Default tests to the legacy storyboard/panel/LayoutEngine path.

    The ``panel_compose`` name now means the page-plan -> per-panel-images ->
    local compose pipeline; the old storyboard -> panels -> LayoutEngine flow
    is selected by ``INKSTONE_RENDER_MODE=storyboard``. Most of the existing
    pipeline test suite (FakeChat/FakeImage fixtures) was written against that
    legacy contract. Opting the whole suite into ``storyboard`` here avoids
    touching every ``creative_comic(...)`` call site; tests that exercise the
    finished-page or page-panel paths override this within the test body via
    ``monkeypatch.setenv("INKSTONE_RENDER_MODE", ...)``.
    """
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "storyboard")
