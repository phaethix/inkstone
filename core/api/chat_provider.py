"""core.api.chat_provider — Chat Provider abstraction for structured extraction.

Wraps the Agnes chat/function-calling endpoint so that M2's screenwriter,
storyboard, and consistency-prompt builders can pull structured JSON out of the
model through a single interface. Like the image layer, the free Agnes API is
the zero-config default, with any OpenAI-compatible chat endpoint as a fallback
to hedge the single-provider risk.

- ``ChatProvider``: abstract base class defining the ``chat_function_call`` contract.
- ``AgnesChatAPI``: default implementation (``agnes-2.0-flash``).
- ``OpenAICompatChatProvider``: fallback for OpenAI/compat chat endpoints.
- ``get_chat_provider``: factory that picks a provider from config / args.

Reliability (retry, exponential backoff + jitter, error collection) is shared
with the image layer via :mod:`core.api.retry` so their semantics never drift.
Chat and image traffic use **separate** rate-limiter buckets
(``bucket="chat"`` vs ``bucket="image"``) so neither starves the other, while
the error log still distinguishes them via the ``model_type`` tag
(``"chat"`` vs ``"image"``) in ``logs/``.
"""

import json
import logging
from abc import ABC, abstractmethod

from requests import Response

from core.api.retry import collect_provider_error, retryable_post
from core.schemas import decode_tool_arguments

logger = logging.getLogger(__name__)

BASE_URL = "https://apihub.agnes-ai.com/v1"


class ChatProvider(ABC):
    """Chat Provider abstraction (the core abstraction of this module).

    All structured extraction (character / setting assets, storyboards) goes
    through ``chat_function_call``: callers pass a tool schema and force the
    model to emit its arguments via ``tool_choice``; they get back a parsed
    dict and never see raw chat plumbing.
    """

    # Subclasses declare the model identifier for config display.
    model: str = ""

    @abstractmethod
    async def chat_function_call(
        self,
        messages: list[dict],
        tools: list[dict],
        tool_choice: dict,
        *,
        model: str | None = None,
        max_retries: int = 3,
        retry_base_delay: float = 4.0,
    ) -> dict:
        """Force a single function call and return its parsed arguments.

        Args:
            messages: Chat messages (system / user / assistant).
            tools: OpenAI-style tool schemas; the model must call one of them.
            tool_choice: Forced ``tool_choice`` (e.g.
                ``{"type": "function", "function": {"name": "extract"}}``).
            model: Override the provider's default model for this call.
            max_retries / retry_base_delay: Retry count and backoff base for
                transient errors (chat is fast, so the base is small).
        Returns:
            The tool's ``arguments`` parsed from JSON into a dict.
        """
        ...


class AgnesChatAPI(ChatProvider):
    """Agnes chat API wrapper (forced function calling -> structured JSON)."""

    def __init__(self, api_key: str, model: str = "agnes-2.0-flash"):
        self.api_key = api_key
        self.model = model
        self.base_url = BASE_URL
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    async def chat_function_call(
        self,
        messages: list[dict],
        tools: list[dict],
        tool_choice: dict,
        *,
        model: str | None = None,
        max_retries: int = 3,
        retry_base_delay: float = 4.0,
    ) -> dict:
        payload = {
            "model": model or self.model,
            "messages": messages,
            "tools": tools,
            "tool_choice": tool_choice,
        }
        fn_name = (
            tool_choice.get("function", {}).get("name", "?")
            if tool_choice.get("type") == "function"
            else "?"
        )
        logger.info(f"[AgnesChat] function call -> {fn_name}")

        async def _collect(*, status_code, response, attempt, exc=None, final=False):
            # Same collection semantics as the image layer, tagged "chat".
            await collect_provider_error(
                "",
                status_code=status_code,
                response=response,
                attempt=attempt,
                exc=exc,
                final=final,
                model_type="chat",
            )

        resp = await retryable_post(
            provider_tag="[AgnesChat]",
            url=f"{self.base_url}/chat/completions",
            headers=self.headers,
            json_payload=payload,
            max_retries=max_retries,
            retry_base_delay=retry_base_delay,
            size=None,
            bucket="chat",
            collect=_collect,
        )
        return self._parse_tool_args(resp)

    @staticmethod
    def _parse_tool_args(resp: Response) -> dict:
        result = resp.json()

        if "error" in result:
            err = result["error"]
            raise RuntimeError(f"Agnes chat error: {err.get('message', err)}")

        choices = result.get("choices", [])
        if not choices:
            raise RuntimeError("Agnes chat: no choices returned")
        msg = choices[0].get("message", {})
        calls = msg.get("tool_calls") or []
        if not calls:
            raise RuntimeError(f"Agnes chat: no tool_calls in response: {json.dumps(msg)[:300]}")
        args_raw = calls[0]["function"]["arguments"]
        try:
            return decode_tool_arguments(args_raw)
        except RuntimeError as e:
            raise RuntimeError(str(e).replace("chat:", "Agnes chat:", 1)) from e


