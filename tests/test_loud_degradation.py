"""tests/test_loud_degradation.py — degradations must be loud, never silent.

Covers the three "silent by default" spots fixed in the v0.1.1 batch:
manga2pdf fallback, unbounded webtoon canvas, and non-loopback UI binding.
"""

import logging

import pytest
from PIL import Image

from core.comic.export import ExportEngine
from core.comic.layout import (
    DEFAULT_WEBTOON_MAX_PIXELS,
    LayoutEngine,
    PanelImage,
    _webtoon_max_pixels,
)


def test_pdf_export_warns_on_pil_fallback(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr("core.comic.export.shutil.which", lambda _name: None)
    for i in range(2):
        Image.new("RGB", (100, 80), (255, 255, 255)).save(tmp_path / f"page_{i:02d}.png")
    with caplog.at_level(logging.WARNING, logger="core.comic.export"):
        out = ExportEngine().export_pdf(tmp_path, out=str(tmp_path / "comic.pdf"))
    assert out.endswith("comic.pdf")
    assert any(
        "manga2pdf" in r.message and "plain multi-page PDF" in r.message
        for r in caplog.records
    )


# --------------------------------------------------------------------- #
# Webtoon canvas guard
# --------------------------------------------------------------------- #
def test_webtoon_guard_defaults_to_200mp(monkeypatch):
    monkeypatch.delenv("INKSTONE_WEBTOON_MAX_PIXELS", raising=False)
    assert _webtoon_max_pixels() == DEFAULT_WEBTOON_MAX_PIXELS


def test_webtoon_guard_env_override_and_disable(monkeypatch):
    monkeypatch.setenv("INKSTONE_WEBTOON_MAX_PIXELS", "1000")
    assert _webtoon_max_pixels() == 1000
    monkeypatch.setenv("INKSTONE_WEBTOON_MAX_PIXELS", "0")
    assert _webtoon_max_pixels() == 0
    monkeypatch.setenv("INKSTONE_WEBTOON_MAX_PIXELS", "garbage")
    assert _webtoon_max_pixels() == DEFAULT_WEBTOON_MAX_PIXELS


def test_webtoon_over_limit_fails_with_actionable_message(tmp_path, monkeypatch):
    monkeypatch.setenv("INKSTONE_WEBTOON_MAX_PIXELS", "1000")
    eng = LayoutEngine(page_width=400)
    panels = [PanelImage(Image.new("RGB", (400, 300), (200, 200, 200))) for _ in range(3)]
    with pytest.raises(ValueError, match="format page"):
        eng.compose(panels, tmp_path, layout_mode="webtoon")


def test_webtoon_under_limit_still_composes(tmp_path, monkeypatch):
    monkeypatch.setenv("INKSTONE_WEBTOON_MAX_PIXELS", "0")
    eng = LayoutEngine(page_width=400)
    panels = [PanelImage(Image.new("RGB", (400, 100), (200, 200, 200))) for _ in range(2)]
    paths = eng.compose(panels, tmp_path, layout_mode="webtoon")
    assert len(paths) == 1


# --------------------------------------------------------------------- #
# Non-loopback bind warning
# --------------------------------------------------------------------- #
def test_server_warns_on_non_loopback_bind(tmp_path, monkeypatch, capsys):
    import web.server as server

    class _FakeHTTPD:
        def __init__(self, *args, **kwargs):
            pass

        def serve_forever(self):
            raise KeyboardInterrupt

    monkeypatch.setattr(server, "HOST", "0.0.0.0")
    monkeypatch.setattr(server, "ThreadingHTTPServer", _FakeHTTPD)
    monkeypatch.setattr(server, "_load_dotenv", lambda: None)
    monkeypatch.setattr(server, "OUTPUT_DIR", tmp_path)
    server.main()
    err = capsys.readouterr().err
    assert "unauthenticated" in err
    assert "0.0.0.0" in err


def test_server_quiet_on_loopback(tmp_path, monkeypatch, capsys):
    import web.server as server

    class _FakeHTTPD:
        def __init__(self, *args, **kwargs):
            pass

        def serve_forever(self):
            raise KeyboardInterrupt

    monkeypatch.setattr(server, "HOST", "127.0.0.1")
    monkeypatch.setattr(server, "ThreadingHTTPServer", _FakeHTTPD)
    monkeypatch.setattr(server, "_load_dotenv", lambda: None)
    monkeypatch.setattr(server, "OUTPUT_DIR", tmp_path)
    server.main()
    assert "unauthenticated" not in capsys.readouterr().err
