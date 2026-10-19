"""core.harness — prompt-harness identity and rendering.

Phase 0 of the prompt-harness design lands here: a chat request is a value with
a digest, so a prompt change cannot silently reuse a stale plan. Later phases
add the editable component tree and its validator.
"""

from core.harness.request import REQUEST_SCHEMA, ChatRequest

__all__ = ["ChatRequest", "REQUEST_SCHEMA"]
