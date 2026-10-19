# Continuity-Review Escalation Implementation Plan

> **For agentic workers:** Execute the tasks in order on `main`, one commit per
> task. This implements
> [the escalation design](../specs/2026-10-19-ledger-escalation-design.md): land
> the threshold, the render-boundary pause, the confirmation path (library, web,
> CLI), and the aggregated panel that invariant 11 already mandates.

**Goal:** When one character's ledger pending set exceeds 30 pages, the run
refuses further render work until a human confirms ("the pages stay as they
are"), and the web panel surfaces pending sets per character — quiet below the
threshold, stopping above it.

**Architecture:** The policy lives in `core/comic/ledger.py` (constant, query,
confirmation, stop-record dedupe, pause exception), matching how `gate.py` owns
the sample-gate policy and `budget.py` owns the budget pause. The pipeline
raises at the plan→render boundary; `run_until_complete` converts it to
`PausedRun`; the web and CLI confirm. No key, fingerprint, `ProjectState`, or
`consistency.json` schema change.

**Tech Stack:** pydantic v2, pytest, ruff, stdlib http.server + vanilla JS.

## Global Constraints

- Work on `main`; one commit per task; keep plan-task checkboxes unchecked.
- Strict threshold: `len(pending_pages) > LEDGER_ESCALATION_THRESHOLD` (30).
  Below or at it, nothing blocks — no card, no pause, no stop record.
- The escalated signature for the stop record is `{name: {"pending": n,
  "version": v}}`; a resume at an unchanged boundary appends no second record.
- `confirm_review(name)` raises `reviewed_version` to `reference.version` and
  clears `pending_pages`; idempotent; no write when nothing changes.
- No action key or fingerprint may read the ledger or the threshold; no
  persisted schema changes anywhere.
- Full suite (`uv run pytest -o addopts="" -q`) plus `ruff check .` and
  `ruff format --check .` are the gate for every code task.

## Task 1: Ledger policy — threshold, query, confirmation, stop record

**Files:**
- Modify: `core/comic/ledger.py`
- Create: `tests/test_ledger_escalation.py`

- [ ] **Step 1: Add the threshold and the query.** Module level:
  `LEDGER_ESCALATION_THRESHOLD = 30` with a comment naming §4 invariant 11 and
  the "same order as `SAMPLE_GATE_SIZE`" rule; and
  `ConsistencyLedger.escalated(threshold: int | None = None) -> dict[str, list[str]]`
  returning `{name: list(pending_pages)}` for entries strictly above the
  threshold.
- [ ] **Step 2: Add `confirm_review(name) -> bool`.** No-op `False` for an
  unknown character or when already reviewed with empty pending; otherwise
  `reviewed_version = max(reviewed_version, reference.version)`,
  `pending_pages = []`, `updated_at` bump, `True`.
- [ ] **Step 3: Add the pause exception.** `class ContinuityReviewPaused(Exception)`
  with `.escalated: dict[str, list[str]]` and the message
  `"awaiting_human: continuity review pending for <name> (<n> pages), ..."`.
- [ ] **Step 4: Add `record_escalation_stop(output_dir, threshold=None) -> bool`.**
  Build the signature from escalated entries; find the most recent
  `kind == "stop" and stage == "ledger" and reason == "continuity_review"`
  record in `<output_dir>/runs.jsonl` (read through, ignore unparseable lines);
  append via `core.comic.runs.append_run` only when the signature differs (or
  none exists). Returns whether a record was written. No escalated characters →
  `False`, no write.
- [ ] **Step 5: Tests in `tests/test_ledger_escalation.py`** (design AC 1, 4, 5
  at the unit level): the constant equals 30 and shares a power of ten with
  `SAMPLE_GATE_SIZE` (mirror `tests/test_gate.py`); `escalated()` strict at the
  boundary and parameterizable; `confirm_review` clears, is idempotent, leaves
  other characters and `pages` untouched, survives save/load; `record_escalation_stop`
  writes once, dedupes a resume, writes again on a changed signature, and
  writes nothing below the threshold.
- [ ] **Step 6: Run `tests/test_ledger_escalation.py`,
  `tests/test_phase0d_ledger.py`, `tests/test_phase0d_integration.py`, and ruff.**
- [ ] **Step 7: Commit** as `feat(ledger): add the continuity escalation policy`.

## Task 2: Pause the render phase at the boundary

**Files:**
- Modify: `core/pipelines/creative_comic.py`
- Modify: `core/pipelines/run_until_complete.py`
- Create: `tests/test_ledger_escalation_pipeline.py`

- [ ] **Step 1: Add the boundary check.** In the `for phase in ("plan", "render")`
  loop, at the start of the `else:` (render) branch — after the full plan pass,
  before `_expand_page_render_nodes` / `_expand_panel_render_nodes`: rebuild the
  ledger from the freshly planned `page_cache` (save when changed), then
  `escalated = ledger.escalated()`; when non-empty:
  `ledger.record_escalation_stop(output_dir)`, `state.save(state_path)`, and
  `raise ContinuityReviewPaused(escalated)`. Import the exception from
  `core.comic.ledger`; do not import the constant (the test patches the module
  global).
- [ ] **Step 2: Catch it in `run_until_complete`.** Add
  `ContinuityReviewPaused` to the `except (BudgetPaused, GatePaused)` tuple;
  the existing `PausedRun(reason=str(exc))` path carries the message.
- [ ] **Step 3: Tests in `tests/test_ledger_escalation_pipeline.py`.** Using the
  fake providers from `tests/test_finished_page_pipeline.py`:
  - a completed small book, then bump one character's ledger reference
    (`record_reference` + `rebuild_from_state` + `save`) with
    `monkeypatch.setattr("core.comic.ledger.LEDGER_ESCALATION_THRESHOLD", 1)`:
    the resume run raises `ContinuityReviewPaused` with zero provider calls,
    saves state, and writes exactly one ledger stop record;
  - a second unconfirmed resume appends no second stop record;
  - `confirm_review` then a third resume completes with zero calls and no
    pause;
  - control: with the default threshold (30) the same setup runs to completion
    without a pause.
  - `run_until_complete` maps the exception to `PausedRun` with
    `"awaiting_human"` in the reason.
- [ ] **Step 4: Regression check.** `tests/test_finished_page_pipeline.py` and
  `tests/test_panel_pipeline.py` (or the panel-compose test file) must stay
  green: first-run portraits auto-review, so small books never escalate.
- [ ] **Step 5: Commit** as `feat(pipeline): hold render work for an escalated continuity review`.

## Task 3: Web — expose the threshold, confirm endpoint, panel

**Files:**
- Modify: `web/server.py`
- Modify: `web/index.html`
- Modify: `tests/test_web_server.py`

- [ ] **Step 1: Snapshot field.** `_state_snapshot` adds
  `"ledger_escalation_threshold": LEDGER_ESCALATION_THRESHOLD` (import from
  `core.comic.ledger`).
- [ ] **Step 2: Endpoint.** `apply_ledger_confirm(project_id, character) -> dict`
  mirroring `apply_review`: load the project state, `load_or_rebuild` the
  ledger, `confirm_review`, save the ledger, return the refreshed snapshot.
  Unknown character → raise `ValueError` (the handler 404s). Dispatch
  `POST /api/project/<id>/ledger/confirm` in `do_POST` (path parts length 5)
  with `{"character": name}` and the same origin/JSON guards as `/review`.
- [ ] **Step 3: Panel in `web/index.html`.** Add a `ledgerWrap` / `ledgerList`
  section beside `reviewWrap` ("Continuity review"); a `renderLedger(job)` that
  lists characters from `job.ledger_pending` with count, bounded page list, and
  a Confirm button calling the endpoint and re-rendering from the response;
  characters above `job.ledger_escalation_threshold` marked "the next run
  pauses until confirmed". Call it wherever `renderReview(job)` is called and
  from the review/regen response paths that re-render from a snapshot.
- [ ] **Step 4: Tests in `tests/test_web_server.py`.** Snapshot carries the
  threshold; the confirm endpoint clears a seeded pending set, persists
  `consistency.json`, and responds with the refreshed snapshot; an unknown
  character 404s; `web/index.html` contains the panel ids.
- [ ] **Step 5: Commit** as `feat(web): surface and confirm continuity reviews`.

## Task 4: CLI — show and clear the review

**Files:**
- Modify: `core/cli.py`
- Modify: `core/cli_generate.py`
- Modify: `tests/test_phase0d_integration.py` (or the CLI identity test file)

- [ ] **Step 1: `identity --confirm NAME`.** Add the argparse flag; in
  `_run_identity`, after loading state and ledger: `confirm_review`, save on
  change, print the outcome (and exit non-zero on an unknown character, matching
  `rebuild`'s missing-key style).
- [ ] **Step 2: View annotation.** `--view` (and the default print) marks
  characters whose pending count exceeds `LEDGER_ESCALATION_THRESHOLD` as
  escalated ("运行将在渲染前暂停待确认").
- [ ] **Step 3: Paused-run hint.** In `core/cli_generate.py`, branch on
  `"continuity review" in result.reason` *before* the existing
  `"awaiting_human"` gate hint: print the confirmation lines
  (`inkstone identity --out <dir> --confirm <name>` and the web panel).
- [ ] **Step 4: Tests.** Extend the identity CLI test (the faked namespace
  needs the new attribute) plus a `--confirm` case: clears pending, persists,
  unknown name exits non-zero.
- [ ] **Step 5: Commit** as `feat(cli): confirm a continuity review from inkstone identity`.

## Task 5: Records

**Files:**
- Modify: `docs/superpowers/specs/2026-10-19-ledger-escalation-design.md`
  (status → implemented; revision note)
- Modify: `docs/superpowers/specs/2026-09-30-consistency-ledger-design.md`
  (the "the escalation policy is 5b" line gains the landed reference)
- Modify: `docs/architecture/2026-09-28-content-addressed-pipeline-design.md`
  (local record: §13 item 18 / invariant 11 revision)
- Modify: `docs/ROADMAP.md` (local P0 note)
- Modify: `CHANGELOG.md` (`### Added` bullet with the threshold, the pause, and
  the confirmation surfaces)

- [ ] **Step 1: Update the spec status and revision history.**
- [ ] **Step 2: Append the landed note to the 0d design and the design-doc
  revision record.**
- [ ] **Step 3: ROADMAP local note** under the P0 migration section.
- [ ] **Step 4: CHANGELOG bullet** in `### Added`, naming
  `LEDGER_ESCALATION_THRESHOLD = 30`, the render-boundary pause, the web panel,
  and `identity --confirm`.
- [ ] **Step 5: Commit** as `docs(ledger): record the escalation policy`.
