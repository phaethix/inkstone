"""tests/test_chat_provider.py — ChatProvider abstraction & reliability tests (no network).

Verifies:
- AgnesChatAPI and OpenAICompatChatProvider both implement the ChatProvider contract.
- get_chat_provider raises a clear error when the key is missing / provider unknown.
- Forced function calling returns the parsed tool arguments dict.
- A dict-form arguments payload is returned as-is (no double-decode).
- A response with no tool_calls raises a clear error.
- Retry/backoff: 503s are retried with the shared exponential+jitter backoff.
- Chat errors are collected on retryable failures (tagged "chat" in the log).
"""

import asyncio
import json

import pytest
import requests

from core.api.chat_provider import (
    AgnesChatAPI,
    ChatProvider,
    OpenAICompatChatProvider,
    get_chat_provider,
)


def _tool_call_body(args, call_n=0):
    """Build a chat-completion body whose first choice emits one tool call."""
    return {
        "choices": [
            {"message": {"tool_calls": [{"function": {"name": "extract", "arguments": args}}]}}
        ]
    }


def _fake_chat_post(status_codes, body):
    """Build a fake ``requests.post`` returning the given status codes then body."""
    calls = {"n": 0}

    class FakeResp:
        def __init__(self, code):
            self.status_code = code
            self.text = ""

        def raise_for_status(self):
            if self.status_code >= 400:
                raise requests.HTTPError(response=self)

        def json(self):
            return body

    def fake_post(*_a, **_k):
        code = status_codes[min(calls["n"], len(status_codes) - 1)]
        calls["n"] += 1
        return FakeResp(code)

    fake_post.calls = calls
    return fake_post


# --- contract tests ---


def test_agnes_is_chat_provider():
    api = AgnesChatAPI(api_key="test-key")
    assert isinstance(api, ChatProvider)
    assert hasattr(api, "chat_function_call")


def test_openai_compat_chat_is_chat_provider():
    api = OpenAICompatChatProvider(api_key="x", base_url="https://example.com/v1", model="m")
    assert isinstance(api, ChatProvider)
    assert api.model == "m"


def test_factory_requires_agnes_key(monkeypatch):
    monkeypatch.delenv("AGNES_API_KEY", raising=False)
    monkeypatch.delenv("PROVIDER", raising=False)
    with pytest.raises(RuntimeError):
        get_chat_provider(provider="agnes")


def test_factory_unknown_provider():
    with pytest.raises(ValueError):
        get_chat_provider(provider="bogus")


def test_openai_compat_chat_factory_does_not_send_agnes_key(monkeypatch):
    monkeypatch.setenv("AGNES_API_KEY", "agnes-secret")
    monkeypatch.setenv("PROVIDER", "openai_compat")
    monkeypatch.setenv("OPENAI_COMPAT_CHAT_BASE_URL", "https://chat.example/v1")
    monkeypatch.setenv("OPENAI_COMPAT_CHAT_API_KEY", "compat-secret")

    provider = get_chat_provider()

    assert isinstance(provider, OpenAICompatChatProvider)
    assert provider.api_key == "compat-secret"


# --- forced function calling ---


def test_agnes_chat_returns_parsed_args(patch_async, monkeypatch):
    body = _tool_call_body(json.dumps({"characters": [{"name": "方鸿渐"}]}))
    fake_post = _fake_chat_post([200], body)
    monkeypatch.setattr(requests, "post", fake_post)
    api = AgnesChatAPI(api_key="k")
    out = asyncio.run(
        api.chat_function_call(
            messages=[{"role": "user", "content": "x"}],
            tools=[{"type": "function", "function": {"name": "extract"}}],
            tool_choice={"type": "function", "function": {"name": "extract"}},
        )
    )
    assert out == {"characters": [{"name": "方鸿渐"}]}
    assert fake_post.calls["n"] == 1


