"""t2i rejects negative_prompt; i2i accepts it.

The live service says, verbatim, for a reference-less call:

    400 {"error":{"message":"negative_prompt is not supported by
    text image queue","code":"invalid_request"}}

so forwarding the field on a t2i call turns every such render into a hard
failure. Dropping the field everywhere instead would forfeit the fix on the i2i
path, which is the one the page renderer actually uses. The provider therefore
asks only when the queue accepts it, and escalates a rejection into a single
retry without the field rather than burning the retry budget.

The fake below raises ``HTTPError`` for 4xx exactly as ``retryable_post`` does.
A fake that returned a 400 response instead would let the fallback branch look
correct while never running in production.
"""

import asyncio
import json

import requests

from core.api import agnes_image
from core.api.agnes_image import AgnesImageAPI


class _FakeResponse:
    def __init__(self, status_code: int, body: dict):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body

    @property
    def text(self) -> str:
        return json.dumps(self._body)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Client Error", response=self)


def _ok() -> _FakeResponse:
    return _FakeResponse(200, {"data": [{"url": "https://example.test/a.png"}]})


def _rejection() -> _FakeResponse:
    return _FakeResponse(
        400,
        {
            "error": {
                "message": "negative_prompt is not supported by text image queue",
                "type": "invalid_request",
                "code": "invalid_request",
            }
        },
    )


async def _passthrough_ref(value):
    return value


def _fake_post(responses: list[_FakeResponse], sent: list[dict]):
    """Mimic ``retryable_post``: capture the payload, raise on 4xx."""

    async def post(**kwargs):
        sent.append(dict(kwargs["json_payload"]))
        resp = responses.pop(0)
        resp.raise_for_status()
        return resp

    return post


def _install(monkeypatch, responses: list[_FakeResponse], sent: list[dict]) -> None:
    monkeypatch.setattr(agnes_image, "retryable_post", _fake_post(responses, sent))
    monkeypatch.setattr(agnes_image, "resolve_image_ref_async", _passthrough_ref)


REF = ["https://example.test/ref.png"]


def test_t2i_does_not_send_negative_prompt(monkeypatch):
    """The text-image queue rejects the field outright, so never ask."""
    sent: list[dict] = []
    _install(monkeypatch, [_ok()], sent)
    provider = AgnesImageAPI(api_key="k")
    asyncio.run(provider.generate_single_image("p", negative_prompt="no text"))
    assert sent and "negative_prompt" not in sent[0]


def test_i2i_still_sends_negative_prompt(monkeypatch):
    """i2i accepts the field, and i2i is what the page renderer uses."""
    sent: list[dict] = []
    _install(monkeypatch, [_ok()], sent)
    provider = AgnesImageAPI(api_key="k")
    asyncio.run(
        provider.generate_single_image("p", reference_image_paths=REF, negative_prompt="no text")
    )
    assert sent and sent[0]["negative_prompt"] == "no text"


def test_i2i_rejection_falls_back_to_a_request_without_the_field(monkeypatch):
    """If a queue ever rejects the field, the page must still render."""
    sent: list[dict] = []
    _install(monkeypatch, [_rejection(), _ok()], sent)
    provider = AgnesImageAPI(api_key="k")
    out = asyncio.run(
        provider.generate_single_image("p", reference_image_paths=REF, negative_prompt="no text")
    )
    assert out.data == "https://example.test/a.png"
    assert len(sent) == 2
    assert "negative_prompt" in sent[0]
    assert "negative_prompt" not in sent[1]


def test_unrelated_400_is_not_retried_without_the_field(monkeypatch):
    """A content-policy 400 must keep its meaning, not look like a field issue."""
    sent: list[dict] = []
    policy = _FakeResponse(
        400, {"error": {"message": "prompt violates content policy", "code": "invalid_request"}}
    )
    _install(monkeypatch, [policy, _ok()], sent)
    provider = AgnesImageAPI(api_key="k")
    try:
        asyncio.run(
            provider.generate_single_image(
                "p", reference_image_paths=REF, negative_prompt="no text"
            )
        )
    except requests.HTTPError:
        pass
    else:
        raise AssertionError("an unrelated 400 must propagate")
    assert len(sent) == 1, "an unrelated 400 must not be silently retried"


def test_t2i_rejection_has_no_field_to_drop(monkeypatch):
    """A t2i 400 cannot be blamed on a field that was never sent."""
    sent: list[dict] = []
    _install(monkeypatch, [_rejection()], sent)
    provider = AgnesImageAPI(api_key="k")
    try:
        asyncio.run(provider.generate_single_image("p", negative_prompt="no text"))
    except requests.HTTPError:
        pass
    else:
        raise AssertionError("the rejection must propagate when nothing was sent")
    assert len(sent) == 1
