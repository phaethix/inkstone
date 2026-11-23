"""Agnes i2i drops negative_prompt; positive prompt wording is authoritative.

The OpenAI-compatible fallback may still use a server-side negative channel,
but Agnes i2i must never send one and the render key must never depend on a
string that never reaches the model.
"""

import asyncio

from core.api import agnes_image
from core.api import image_provider as image_provider_module
from core.api.image_provider import OpenAICompatProvider
from core.comic.cas import render_page_action_key, render_page_inputs
from core.comic.page_prompt import (
    render_finished_page_negative_prompt,
    render_finished_page_prompt,
)
from core.comic.stage_contract import stage_source_hash
from core.pipelines import creative_comic


def _key(**overrides) -> str:
    kwargs = {
        "plan_json": '{"page_id":"p1"}',
        "prompt": "positive prompt",
        "resolved_size": "1024x1536",
        "page_key": "c0000/p0000",
        "ref_policy": "none",
        "env": "envhash",
        "stage_src": "srchash",
    }
    kwargs.update(overrides)
    return render_page_action_key(**kwargs)


class _FakeResponse:
    """Stands in for the requests.Response the providers parse."""

    @staticmethod
    def json():
        return {"data": [{"url": "https://example.test/a.png"}]}


def _capturing_post(sink: dict):
    async def fake_post(**kwargs):
        sink.update(kwargs["json_payload"])
        return _FakeResponse()

    return fake_post


def test_dropped_negative_prompt_does_not_change_the_render_key():
    assert _key() == _key(negative_prompt="no speech bubbles")


def test_different_negative_prompts_keep_the_same_render_key():
    a = _key(negative_prompt="no speech bubbles")
    b = _key(negative_prompt="no captions")
    assert a == b


def test_empty_negative_prompt_keeps_the_legacy_key():
    """Absent and empty must agree, so pre-existing caches are not orphaned."""
    assert _key() == _key(negative_prompt="")


def test_render_page_inputs_ignore_the_negative_prompt():
    assert render_page_inputs("{}", "p") == render_page_inputs("{}", "p", "")
    assert render_page_inputs("{}", "p") == render_page_inputs("{}", "p", "no bubbles")


def test_openai_compat_payload_carries_negative_prompt():
    provider = OpenAICompatProvider(api_key="k", base_url="https://example.test/v1", model="m")
    payload = provider._build_payload("p", [], "1024x1024", negative_prompt="no text")
    assert payload["negative_prompt"] == "no text"


def test_openai_compat_payload_omits_empty_negative_prompt():
    provider = OpenAICompatProvider(api_key="k", base_url="https://example.test/v1", model="m")
    assert "negative_prompt" not in provider._build_payload("p", [], "1024x1024")
    assert "negative_prompt" not in provider._build_payload(
        "p", [], "1024x1024", negative_prompt=""
    )


def test_openai_compat_keeps_its_model_selection():
    """``_build_payload`` must stay an instance method: it reads self.models."""
    provider = OpenAICompatProvider(
        api_key="k", base_url="https://example.test/v1", model="t2i", i2i_model="i2i"
    )
    t2i = provider._build_payload("p", [], None, negative_prompt="no text")
    i2i = provider._build_payload("p", ["https://example.test/r.png"], None)
    assert t2i["model"] == "t2i"
    assert i2i["model"] == "i2i"
    assert t2i["negative_prompt"] == "no text"


def test_openai_compat_sends_negative_prompt_over_the_wire(monkeypatch):
    """The payload assertion above is not enough: it must survive the send path."""
    provider = OpenAICompatProvider(api_key="k", base_url="https://example.test/v1", model="m")
    seen: dict = {}
    monkeypatch.setattr(image_provider_module, "retryable_post", _capturing_post(seen))
    asyncio.run(provider.generate_single_image("p", negative_prompt="no bubbles"))
    assert seen["negative_prompt"] == "no bubbles"


def test_openai_compat_sends_no_negative_field_when_unset(monkeypatch):
    provider = OpenAICompatProvider(api_key="k", base_url="https://example.test/v1", model="m")
    seen: dict = {}
    monkeypatch.setattr(image_provider_module, "retryable_post", _capturing_post(seen))
    asyncio.run(provider.generate_single_image("p"))
    assert "negative_prompt" not in seen


def test_agnes_i2i_never_sends_negative_prompt(monkeypatch):
    """The Agnes i2i workflow silently drops its negative conditioning node."""
    provider = agnes_image.AgnesImageAPI(api_key="k")
    seen: dict = {}

    async def _ref(value):
        return value

    monkeypatch.setattr(agnes_image, "retryable_post", _capturing_post(seen))
    monkeypatch.setattr(agnes_image, "resolve_image_ref_async", _ref)
    asyncio.run(
        provider.generate_single_image(
            "p", reference_image_paths=["https://example.test/r.png"], negative_prompt="no bubbles"
        )
    )
    assert "negative_prompt" not in seen


def test_agnes_t2i_never_sends_negative_prompt(monkeypatch):
    """The text-image queue answers 400 for the field; do not ask."""
    provider = agnes_image.AgnesImageAPI(api_key="k")
    seen: dict = {}
    monkeypatch.setattr(agnes_image, "retryable_post", _capturing_post(seen))
    asyncio.run(provider.generate_single_image("p", negative_prompt="no bubbles"))
    assert "negative_prompt" not in seen


def test_render_stage_src_covers_the_positive_prompt_function():
    """A positive-prompt edit must invalidate cached pages.

    The pipeline keys pages with ``_RENDER_PAGE_STAGE_SRC``. If that constant
    omitted the positive prompt, prompt edits would leave stored pages valid
    cache hits. The deprecated negative helper stays in the hash so old keys
    are invalidated by its source change.
    """
    assert creative_comic._RENDER_PAGE_STAGE_SRC == stage_source_hash(
        render_finished_page_prompt, render_finished_page_negative_prompt
    )


def test_pipeline_source_forwards_the_compat_only_negative_channel():
    """The compat provider may receive it; the Agnes key ignores it."""
    import ast
    import inspect

    source = inspect.getsource(creative_comic)
    tree = ast.parse(source)

    strict_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "render_finished_page_negative_prompt"
        and any(kw.arg == "strict" for kw in node.keywords)
    ]
    assert strict_calls, "the strict retry must escalate the negative prompt too"

    forwarded = [
        kw
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and any(kw.arg == "negative_prompt" for kw in node.keywords)
        for kw in node.keywords
        if kw.arg == "negative_prompt"
    ]
    assert forwarded, "the render call must forward the negative prompt"
