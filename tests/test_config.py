def test_render_mode_defaults_finished_page(monkeypatch):
    monkeypatch.delenv("INKSTONE_RENDER_MODE", raising=False)
    from core.config import render_mode

    assert render_mode() == "finished_page"


def test_render_mode_panel_compose(monkeypatch):
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "panel_compose")
    from core.config import render_mode

    assert render_mode() == "panel_compose"


def test_finished_page_size_default(monkeypatch):
    monkeypatch.delenv("INKSTONE_PAGE_SIZE", raising=False)
    from core.config import finished_page_size

    assert finished_page_size() == "1024x1536"
