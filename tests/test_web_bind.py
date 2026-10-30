"""Loopback UI trust boundary: DNS rebinding, LAN token, and artifact reads.

The origin check used to allow any Origin that matched the request Host. A
name that rebinds to loopback therefore looked same-origin and could call
``/api/generate`` or read ``/files``. Non-loopback binds are refused unless
the operator opts in and names a token plus an exact browser origin.
"""

import http.client
import json
from contextlib import contextmanager
from http.server import ThreadingHTTPServer

from web import server


@contextmanager
def _running_server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    import threading

    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd
    finally:
        httpd.shutdown()
        thread.join(timeout=5)


def _exchange(
    port: int,
    method: str,
    path: str,
    *,
    body: bytes = b"{}",
    headers: dict[str, str] | None = None,
) -> tuple[int, bytes, dict[str, str]]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.putrequest(method, path, skip_host=True)
        merged = {"Host": f"127.0.0.1:{port}"}
        merged.update(headers or {})
        if method == "POST" and "Content-Length" not in merged:
            merged["Content-Length"] = str(len(body))
        for key, value in merged.items():
            conn.putheader(key, value)
        conn.endheaders(body if method == "POST" else None)
        resp = conn.getresponse()
        payload = resp.read()
        header_map = {k.lower(): v for k, v in resp.getheaders()}
        return resp.status, payload, header_map
    finally:
        conn.close()


def test_rebinding_origin_that_matches_host_cannot_generate():
    """Host and Origin can agree on an attacker name and still not be us."""
    with _running_server() as httpd:
        port = httpd.server_port
        status, payload, _headers = _exchange(
            port,
            "POST",
            "/api/generate",
            headers={
                "Host": f"evil.example:{port}",
                "Origin": f"http://evil.example:{port}",
                "Sec-Fetch-Site": "same-origin",
                "Content-Type": "application/json",
            },
        )
    assert status == 403
    message = json.loads(payload)["error"].lower()
    assert "origin" in message or "cross" in message


def test_loopback_curl_without_origin_still_reaches_the_api():
    with _running_server() as httpd:
        status, payload, _headers = _exchange(
            httpd.server_port,
            "POST",
            "/api/job/missing-job/stop",
        )
    assert status == 404
    assert (
        "unknown" in json.loads(payload)["error"].lower()
        or "not found" in json.loads(payload)["error"].lower()
    )


def test_files_route_does_not_serve_manuscripts_or_checkpoints(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "OUTPUT_DIR", tmp_path)
    project = tmp_path / "demo"
    (project / "review" / "prompts").mkdir(parents=True)
    (project / "pages").mkdir()
    (project / "source.txt").write_text("secret manuscript", encoding="utf-8")
    (project / "state.json").write_text("{}", encoding="utf-8")
    (project / "cache.json").write_text("{}", encoding="utf-8")
    (project / "consistency.json").write_text("{}", encoding="utf-8")
    (project / "review" / "prompts" / "page.json").write_text("{}", encoding="utf-8")
    (project / "pages" / "page.png").write_bytes(b"\x89PNG")

    with _running_server() as httpd:
        port = httpd.server_port
        blocked = []
        for rel in (
            "demo/source.txt",
            "demo/state.json",
            "demo/cache.json",
            "demo/consistency.json",
            "demo/review/prompts/page.json",
        ):
            status, payload, _headers = _exchange(port, "GET", f"/files/{rel}")
            blocked.append((rel, status, payload))
        png_status, png, png_headers = _exchange(port, "GET", "/files/demo/pages/page.png")

    for rel, status, payload in blocked:
        assert status == 404, rel
        assert b"secret manuscript" not in payload
    assert png_status == 200
    assert png == b"\x89PNG"
    assert png_headers.get("x-content-type-options") == "nosniff"


def test_non_loopback_bind_requires_token_even_if_host_says_loopback(monkeypatch):
    monkeypatch.setattr(server, "HOST", "0.0.0.0")
    monkeypatch.setenv("INKSTONE_UI_TOKEN", "lan-token-value")
    monkeypatch.setenv("INKSTONE_UI_ORIGIN", "http://192.168.1.5:8000")
    with _running_server() as httpd:
        port = httpd.server_port
        denied, _payload, _headers = _exchange(
            port,
            "POST",
            "/api/generate",
            headers={
                "Host": f"127.0.0.1:{port}",
                "Origin": f"http://127.0.0.1:{port}",
            },
        )
        allowed, body, _headers = _exchange(
            port,
            "POST",
            "/api/job/missing-job/stop",
            headers={"X-Inkstone-Token": "lan-token-value"},
        )
    assert denied == 401
    assert allowed == 404
    assert b"lan-token-value" not in body


