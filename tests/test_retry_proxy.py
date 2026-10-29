"""tests/test_retry_proxy.py — a stale env proxy must not break every upstream call.

A leftover ``HTTPS_PROXY`` pointing at a dead local proxy made ``requests``
raise ``ProxyError`` on every attempt, burning ~18 minutes of retries in
``comic_out/1f61fb46a59a`` before the run died. The API host is reachable
directly, so upstream calls must use a Session that ignores the environment.

``trust_env`` is a ``requests.Session`` **attribute**, not a request kwarg:
passing it to ``requests.post`` raises ``TypeError``. These tests therefore
exercise the real session object rather than a mocked ``requests.post``.
"""

import asyncio
import http.server
import threading
from unittest.mock import patch

import pytest

from core.api import retry as retry_mod


class _Handler(http.server.BaseHTTPRequestHandler):
    """Answer every request with 200 so only transport behaviour is under test."""

    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        self.do_GET()

    def log_message(self, *args) -> None:
        pass


@pytest.fixture()
def local_server():
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_upstream_session_ignores_environment_proxy(monkeypatch, local_server):
    """A dead env proxy must not fail a reachable direct connection."""
    monkeypatch.delenv("INKSTONE_HTTP_PROXY", raising=False)
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")

    session = retry_mod.upstream_session()
    assert session.trust_env is False
    resp = session.get(local_server, timeout=10)
    assert resp.status_code == 200


def test_upstream_session_honours_explicit_proxy(monkeypatch):
    """``INKSTONE_HTTP_PROXY`` is used when set, and env vars still ignored."""
    monkeypatch.setenv("INKSTONE_HTTP_PROXY", "http://127.0.0.1:3128")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")

    session = retry_mod.upstream_session()
    assert session.trust_env is False
    assert session.proxies == {
        "http": "http://127.0.0.1:3128",
        "https": "http://127.0.0.1:3128",
    }


def test_upstream_session_has_no_proxy_by_default(monkeypatch):
    monkeypatch.delenv("INKSTONE_HTTP_PROXY", raising=False)
    session = retry_mod.upstream_session()
    assert not session.proxies


def test_retryable_post_uses_upstream_session(monkeypatch):
    """The retry loop must go through the hardened session, not bare requests.post."""
    monkeypatch.delenv("INKSTONE_HTTP_PROXY", raising=False)

    captured: dict = {}

    class _Resp:
        status_code = 200
        text = ""

        def raise_for_status(self) -> None:
            return None

    class _Session:
        trust_env = False

        def post(self, url, **kwargs):
            captured["url"] = url
            captured["kwargs"] = kwargs
            return _Resp()

    async def run():
        with patch.object(retry_mod, "upstream_session", return_value=_Session()):
            await retry_mod.retryable_post(
                provider_tag="[Test]",
                url="http://example.test/v1/chat/completions",
                headers={},
                json_payload={},
                max_retries=1,
                retry_base_delay=0.01,
            )

    asyncio.run(run())

    assert captured["url"] == "http://example.test/v1/chat/completions"
    assert "timeout" in captured["kwargs"]


def test_retryable_post_does_not_pass_trust_env_as_request_kwarg(monkeypatch):
    """``trust_env`` on a request call is a TypeError; it must not be forwarded."""
    monkeypatch.delenv("INKSTONE_HTTP_PROXY", raising=False)

    captured: dict = {}

    class _Resp:
        status_code = 200
        text = ""

        def raise_for_status(self) -> None:
            return None

    class _Session:
        trust_env = False

        def post(self, url, **kwargs):
            captured.update(kwargs)
            return _Resp()

    async def run():
        with patch.object(retry_mod, "upstream_session", return_value=_Session()):
            await retry_mod.retryable_post(
                provider_tag="[Test]",
                url="http://example.test/v1/images",
                headers={},
                json_payload={},
                max_retries=1,
                retry_base_delay=0.01,
            )

    asyncio.run(run())

    assert "trust_env" not in captured
    assert "proxies" not in captured
