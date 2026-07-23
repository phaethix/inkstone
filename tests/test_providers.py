"""tests/test_providers.py — ImageProvider abstraction & reliability tests (no network).

Verifies:
- AgnesImageAPI and OpenAICompatProvider both implement the ImageProvider contract.
- get_image_provider raises a clear error (not a silent failure) when the key is missing.
- Retry/backoff behavior: 503s are retried with the exponential+jitter backoff, and
  the backoff value is actually applied.
- Both providers now collect errors on retryable failures.
- i2i reference images are resolved to base64 data URIs inside ``extra_body.image``.
- ImageOutput.b64 save decodes the data URI to bytes.
- collect_error appends a single JSON line to the JSONL error log.
"""

import asyncio
import base64
import json

import pytest
import requests

from core.api.agnes_image import AgnesImageAPI
from core.api.image_provider import (
    ImageOutput,
    ImageProvider,
    OpenAICompatProvider,
    get_image_provider,
)
from core.api.retry import RETRYABLE_STATUS, compute_backoff


def _fake_response_class(status_codes):
    """Build a fake ``requests.post`` that returns the given status codes in order."""
    calls = {"n": 0}

    class FakeResp:
        def __init__(self, code):
            self.status_code = code
            self.text = ""

        def raise_for_status(self):
            if self.status_code >= 400:
                raise requests.HTTPError(response=self)

        def json(self):
            return {"data": [{"url": "http://x/y.png"}]}

    def fake_post(*_a, **_k):
        code = status_codes[min(calls["n"], len(status_codes) - 1)]
        calls["n"] += 1
        return FakeResp(code)

    fake_post.calls = calls
    return fake_post


# --- contract tests (unchanged shape, retained) ---


def test_agnes_image_uses_image_bucket(monkeypatch):
    captured = {}

    class _L:
        def acquire(self):
            return None

    def fake_get_rate_limiter(size=None, bucket="image"):
        captured["bucket"] = bucket
        return _L()

    monkeypatch.setattr("core.api.rate_limiter.get_rate_limiter", fake_get_rate_limiter)

    class FakeResp:
        def __init__(self, code=200):
            self.status_code = code
            self.text = ""

        def raise_for_status(self):
            if self.status_code >= 400:
                raise requests.HTTPError(response=self)

        def json(self):
            return {"data": [{"url": "http://x/y.png"}]}

    monkeypatch.setattr(requests, "post", lambda *a, **k: FakeResp())
    api = AgnesImageAPI(api_key="k")
    asyncio.run(api.generate_single_image("p", max_retries=1))
    # Image calls keep the "image" bucket, distinct from chat's "chat" bucket.
    assert captured.get("bucket") == "image"


def test_agnes_is_image_provider():
    api = AgnesImageAPI(api_key="test-key")
    assert isinstance(api, ImageProvider)
    assert hasattr(api, "generate_single_image")


def test_openai_compat_is_image_provider():
    api = OpenAICompatProvider(api_key="x", base_url="https://example.com/v1", model="m")
    assert isinstance(api, ImageProvider)
    assert api.model == "m"
    assert api.i2i_model == "m"  # i2i defaults to the same model


def test_openai_compat_i2i_model_override():
    api = OpenAICompatProvider(
        api_key="x",
        base_url="https://example.com/v1",
        model="t2i-m",
        i2i_model="i2i-m",
    )
    assert api.model == "t2i-m"
    assert api.i2i_model == "i2i-m"


def test_factory_requires_agnes_key(monkeypatch):
    monkeypatch.delenv("AGNES_API_KEY", raising=False)
    monkeypatch.delenv("PROVIDER", raising=False)
    with pytest.raises(RuntimeError):
        get_image_provider(provider="agnes")


def test_factory_unknown_provider():
    with pytest.raises(ValueError):
        get_image_provider(provider="bogus")


