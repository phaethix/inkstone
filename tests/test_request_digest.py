"""Request-digest regressions: a prompt edit must never hit a stale plan."""

from core import screenwriter
from core.comic.cas import (
    action_key,
    extract_action_key,
    extract_inputs,
    render_page_action_key,
)
from core.harness.request import ChatRequest
from core.schemas import StoryElements


def _request(text: str = "第一回", *, model: str = "chat-a") -> ChatRequest:
    return screenwriter.build_extract_request(text, model=model)


def test_chat_request_digest_is_stable_and_content_addressed():
    first = _request()
    second = _request()
    assert first.digest() == second.digest()
    assert first.digest().startswith("sha256:")
    assert first.digest() != _request("第二回").digest()
    assert first.digest() != _request(model="chat-b").digest()


def test_changing_the_system_prompt_changes_the_request_digest():
    """The phase-0 defect: ``inspect.getsource`` missed this global."""
    original = screenwriter.SYSTEM_PROMPT
    try:
        before = _request().digest()
        screenwriter.SYSTEM_PROMPT = original + " NEW RULE"
        after = _request().digest()
    finally:
        screenwriter.SYSTEM_PROMPT = original
    assert before != after


def test_changing_a_tool_schema_changes_the_request_digest():
    request = _request()
    other = ChatRequest(
        model=request.model,
        messages=request.messages,
        tools=[{"type": "function", "function": {"name": "different"}}],
        tool_choice=request.tool_choice,
    )
    assert request.digest() != other.digest()


def test_with_user_suffix_returns_a_copy_and_changes_the_digest():
    request = _request()
    retried = request.with_user_suffix("\n\nCRITICAL: fix it")
    assert retried.digest() != request.digest()
    assert request.messages[-1]["content"].endswith("第一回")
    assert retried.messages[-1]["content"].endswith("CRITICAL: fix it")


def test_chat_keys_use_schema_3_only_when_a_request_digest_is_supplied():
    legacy = extract_action_key(chunk="第一回", env="chat-a", stage_src="src")
    keyed = extract_action_key(
        chunk="第一回",
        env="chat-a",
        stage_src="src",
        request_digest=_request().digest(),
    )
    assert legacy != keyed
    assert (
        action_key(
            stage="extract",
            stage_src="src",
            inputs=extract_inputs("第一回"),
            params={},
            env="chat-a",
        )
        == legacy
    )
    assert "request_digest" not in render_page_action_key.__code__.co_varnames


def test_page_plan_key_request_can_pin_accumulated_context():
    """The pipeline pins layouts/bible out of the digest; the sent request keeps them."""
    elements = StoryElements()
    with_context = screenwriter.build_page_plan_request(
        "第一回",
        elements,
        recent_layouts=["splash_action", "dialogue_grid"],
        visual_bible=None,
        model="chat-a",
    )
    pinned = screenwriter.build_page_plan_request(
        "第一回",
        elements,
        recent_layouts=[],
        visual_bible=None,
        model="chat-a",
    )
    assert with_context.digest() != pinned.digest()
    # Two different histories must not move the pinned digest.
    other_history = screenwriter.build_page_plan_request(
        "第一回",
        elements,
        recent_layouts=["wide_establishing"],
        visual_bible=None,
        model="chat-a",
    )
    assert other_history.digest() != pinned.digest()


def test_changing_a_stage_template_changes_the_request_digest(monkeypatch):
    before = _request().digest()
    monkeypatch.setattr(
        "core.screenwriter.render_stage",
        lambda stage, values: "DIFFERENT STAGE TEMPLATE " + values["source_chunk"],
    )
    after = _request().digest()
    assert before != after
