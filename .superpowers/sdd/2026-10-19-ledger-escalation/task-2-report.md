# Task 2 report: render boundary pause

## Change

- `core/pipelines/creative_comic.py`: at the start of the render branch of the
  `for phase in ("plan", "render")` loop — after the full plan pass, before
  `_expand_page_render_nodes` / `_expand_panel_render_nodes` — the run now
  rebuilds the ledger from the freshly planned `page_cache` (saving when
  changed), reads `ledger.escalated()`, and on a non-empty result records the
  deduplicated stop, saves `state.json`, and raises `ContinuityReviewPaused`.
  The constant itself is not imported, so tests can patch the module global.
- `core/pipelines/run_until_complete.py`: `ContinuityReviewPaused` joins the
  `except (BudgetPaused, GatePaused)` tuple; the existing `PausedRun` path
  carries the message.
- New `tests/test_ledger_escalation_pipeline.py` (3 tests, fakes only):
  - an escalated resume raises with zero provider calls (a stale page that
    otherwise must repaint is held), writes exactly one stop record, saves
    state; an unconfirmed resume appends no second record; after
    `confirm_review` the run repaints exactly the held page and adds no stop;
  - control: below the default threshold the same setup completes silently —
    the repaint goes ahead, no stop record, and `pending_pages()` still carries
    the quiet set for the panel;
  - `run_until_complete` maps the exception to a `PausedRun` whose reason
    contains `awaiting_human` and `continuity review`.

Implementation note (recorded in the spec revision history): the test
originally forced render work by deleting a page's bytes, but the manifest
projection restores deleted page bytes with zero provider calls, so the test
sets `stale_pages` the way a content invalidation does — the same seam the
existing stale-page regression uses.

## Verification

- Full suite: `781 passed, 5 skipped` (was 765 before the slice; +16).
- `ruff check .` and `ruff format --check .`: clean (203 files).

## Commit

- `f32d92f` `feat(pipeline): hold render work for an escalated continuity review`
