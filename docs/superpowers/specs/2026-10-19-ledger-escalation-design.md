# Continuity-Review Escalation Design (Phase 5b Follow-Up)

**Date:** 2026-10-19
**Status:** Implemented (2026-10-19)
**Parent:** [Content-Addressed Execution Pipeline](../../architecture/2026-09-28-content-addressed-pipeline-design.md) §4 (the continuity flag), §12 invariant 11, §13 resolved item 18
**Follow-up to:** [Consistency Ledger Design](2026-09-30-consistency-ledger-design.md) — that slice landed `pending_pages` and deferred the policy explicitly: "0d stores `pending_pages`; the escalation policy is 5b." The sample gate landed without it.

## Goal

Implement the half of the continuity-break behaviour that phase 5b deferred:

1. the aggregated UI panel that shows pending pages per character (§12 invariant
   11's default, quiet surfacing — today `ledger_pending` is exposed in the web
   snapshot but nothing renders it), and
2. the escalation threshold: when one character's pending page set exceeds it,
   the run refuses to issue further render work until a human confirms.

Both are zero-quota: no provider calls, no key changes, no new persisted schema.

## Today

- `ConsistencyLedger` records `reference.version`, `reviewed_version`, and the
  derived `pending_pages` per character (`core/comic/ledger.py`).
- A first-ever portrait sets `reviewed_version` to the new version
  (`creative_comic.py` ~L2204): pages not yet enumerated cannot be flagged. A
  *regeneration* bumps the version and leaves `reviewed_version` behind, so a
  rebuild marks the character's pages pending.
- Nothing ever raises `reviewed_version` afterwards: there is no confirmation
  path (no code contains "escalat"), so a regeneration leaves a permanent,
  invisible pending set.
- `web/server.py` puts `ledger_pending` into every job snapshot; `web/index.html`
  never reads it. `inkstone identity` prints `待复核 N 页` per character.
- The design's wording, verbatim (§4): the default is "**quiet**: the break is
  recorded in the ledger (`consistency.json`) and shown aggregated in the UI
  panel, **without a card, without blocking the run, and without entering
  `run`'s pause path**. Only when the pending pages for one character exceed a
  threshold (by default the same order as `sample_gate_size`, held by a test)
  does it escalate to a human confirmation." Anti-pattern 17 bounds this: the
  flag must never become an always-on gate. Escalation is the threshold-gated
  exception, not the default.

## The contract

### Threshold

- `LEDGER_ESCALATION_THRESHOLD = 30` in `core/comic/ledger.py`: by default the
  same order as `SAMPLE_GATE_SIZE` (30), held by a test the way
  `test_gate.py` holds the gate size.
- The comparison is strict: `len(pending_pages) > threshold`. At or below the
  threshold the behaviour is quiet (ledger + panel only).
- `ConsistencyLedger.escalated(threshold: int | None = None)` returns
  `{name: [page, ...]}` for characters above the threshold. Callers may pass a
  smaller threshold (tests); nothing persists it.

### Escalation: refuse render work, recoverably

- The check runs in `creative_comic` when the render phase is entered — after
  the full plan pass, before any render node is expanded or dispatched. This is
  the same "buy information before the batch" boundary the sample gate uses,
  with the ledger's unit (a character's whole pending set) instead of a page.
