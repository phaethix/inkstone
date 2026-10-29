"""tests/test_image_download_proxy.py — downloads must not inherit shell proxies.

Review finding: ``retryable_post`` was hardened against a stale ``HTTPS_PROXY``
but ``utils.image.download_image`` still used a bare ``requests.get``. The
download is the same failure mode: a dead proxy raises ``ProxyError`` and the
three tenacity attempts all fail, so generated images never reach disk.

``trust_env`` is a Session attribute, not a request kwarg, so this test drives a
real local HTTP server rather than asserting on a mocked call.
"""

import http.server
import threading

import pytest

from utils import image as image_mod


class _PngHandler(http.server.BaseHTTPRequestHandler):
    """Serve four bytes with a Content-Length so the size cap is exercised."""

    def do_GET(self) -> None:
        payload = b"data"
        self.send_response(200)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args) -> None:
        pass


@pytest.fixture()
def png_server():
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _PngHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}/a.png"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_download_image_ignores_environment_proxy(monkeypatch, png_server, tmp_path):
    """A dead env proxy must not stop a reachable direct download."""
    monkeypatch.delenv("INKSTONE_HTTP_PROXY", raising=False)
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")

    target = tmp_path / "a.png"
    image_mod.download_image(png_server, str(target))

    assert target.read_bytes() == b"data"


def test_download_image_honours_explicit_proxy(monkeypatch):
    """``INKSTONE_HTTP_PROXY`` must reach the session used for downloads."""
    monkeypatch.setenv("INKSTONE_HTTP_PROXY", "http://127.0.0.1:3128")

    seen: dict = {}

    class _Resp:
        headers = {"Content-Length": "4"}

        def raise_for_status(self) -> None:
            return None

        def iter_content(self, chunk_size):
            return iter([b"data"])

    class _Session:
        trust_env = False
        proxies: dict = {}

        def get(self, url, **kwargs):
            seen["url"] = url
            seen["kwargs"] = kwargs
            return _Resp()

    from unittest.mock import patch

    import core.api.retry as retry_mod

    with patch.object(retry_mod, "upstream_session", return_value=_Session()):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            image_mod.download_image("https://example.test/b.png", str(Path(tmp) / "b.png"))

    assert "trust_env" not in seen["kwargs"]
    assert "proxies" not in seen["kwargs"]