def test_agnes_chat_returns_dict_args_as_is(patch_async, monkeypatch):
    body = _tool_call_body({"characters": [{"name": "方鸿渐"}]})  # already a dict
    fake_post = _fake_chat_post([200], body)
    monkeypatch.setattr(requests, "post", fake_post)
    api = AgnesChatAPI(api_key="k")
    out = asyncio.run(
        api.chat_function_call(
            messages=[{"role": "user", "content": "x"}],
            tools=[],
            tool_choice={"type": "function", "function": {"name": "extract"}},
        )
    )
    assert out == {"characters": [{"name": "方鸿渐"}]}


def test_agnes_chat_no_tool_calls_raises(patch_async, monkeypatch):
    body = {"choices": [{"message": {"content": "I can't do that"}}]}
    fake_post = _fake_chat_post([200], body)
    monkeypatch.setattr(requests, "post", fake_post)
    api = AgnesChatAPI(api_key="k")
    with pytest.raises(RuntimeError):
        asyncio.run(
            api.chat_function_call(
                messages=[{"role": "user", "content": "x"}],
                tools=[],
                tool_choice={"type": "function", "function": {"name": "extract"}},
            )
        )


# --- retry / backoff behavior ---


def test_agnes_chat_retries_on_503(patch_async, monkeypatch):
    body = _tool_call_body(json.dumps({"ok": True}))
    fake_post = _fake_chat_post([503, 503, 200], body)
    monkeypatch.setattr(requests, "post", fake_post)
    api = AgnesChatAPI(api_key="k")
    out = asyncio.run(
        api.chat_function_call(
            messages=[{"role": "user", "content": "x"}],
            tools=[],
            tool_choice={"type": "function", "function": {"name": "extract"}},
            max_retries=3,
        )
    )
    assert fake_post.calls["n"] == 3  # two retries, then success
    assert out == {"ok": True}


def test_agnes_chat_gives_up_after_max_retries(patch_async, monkeypatch):
    fake_post = _fake_chat_post([503, 503, 503, 503], {})
    monkeypatch.setattr(requests, "post", fake_post)
    api = AgnesChatAPI(api_key="k")
    with pytest.raises(RuntimeError):
        asyncio.run(
            api.chat_function_call(
                messages=[{"role": "user", "content": "x"}],
                tools=[],
                tool_choice={"type": "function", "function": {"name": "extract"}},
                max_retries=3,
            )
        )
    # Initial attempt + 2 retries = 3 total posts before giving up.
    assert fake_post.calls["n"] == 3


def test_agnes_chat_uses_chat_bucket(patch_async, monkeypatch):
    captured = {}

    class _L:
        def acquire(self):
            return None

    def fake_get_rate_limiter(size=None, bucket="image"):
        captured["bucket"] = bucket
        return _L()

    monkeypatch.setattr("core.api.rate_limiter.get_rate_limiter", fake_get_rate_limiter)
    fake_post = _fake_chat_post([200], _tool_call_body(json.dumps({"ok": True})))
    monkeypatch.setattr(requests, "post", fake_post)
    api = AgnesChatAPI(api_key="k")
    asyncio.run(
        api.chat_function_call(
            messages=[{"role": "user", "content": "x"}],
            tools=[],
            tool_choice={"type": "function", "function": {"name": "extract"}},
        )
    )
    # Chat must not share the image limiter bucket (independent rate budget).
    assert captured.get("bucket") == "chat"


def test_agnes_chat_collects_errors(patch_async, monkeypatch):
    collected = {"n": 0}

    def fake_collect(*_a, **_k):
        collected["n"] += 1
        return None

    monkeypatch.setattr("core.api.error_collector.collect_error", fake_collect)
    fake_post = _fake_chat_post([503, 503, 200], _tool_call_body(json.dumps({"ok": True})))
    monkeypatch.setattr(requests, "post", fake_post)
    api = AgnesChatAPI(api_key="k")
    asyncio.run(
        api.chat_function_call(
            messages=[{"role": "user", "content": "x"}],
            tools=[],
            tool_choice={"type": "function", "function": {"name": "extract"}},
            max_retries=3,
        )
    )
    # Two retryable 503s => two error records (tagged "chat" upstream).
    assert collected["n"] == 2
