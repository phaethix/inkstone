# Task 1 report: ledger escalation policy

## Change

- `core/comic/ledger.py`:
  - module-level `LEDGER_ESCALATION_THRESHOLD = 30` (same order as
    `gate.SAMPLE_GATE_SIZE`; §4 invariant 11 / §13 item 18);
  - `ContinuityReviewPaused(Exception)` with `.escalated` and the
    `awaiting_human: continuity review pending for <name> (<n> pages)` message;
  - `_last_ledger_stop(output_dir)` scans `runs.jsonl` for the most recent
    `kind=stop, stage=ledger, reason=continuity_review` record (tolerates
    unparseable lines and non-dict rows);
  - `ConsistencyLedger.escalated(threshold=None)` — strict `>`, returns
    `{name: [pages]}`;
  - `ConsistencyLedger.confirm_review(name)` — raises `reviewed_version` to
    `reference.version`, clears `pending_pages`, idempotent, returns whether
    anything changed;
  - `ConsistencyLedger.record_escalation_stop(output_dir, threshold=None)` —
    appends one `stop` record per changed boundary (signature
    `{name: {pending, version}}`), deduped against the last ledger stop; no
    escalated character → no write.
- Module docstring names the escalation policy (phase 5b).
- New `tests/test_ledger_escalation.py`: 13 tests — threshold held (value 30,
  `< 300`, power-of-ten equality with `SAMPLE_GATE_SIZE`), strict boundary,
  threshold parameter, confirmation (clears, idempotent, unknown no-op, leaves
  other characters, persists), stop record (writes once, dedupes, records a
  changed boundary, writes nothing below threshold, scans past budget stops),
  exception message/attribute.

## Verification

- `uv run pytest -o addopts="" -q tests/test_ledger_escalation.py
  tests/test_phase0d_ledger.py tests/test_phase0d_integration.py`:
  `35 passed`.
- `ruff check` + `ruff format --check` on both files: clean.

## Commit

- `7847657` `feat(ledger): add the continuity escalation policy`
