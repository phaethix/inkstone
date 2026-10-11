"""core.comic.runs — append-only audit log for one project (§6).

``runs.jsonl`` records facts the artifacts cannot: that a call was attempted,
that it failed before any bytes were stored, and that a human or a budget
stopped the run. It is never replayed. A resume decides from manifests and
``state.json``, then writes a new line only when it actually calls again.

The prompt and the error text stay out of the file. The log keeps a hash of
the prompt and the exception class. Image calls record themselves at the
pipeline. Chat calls record themselves only while a scope is bound, so a
provider used outside a run writes nothing.
"""

from __future__ import annotations

import hashlib
import json
import os
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path

# (output_dir, stage, key). Unset means this chat call is not part of a run.
_scope: ContextVar[tuple[Path, str, str] | None] = ContextVar("inkstone_audit_scope", default=None)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def append_run(output_dir: Path, record: dict) -> None:
    """Append one JSON line and fsync it. A crash after this returns keeps the line."""
    path = Path(output_dir) / "runs.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def image_intent(output_dir: Path, *, stage: str, key: str, prompt: str, size: str) -> None:
    """Record that an image call is about to be issued."""
    append_run(
        output_dir,
        {
            "kind": "intent",
            "key": key,
            "stage": stage,
            "prompt_hash": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "size": size,
            "ts": _now_iso(),
        },
    )


def image_failure(output_dir: Path, *, stage: str, key: str, error: BaseException) -> None:
    """Record that the call failed before bytes were stored."""
    append_run(
        output_dir,
        {
            "kind": "failure",
            "key": key,
            "stage": stage,
            "error": type(error).__name__,
            "ts": _now_iso(),
        },
    )


def bind_audit(output_dir: Path, stage: str, key: str) -> None:
    """Attribute the next chat calls to this item. Holds until the next bind or clear."""
    _scope.set((Path(output_dir), stage, key))


def clear_audit() -> None:
    """Drop the chat scope so a later call outside this item writes nothing."""
    _scope.set(None)


def note_chat_attempt(prompt: str) -> None:
    """Record a chat call when a run has bound a scope. A hash stands in for the prompt."""
    scoped = _scope.get()
    if scoped is None:
        return
    output_dir, stage, key = scoped
    append_run(
        output_dir,
        {
            "kind": "intent",
            "key": key,
            "stage": stage,
            "prompt_hash": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "size": "",
            "ts": _now_iso(),
        },
    )


def note_chat_failure(error: BaseException) -> None:
    """Record a chat failure class when a run has bound a scope."""
    scoped = _scope.get()
    if scoped is None:
        return
    output_dir, stage, key = scoped
    append_run(
        output_dir,
        {
            "kind": "failure",
            "key": key,
            "stage": stage,
            "error": type(error).__name__,
            "ts": _now_iso(),
        },
    )
