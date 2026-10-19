# Task 3 report: web panel and confirm endpoint

## Change

- `web/server.py`:
  - `_state_snapshot` gains `ledger_escalation_threshold` (imported from
    `core.comic.ledger`), so the UI marks escalated rows without hardcoding 30;
  - `apply_ledger_confirm(project_id, character)` loads the project, rebuilds
    the ledger, raises `ValueError` for an unknown character, confirms, saves
    `consistency.json`, and returns the refreshed snapshot;
  - `POST /api/project/<id>/ledger/confirm` dispatched in `do_POST` (path
    length 5) behind the existing origin guard; unknown character → 404,
    missing `character` → 400.
- `web/index.html`:
  - a "Continuity review" panel (`ledgerWrap` / `ledgerList`) beside the
    character review, always shown when `ledger_pending` is non-empty;
  - `renderLedger(job)` lists one bounded row per character (name, count,
    first 12 page ids, escalation note against
    `job.ledger_escalation_threshold`), with a Confirm button only a primary
    button when escalated;
  - `ledgerConfirm(name)` posts to the endpoint and re-renders from the
    response; called from the same places as `renderReview`, including the
    review-action response path.
- `tests/test_web_server.py`: snapshot threshold + pending assertion, endpoint
  confirm clears and persists, unknown character 404, and the panel ids /
  endpoint string present in `index.html`.

## Verification

- `uv run pytest -o addopts="" -q tests/test_web_server.py`: `39 passed`.
- Full suite: `785 passed, 5 skipped`.
- `ruff check` + `ruff format --check`: clean.

## Commit

- `2a37605` `feat(web): surface and confirm continuity reviews`