def test_openai_compat_factory_does_not_send_agnes_key(monkeypatch):
    monkeypatch.setenv("AGNES_API_KEY", "agnes-secret")
    monkeypatch.setenv("PROVIDER", "openai_compat")
    monkeypatch.setenv("OPENAI_COMPAT_BASE_URL", "https://images.example/v1")
    monkeypatch.setenv("OPENAI_COMPAT_API_KEY", "compat-secret")

    provider = get_image_provider()

    assert isinstance(provider, OpenAICompatProvider)
    assert provider.api_key == "compat-secret"


def test_image_output_save_url(tmp_path, monkeypatch):
    # Do not really download: stub download_image to verify .save() routing
    import core.api.image_provider as ip

    captured = {}

    def fake_download(url, path):
        captured["url"] = url
        with open(path, "wb") as f:
            f.write(b"fake")

    monkeypatch.setattr(ip, "download_image", fake_download)
    out = ImageOutput(fmt="url", ext="png", data="http://x/y.png")
    p = tmp_path / "a.png"
    out.save(str(p))
    assert captured["url"] == "http://x/y.png"
    assert p.exists()


# --- retry / backoff behavior ---


def test_compute_backoff_exponential_capped_with_jitter(monkeypatch):
    # Pin the jitter factor so the exponential-growth assertion is deterministic:
    # the production code still multiplies by random.uniform, we only fix the value.
    monkeypatch.setattr("core.api.retry.random.uniform", lambda _a, _b: 0.75)
    vals = [compute_backoff(i, 20.0) for i in range(10)]
    # Every value is positive and clamped to the 120s cap.
    assert all(0 < v <= 120.0 for v in vals)
    # Early attempts are strictly smaller than later ones (exponential growth
    # dominates before the cap is hit).
    assert vals[0] < vals[3]
    assert RETRYABLE_STATUS == (429, 500, 502, 503, 504)


def test_agnes_retries_on_503(patch_async, monkeypatch):
    fake_post = _fake_response_class([503, 503, 200])
    monkeypatch.setattr(requests, "post", fake_post)
    api = AgnesImageAPI(api_key="k")
    out = asyncio.run(api.generate_single_image("p", max_retries=3))
    assert fake_post.calls["n"] == 3  # retried twice, then succeeded
    assert out.fmt == "url"


def test_openai_compat_retries_on_503(patch_async, monkeypatch):
    fake_post = _fake_response_class([503, 503, 200])
    monkeypatch.setattr(requests, "post", fake_post)
    api = OpenAICompatProvider(api_key="x", base_url="https://example.com/v1", model="m")
    out = asyncio.run(api.generate_single_image("p", max_retries=3))
    assert fake_post.calls["n"] == 3
    assert out.fmt == "url"


def test_backoff_delay_is_applied(patch_async, monkeypatch):
    slept = []

    async def _capture(d):
        slept.append(d)

    monkeypatch.setattr(asyncio, "sleep", _capture)
    # Pin backoff to a deterministic value so we can assert it is actually used.
    monkeypatch.setattr("core.api.retry.compute_backoff", lambda attempt, base, cap=120.0: 7.0)
    fake_post = _fake_response_class([503, 503, 200])
    monkeypatch.setattr(requests, "post", fake_post)
    api = AgnesImageAPI(api_key="k")
    asyncio.run(api.generate_single_image("p", max_retries=3))
    assert slept == [7.0, 7.0]  # two retries before success


def test_agnes_gives_up_after_max_retries(patch_async, monkeypatch):
    fake_post = _fake_response_class([503, 503, 503, 503])
    monkeypatch.setattr(requests, "post", fake_post)
    api = AgnesImageAPI(api_key="k")
    with pytest.raises(RuntimeError):
        asyncio.run(api.generate_single_image("p", max_retries=3))
    # Initial attempt + 2 retries = 3 total posts before giving up.
    assert fake_post.calls["n"] == 3