- When any character is escalated, the run:
  1. refreshes the ledger from the plans the pass just produced
     (`rebuild_from_state`), so pending sets are current, not stale;
  2. appends one `stop` record to `runs.jsonl` — only when the escalated
     boundary changed since the most recent ledger stop (§6: "Repeating a pause
     at an unchanged boundary appends no second stop record"). The signature is
     `{name: {"pending": n, "version": v}}`;
  3. saves `state.json` (the plan pass may have produced billable output);
  4. raises `ContinuityReviewPaused`.
- `run_until_complete` catches it beside `BudgetPaused` / `GatePaused` and
  returns a `PausedRun`; the web job reports `status: "paused"` with the reason.
  The pause is recoverable and re-checked on every render-phase entry, so an
  unconfirmed resume pauses again without duplicating the stop record.
- The escalation is unconditional at the boundary, `finished_page` and panel
  modes alike: the pending decision is due regardless of which pages render
  next, and the check stays a single early branch.

### Confirmation

- `ConsistencyLedger.confirm_review(name) -> bool` raises the character's
  `reviewed_version` to its `reference.version` and clears `pending_pages`;
  returns whether anything changed. Idempotent. This is the only state write
  the escalation adds, and it is the "Phase 5b raises it on acceptance" line
  the `reviewed_version` field was introduced for.
- Three surfaces, one behaviour:
  - library: `confirm_review`;
  - web: `POST /api/project/<id>/ledger/confirm` with `{"character": name}`,
    responding with the refreshed snapshot (models `apply_review`);
  - CLI: `inkstone identity --out <dir> --confirm <name>`.
- Confirmation means "the affected pages stay as they are". Choosing redraws
  remains the existing stale-page / regen flows; the panel does not mark pages
  stale.

### The panel

- `web/index.html` renders `job.ledger_pending` as an aggregated "Continuity
  review" panel beside the existing character review: one row per character
  with its pending count, the positional page ids (visually bounded), and a
  Confirm button. A character above the threshold is marked as escalating
  ("the next run pauses until confirmed").
- The panel shows whenever `ledger_pending` is non-empty — this is invariant
  11's default surfacing, independent of escalation.
- `_state_snapshot` gains `ledger_escalation_threshold` so the UI can mark
  escalated rows without hardcoding 30.
- `inkstone identity --view` appends the escalation note to characters above
  the threshold, so the CLI and the web tell the same story.

## What does not change

- No action key, fingerprint, `h_env`, manifest, or `ProjectState` field
  changes. The ledger (like budget and gate) enters no key (§8 invariant 3,
  §12 invariant 10); the new `stop` record is audit only, never replayed (§6).
- `consistency.json` keeps its exact shape — same fields, same
  `schema_version`. Confirmation writes `reviewed_version` / `pending_pages`,
  which already exist.
- Below the threshold nothing blocks: no card, no pause, no stop record.
- The web snapshot gains one read-only integer field; no other reader changes.

## Failure handling

| Failure | Behavior |
|---|---|
| Confirm an unknown character | `confirm_review` returns `False`; the endpoint 404s; nothing is written |
| Confirm an unchanged character | `False`; no write, no `updated_at` bump |
| `runs.jsonl` missing or empty at the pause | the stop record is created |
| Crash between the stop record and the raise | harmless; the resume re-detects the boundary and dedupes |
| Ledger edited (or confirmed) while paused | the next run re-derives and proceeds or pauses accordingly |
| Escalated character later alias-merged | `rename_character` already merges pending and reviewed versions; confirmation uses the merged entry |

## Acceptance criteria

1. Threshold: `LEDGER_ESCALATION_THRESHOLD == 30`, held by a test that also
   asserts the same order as `SAMPLE_GATE_SIZE`; `escalated()` is strict and
   parameterized.
2. Quiet default: pending at or below the threshold pauses nothing and records
   nothing; the panel shows it.
3. Escalation: a run with an escalated character raises `ContinuityReviewPaused`
   at the render boundary with zero render calls issued; the state produced by
   the plan pass is saved.
4. Idempotent audit: exactly one ledger stop record for an unchanged boundary
   across resumes; a changed boundary records again.
5. Confirmation: `confirm_review` clears pending and is idempotent; the web
   endpoint and CLI flag both persist it; the next run proceeds without a
   pause.
6. Orthogonality: ledger changes do not change any key or fingerprint; no
   `ProjectState` or `consistency.json` schema change.
7. Full suite (`uv run pytest -o addopts="" -q`) plus `ruff check .` and
   `ruff format --check .` pass.

## Out of scope

- Per-page continuity flags (§13 item 18 rejects them) and the §4
  `continuity_review` marker for the redraw-adjacent-page case — a different
  mechanism that still lives only in the design.
- Panel-driven redraw marking; the existing gate/stale/regen flows remain the
  only way to schedule repaints.
- Auto-confirmation policies, notifications, and any persistence of the
  escalation state beyond the audit record.
- The pre-existing, unconsumed `identity --merge` flag; untouched.

## Risks

- **Over-eager pause after a mid-plan regeneration.** A portrait regenerated
  while planning can escalate the same run whose boundary follows. Bounded by
  the threshold (30+ pages for one character) and by confirmation being one
  click; the constant is the tuning point.
- **Redraw jobs pause too.** A regen batch that would use the new reference
  waits for the review first, by design.
- **Duplicate stop records** would pollute the audit; mitigated by the
  signature dedupe and held by a test.
- **Panel noise.** The row list is bounded visually; the panel is quiet
  surfacing, not a card, and disappears after confirmation.

## Revision history

- 2026-10-19: initial design; settles the 0d-deferred escalation policy.
- 2026-10-19: implemented as designed — threshold 30 held by a test, the
  render-boundary pause with the deduplicated stop record, confirmation via the
  ledger API / web endpoint / CLI flag, and the aggregated panel. One decision
  recorded while implementing: the boundary check rebuilds the ledger from the
  freshly planned `page_cache` before reading `escalated()`, so a pending set
  derived from earlier runs cannot go stale between saves.