def test_lan_browser_origin_is_accepted_only_with_the_token(monkeypatch):
    monkeypatch.setattr(server, "HOST", "0.0.0.0")
    monkeypatch.setenv("INKSTONE_UI_TOKEN", "lan-token-value")
    with _running_server() as httpd:
        port = httpd.server_port
        origin = f"http://192.168.1.5:{port}"
        monkeypatch.setenv("INKSTONE_UI_ORIGIN", origin)
        status, body, headers = _exchange(
            port,
            "GET",
            "/",
            headers={"Host": f"192.168.1.5:{port}"},
        )
        authed, authed_body, authed_headers = _exchange(
            port,
            "POST",
            "/api/job/missing-job/stop",
            headers={
                "Host": f"192.168.1.5:{port}",
                "Origin": origin,
                "X-Inkstone-Token": "lan-token-value",
            },
        )
        via_cookie, _cookie_body, _cookie_headers = _exchange(
            port,
            "POST",
            "/api/job/missing-job/stop",
            headers={
                "Host": f"192.168.1.5:{port}",
                "Origin": origin,
                "Cookie": "inkstone_token=lan-token-value",
            },
        )
    assert status == 200
    assert b"lan-token-value" not in body
    assert "lan-token-value" not in headers.get("set-cookie", "")
    assert authed == 404
    assert b"lan-token-value" not in authed_body
    cookie = authed_headers.get("set-cookie", "")
    assert "inkstone_token=lan-token-value" in cookie
    assert "samesite=strict" in cookie.lower()
    assert via_cookie == 404


def _keep_runtime_config(monkeypatch) -> None:
    """main() rebinds these module globals; put the imports back when the test ends."""
    for name in ("HOST", "PORT", "MAX_JSON_BODY_BYTES", "EXPERIMENTS_DIR"):
        monkeypatch.setattr(server, name, getattr(server, name))


def test_main_refuses_non_loopback_without_explicit_lan_opt_in(tmp_path, monkeypatch, capsys):
    _keep_runtime_config(monkeypatch)
    monkeypatch.setenv("INKSTONE_UI_HOST", "0.0.0.0")
    monkeypatch.setattr(server, "_load_dotenv", lambda: None)
    monkeypatch.setattr(server, "OUTPUT_DIR", tmp_path)
    monkeypatch.delenv("INKSTONE_UI_ALLOW_LAN", raising=False)
    monkeypatch.delenv("INKSTONE_UI_TOKEN", raising=False)

    class _Boom:
        def __init__(self, *args, **kwargs):
            raise AssertionError("server must not bind")

    monkeypatch.setattr(server, "ThreadingHTTPServer", _Boom)
    try:
        server.main()
    except SystemExit as exc:
        assert exc.code not in (0, None)
    else:
        raise AssertionError("non-loopback bind should refuse to start")
    err = capsys.readouterr().err
    assert "0.0.0.0" in err
    assert "INKSTONE_UI_ALLOW_LAN" in err


def test_main_warns_when_lan_bind_is_explicit_and_does_not_print_the_token(
    tmp_path, monkeypatch, capsys
):
    _keep_runtime_config(monkeypatch)
    monkeypatch.setenv("INKSTONE_UI_HOST", "0.0.0.0")
    monkeypatch.setenv("INKSTONE_UI_ALLOW_LAN", "1")
    monkeypatch.setenv("INKSTONE_UI_TOKEN", "lan-token-value")
    monkeypatch.setenv("INKSTONE_UI_ORIGIN", "http://192.168.1.5:8000")
    monkeypatch.setattr(server, "_load_dotenv", lambda: None)
    monkeypatch.setattr(server, "OUTPUT_DIR", tmp_path)
    seen: dict = {}

    class _FakeHTTPD:
        def __init__(self, addr, handler):
            seen["addr"] = addr

        def serve_forever(self):
            raise KeyboardInterrupt

    monkeypatch.setattr(server, "ThreadingHTTPServer", _FakeHTTPD)
    server.main()
    err = capsys.readouterr().err
    assert seen["addr"][0] == "0.0.0.0"
    assert "unauthenticated" in err or "network" in err
    assert "lan-token-value" not in err


def test_main_applies_dotenv_before_bind_and_experiments(tmp_path, monkeypatch, capsys):
    _keep_runtime_config(monkeypatch)
    env_file = tmp_path / ".env"
    experiments = tmp_path / "exp-root"
    env_file.write_text(
        "\n".join(
            [
                "INKSTONE_UI_HOST=127.0.0.1",
                "INKSTONE_UI_PORT=8123",
                "INKSTONE_UI_MAX_BODY_BYTES=12345",
                f"INKSTONE_EXPERIMENTS_DIR={experiments}",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(server, "ROOT", tmp_path)
    for name in (
        "INKSTONE_UI_HOST",
        "INKSTONE_UI_PORT",
        "INKSTONE_UI_MAX_BODY_BYTES",
        "INKSTONE_EXPERIMENTS_DIR",
    ):
        monkeypatch.delenv(name, raising=False)
    seen: dict = {}

    class _FakeHTTPD:
        def __init__(self, addr, handler):
            seen["addr"] = addr

        def serve_forever(self):
            raise KeyboardInterrupt

    monkeypatch.setattr(server, "ThreadingHTTPServer", _FakeHTTPD)
    monkeypatch.setattr(server, "OUTPUT_DIR", tmp_path)
    server.main()
    assert seen["addr"] == ("127.0.0.1", 8123)
    assert server.MAX_JSON_BODY_BYTES == 12345
    assert server.EXPERIMENTS_DIR == experiments
    assert "lan-token-value" not in capsys.readouterr().err
