"""Provider attempts are audited without keeping the prompt or the error text."""

import hashlib
import json

from core.comic.runs import (
    bind_audit,
    clear_audit,
    image_failure,
    image_intent,
    note_chat_attempt,
    note_chat_failure,
)


def test_an_intent_stores_the_prompt_hash_and_a_failure_stores_the_class(tmp_path):
    image_intent(tmp_path, stage="render", key="c0000-p0000", prompt="draw this", size="1024x1536")
    image_failure(
        tmp_path,
        stage="render",
        key="c0000-p0000",
        error=RuntimeError("secret provider text"),
    )
    text = (tmp_path / "runs.jsonl").read_text(encoding="utf-8")
    rows = [json.loads(line) for line in text.splitlines()]
    assert rows[0]["kind"] == "intent"
    assert rows[0]["prompt_hash"] == hashlib.sha256(b"draw this").hexdigest()
    assert rows[0]["size"] == "1024x1536"
    assert rows[1] == {
        "kind": "failure",
        "key": "c0000-p0000",
        "stage": "render",
        "error": "RuntimeError",
        "ts": rows[1]["ts"],
    }
    logged = text
    assert "draw this" not in logged
    assert "secret provider text" not in logged


def test_a_chat_attempt_is_logged_only_while_a_run_is_bound(tmp_path):
    clear_audit()
    note_chat_attempt("unscoped chapter")
    note_chat_failure(RuntimeError("unscoped secret"))
    assert not (tmp_path / "runs.jsonl").exists()

    prompt = json.dumps([{"role": "user", "content": "extract this chapter"}], ensure_ascii=False)
    bind_audit(tmp_path, "extract", "c0000")
    note_chat_attempt(prompt)
    note_chat_failure(RuntimeError("secret chat text"))
    clear_audit()
    note_chat_attempt("after the run")

    text = (tmp_path / "runs.jsonl").read_text(encoding="utf-8")
    rows = [json.loads(line) for line in text.splitlines()]
    assert rows[0]["prompt_hash"] == hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    assert rows[0]["stage"] == "extract"
    assert rows[0]["size"] == ""
    assert rows[1]["error"] == "RuntimeError"
    assert "extract this chapter" not in text
    assert "secret chat text" not in text
    assert "after the run" not in text
    assert len(rows) == 2