def test_agnes_default_retries_are_patient():
    # The free-tier image service is frequently 503 "Service busy"; the default
    # must ride through it rather than giving up after a couple of tries.
    api = AgnesImageAPI(api_key="k")
    assert api.max_retries == 5
    assert api.retry_base_delay == 5.0


def test_agnes_retries_through_many_busy_503s(patch_async, monkeypatch):
    # Four consecutive "Service busy" replies, then success — must not give up.
    fake_post = _fake_response_class([503, 503, 503, 503, 200])
    monkeypatch.setattr(requests, "post", fake_post)
    api = AgnesImageAPI(api_key="k")
    out = asyncio.run(api.generate_single_image("p"))
    assert out.fmt == "url"
    # 4 busy attempts + 1 success => 5 posts, all within the retry budget.
    assert fake_post.calls["n"] == 5


def test_openai_compat_retries_through_many_busy_503s(patch_async, monkeypatch):
    fake_post = _fake_response_class([503, 503, 503, 503, 200])
    monkeypatch.setattr(requests, "post", fake_post)
    api = OpenAICompatProvider(api_key="x", base_url="https://example.com/v1", model="m")
    out = asyncio.run(api.generate_single_image("p"))
    assert out.fmt == "url"
    assert fake_post.calls["n"] == 5


# --- both providers collect errors on retryable failures ---


def test_openai_compat_collects_errors(patch_async, monkeypatch):
    collected = {"n": 0}

    def fake_collect(*_a, **_k):
        collected["n"] += 1
        return None

    monkeypatch.setattr("core.api.error_collector.collect_error", fake_collect)
    fake_post = _fake_response_class([503, 503, 200])
    monkeypatch.setattr(requests, "post", fake_post)
    api = OpenAICompatProvider(api_key="x", base_url="https://example.com/v1", model="m")
    asyncio.run(api.generate_single_image("p", max_retries=3))
    # Two retryable 503s => two error records (previously OpenAICompat collected none).
    assert collected["n"] == 2


# --- i2i reference resolution into extra_body.image ---


def test_openai_compat_i2i_sends_resolved_image(patch_async, monkeypatch, tmp_path):
    ref = tmp_path / "ref.png"
    ref.write_bytes(b"\x89PNG\r\n")
    captured = {}

    class FakeResp:
        def __init__(self, code=200):
            self.status_code = code
            self.text = ""

        def raise_for_status(self):
            if self.status_code >= 400:
                raise requests.HTTPError(response=self)

        def json(self):
            return {"data": [{"url": "http://x/y.png"}]}

    def fake_post(*_a, **k):
        captured["json"] = k.get("json")
        return FakeResp()

    monkeypatch.setattr(requests, "post", fake_post)
    api = OpenAICompatProvider(api_key="x", base_url="https://example.com/v1", model="m")
    asyncio.run(api.generate_single_image("p", reference_image_paths=[str(ref)], max_retries=1))
    assert "extra_body" in captured["json"]
    assert captured["json"]["extra_body"]["response_format"] == "url"
    assert captured["json"]["extra_body"]["image"][0].startswith("data:image/png;base64,")


# --- ImageOutput.b64 save ---


def test_image_output_save_b64(tmp_path):
    raw = b"\x89PNG\r\nfake"
    b64 = base64.b64encode(raw).decode()
    out = ImageOutput(fmt="b64", ext="png", data=f"data:image/png;base64,{b64}")
    p = tmp_path / "b.png"
    out.save(str(p))
    assert p.read_bytes() == raw


# --- error collector appends a JSONL line ---


def test_collect_error_appends_jsonl(tmp_path, monkeypatch):
    log = tmp_path / "errors.jsonl"
    monkeypatch.setenv("INKSTONE_ERROR_LOG", str(log))
    from core.api.error_collector import collect_error

    path = collect_error("image", "m", prompt="hi", error_type="X", status_code=503)
    assert path == str(log)
    lines = log.read_text().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["error_type"] == "X"
    assert rec["status_code"] == 503
    assert rec["prompt"] == "hi"
