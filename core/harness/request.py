"""core.harness.request — the exact chat request a stage sends.

A stage's cache identity must cover what was actually sent, not just the
entry function's source. ``ChatRequest.digest()`` is that identity: it folds
the model, messages, tools, and forced tool choice into one canonical hash.

The digest deliberately excludes the provider base URL and credentials. Those
already enter the stage environment (``snapshot.chat``) and must not be part of
content identity.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace

REQUEST_SCHEMA = 1


@dataclass(frozen=True)
class ChatRequest:
    """One forced function call, ready to send and to fingerprint."""

    model: str
    messages: list[dict]
    tools: list[dict]
    tool_choice: dict
    schema: int = REQUEST_SCHEMA

    def digest(self) -> str:
        """A canonical SHA-256 of everything that can change the model's output."""
        payload = {
            "schema": self.schema,
            "model": self.model,
            "messages": self.messages,
            "tools": self.tools,
            "tool_choice": self.tool_choice,
        }
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def with_user_suffix(self, suffix: str) -> ChatRequest:
        """Return a copy whose last user message carries ``suffix``.

        The page-plan language retry is the one caller: it re-sends the same
        request plus one corrective sentence, so the retry must be a new
        request, not a mutation of the first.
        """
        messages = [dict(message) for message in self.messages]
        for message in reversed(messages):
            if message.get("role") == "user":
                message["content"] = str(message.get("content", "")) + suffix
                return replace(self, messages=messages)
        messages.append({"role": "user", "content": suffix.strip()})
        return replace(self, messages=messages)
