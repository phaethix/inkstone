"""Provider image URLs must not become a request into the local network."""

import socket

import pytest

from utils import image as image_mod

download_image = image_mod.download_image
UnsafeImageUrl = image_mod.UnsafeImageUrl


class _Resp:
    def __init__(self, status: int, body: bytes = b"ok", location: str | None = None):
        self.status_code = status
        self.headers = {"Content-Length": str(len(body))}
        if location:
            self.headers["Location"] = location
        self._body = body
        self.closed = False

    @property
    def is_redirect(self) -> bool:
        return self.status_code in {301, 302, 303, 307, 308}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size: int = 8192):
        yield self._body

    def close(self) -> None:
        self.closed = True


class _Session:
    def __init__(self, routes: dict[str, _Resp], fetched: list[str]):
        self._routes = routes
        self._fetched = fetched

    def get(self, url, **kwargs):
        assert kwargs.get("allow_redirects") is False
        self._fetched.append(url)
        if url not in self._routes:
            raise AssertionError(f"unexpected fetch {url}")
        return self._routes[url]


def _public_dns(monkeypatch, mapping: dict[str, str]):
    def fake(host, port, *args, **kwargs):
        ip = mapping.get(host)
        if ip is None:
            raise socket.gaierror(f"no address for {host}")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port or 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake)


def test_download_refuses_loopback_before_connecting(tmp_path):
    fetched: list[str] = []

    class _Guard:
        def get(self, url, **kwargs):
            fetched.append(url)
            raise AssertionError("loopback URL must not be requested")

    from core.api import retry as retry_mod

    monkey_session = _Guard()
    # Patch the session factory the downloader imports lazily.
    original = retry_mod.upstream_session
    retry_mod.upstream_session = lambda: monkey_session  # type: ignore[method-assign]
    try:
        with pytest.raises(UnsafeImageUrl):
            download_image("http://127.0.0.1:9/latest/meta-data/", str(tmp_path / "a.png"))
    finally:
        retry_mod.upstream_session = original
    assert fetched == []


def test_download_refuses_metadata_ip_and_unsafe_schemes(monkeypatch, tmp_path):
    fetched: list[str] = []

    class _Guard:
        def get(self, url, **kwargs):
            fetched.append(url)
            return _Resp(200, b"secret-body")

    monkeypatch.setattr("core.api.retry.upstream_session", lambda: _Guard())
    for url in (
        "http://169.254.169.254/latest/meta-data/",
        "https://169.254.169.254/latest/meta-data/",
        "https://100.100.100.200/latest/meta-data/",
        "http://2130706433/latest/meta-data/",
        "https://metadata.google.internal/computeMetadata/v1/",
        "file:///etc/passwd",
        "gopher://169.254.169.254/",
        "https://user:secret@cdn.example/a.png",
    ):
        with pytest.raises(UnsafeImageUrl):
            download_image(url, str(tmp_path / "a.png"))
    assert fetched == []


def test_download_does_not_follow_redirect_to_metadata(monkeypatch, tmp_path):
    _public_dns(monkeypatch, {"cdn.example": "1.1.1.1"})
    fetched: list[str] = []
    session = _Session(
        {
            "https://cdn.example/a.png": _Resp(
                302, location="http://169.254.169.254/latest/meta-data/"
            )
        },
        fetched,
    )
    monkeypatch.setattr("core.api.retry.upstream_session", lambda: session)
    with pytest.raises(UnsafeImageUrl):
        download_image("https://cdn.example/a.png", str(tmp_path / "a.png"))
    assert fetched == ["https://cdn.example/a.png"]


def test_download_allowlist_rejects_other_public_hosts(monkeypatch, tmp_path):
    _public_dns(monkeypatch, {"cdn.example": "1.1.1.1", "other.example": "1.1.1.1"})
    monkeypatch.setenv("INKSTONE_IMAGE_DOWNLOAD_HOSTS", "cdn.example")
    fetched: list[str] = []
    session = _Session({"https://other.example/a.png": _Resp(200, b"png")}, fetched)
    monkeypatch.setattr("core.api.retry.upstream_session", lambda: session)
    with pytest.raises(UnsafeImageUrl):
        download_image("https://other.example/a.png", str(tmp_path / "a.png"))
    assert fetched == []


def test_download_blocks_public_name_that_resolves_to_a_private_ip(monkeypatch, tmp_path):
    _public_dns(monkeypatch, {"cdn.example": "10.1.2.3"})
    fetched: list[str] = []

    class _Guard:
        def get(self, url, **kwargs):
            fetched.append(url)
            raise AssertionError("private resolution must not be requested")

    monkeypatch.setattr("core.api.retry.upstream_session", lambda: _Guard())
    with pytest.raises(UnsafeImageUrl):
        download_image("https://cdn.example/a.png", str(tmp_path / "a.png"))
    assert fetched == []