class OpenAICompatChatProvider(ChatProvider):
    """OpenAI-compatible chat endpoint fallback (hedges Agnes single-provider risk).

    Targets any service exposing ``POST {base_url}/chat/completions`` that speaks
    the OpenAI tool-calling protocol (``tools`` + ``tool_choice``). Agnes and
    most compat gateways share this shape, which is exactly why the abstraction
    exists.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    async def chat_function_call(
        self,
        messages: list[dict],
        tools: list[dict],
        tool_choice: dict,
        *,
        model: str | None = None,
        max_retries: int = 3,
        retry_base_delay: float = 4.0,
    ) -> dict:
        payload = {
            "model": model or self.model,
            "messages": messages,
            "tools": tools,
            "tool_choice": tool_choice,
        }
        fn_name = (
            tool_choice.get("function", {}).get("name", "?")
            if tool_choice.get("type") == "function"
            else "?"
        )
        logger.info(f"[OpenAICompatChat] function call -> {fn_name}")

        async def _collect(*, status_code, response, attempt, exc=None, final=False):
            await collect_provider_error(
                "",
                status_code=status_code,
                response=response,
                attempt=attempt,
                exc=exc,
                final=final,
                model_type="chat",
            )

        resp = await retryable_post(
            provider_tag="[OpenAICompatChat]",
            url=f"{self.base_url}/chat/completions",
            headers=self.headers,
            json_payload=payload,
            max_retries=max_retries,
            retry_base_delay=retry_base_delay,
            size=None,
            bucket="chat",
            collect=_collect,
        )
        return AgnesChatAPI._parse_tool_args(resp)


def get_chat_provider(
    provider: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
    **_,
) -> ChatProvider:
    """Factory: return a chat Provider based on configuration.

    Precedence: explicit args > environment variables > default (agnes).
    Configuration is read centrally from :class:`core.config.ChatConfig`.

    Env (ordinary users only need ``AGNES_API_KEY``)::

        AGNES_API_KEY=sk-xxx
        PROVIDER=agnes                      # agnes | openai_compat
        AGNES_CHAT_MODEL=agnes-2.0-flash
        OPENAI_COMPAT_CHAT_BASE_URL=https://...
        OPENAI_COMPAT_CHAT_API_KEY=...
        OPENAI_COMPAT_CHAT_MODEL=...

    Default path: when ``PROVIDER`` is unset or empty => ``agnes``, using only
    ``AGNES_API_KEY`` — realizing the "fill one line and it just works" goal.
    """
    from core.config import ChatConfig

    cfg = ChatConfig()
    provider = (provider or cfg.provider or "agnes").lower()

    if provider == "agnes":
        key = api_key or cfg.agnes_api_key
        if not key:
            raise RuntimeError("PROVIDER=agnes but no AGNES_API_KEY found (env or config)")
        return AgnesChatAPI(api_key=key, model=model or cfg.agnes_chat_model)

    if provider in ("openai_compat", "openai-compatible", "openai", "gemini"):
        base_url = base_url or cfg.openai_compat_chat_base_url
        key = api_key or cfg.openai_compat_chat_api_key
        if not base_url or not key:
            raise RuntimeError(
                "PROVIDER=openai_compat requires OPENAI_COMPAT_CHAT_BASE_URL "
                "and OPENAI_COMPAT_CHAT_API_KEY"
            )
        return OpenAICompatChatProvider(
            api_key=key,
            base_url=base_url,
            model=model or cfg.openai_compat_chat_model or "gpt-4o-mini",
        )

    raise ValueError(f"Unknown PROVIDER={provider!r} (supported: agnes | openai_compat)")
