# Task 4 report: CLI confirmation

## Change

- `core/cli.py`:
  - `identity --confirm NAME` — loads state + ledger, exits 1 with a clear
    message for an unknown character, otherwise confirms, saves
    `consistency.json`, and reports the new reviewed version;
  - `identity` view marks a character whose pending count exceeds
    `LEDGER_ESCALATION_THRESHOLD` (`（超过升级阈值：下次运行将在渲染前暂停待确认）`).
- `core/cli_generate.py`: the paused-run output branches on
  `"continuity review" in result.reason` *before* the existing
  `awaiting_human` gate hint (the ledger reason also contains
  `awaiting_human`), printing the `identity --confirm` line and the web panel
  hint.
- `tests/test_phase0d_integration.py`: the existing identity-view test's faked
  namespace gains `confirm`; three new tests — the escalation note appears for
  a 31-page pending set, `--confirm` clears and persists, an unknown name exits
  non-zero.

## Verification

- `uv run pytest -o addopts="" -q tests/test_phase0d_integration.py
  tests/test_ledger_escalation.py`: `25 passed`.
- Full suite: `788 passed, 5 skipped`.
- `ruff check .` and `ruff format --check .`: clean (203 files).

## Commit

- `54f6e92` `feat(cli): confirm a continuity review from inkstone identity`
