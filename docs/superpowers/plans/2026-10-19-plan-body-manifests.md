# Plan-Body Manifests Implementation Plan

Design: `docs/superpowers/specs/2026-10-19-plan-body-manifests-design.md`.
Task checkboxes are intentionally left unchecked; the task reports under
`.superpowers/sdd/2026-10-19-plan-body-manifests/` are the record.

## Global Constraints

- No key, fingerprint, `ProjectState`, `cache.json`, or `consistency.json`
  schema change. Key functions may be refactored to share input helpers only
  if their output strings are byte-identical (existing key tests pin this).
- Writes happen on the fresh-call path only; restores never rewrite a
  manifest. All manifest traffic goes through `core/comic/cas.py` helpers.
- Restores never `_charge`; provider counters in tests are the proof.
- The full gate per code task: `uv run pytest -o addopts="" -q`,
  `ruff check`, `ruff format --check`.

## Task 1: Stage input digests in `core/comic/cas.py`

- [ ] **Step 1: Add six inputs helpers.** `extract_inputs(chunk)`,
  `bible_inputs(chunk)`, `beats_inputs(chunk, extract_key)`,
  `page_plan_inputs(chunk, beats_json, beats_key)`,
  `storyboard_inputs(chunk, cast)`,
  `page_script_inputs(chunk, storyboard_json, elements_json)`. Each returns the
  ordered `sha256:` digest list the corresponding key already builds.
- [ ] **Step 2: Refactor the six key functions** to call these helpers, so the
  key and the manifest inputs cannot drift.
- [ ] **Step 3: Tests in `tests/test_cas.py`.** The helpers return the digests
  the key embeds; `beats_action_key` over changed chunk/upstream key still
  changes; `test_phase3_keys.py` stays green (pinned key values unchanged).
- [ ] **Step 4: Commit** as
  `refactor(cas): expose the plan-stage input digests`.

## Task 2: Extract and bible restore, plus the stamp rule

- [ ] **Step 1: The restore helper.** In `core/pipelines/creative_comic.py`,
  add `_restore_stage_body(root, key, stage, model)` — `stored_output(...)`,
  `model_validate_json`, warn on `ValueError` and return `None` (a miss).
- [ ] **Step 2: Extract.** In the chunk loop, on a cache miss try the helper at
  `extract_key`; a hit fills `elements`, keeps `fresh_extract` false, records
  nothing; a miss keeps today's charge/call path and then `record_ok`s
  `extract.json` (`stage="extract"`, `_EXTRACT_STAGE_SRC`, `snapshot.chat`,
  `extract_inputs(chunk)`). Both paths run the existing cache/save/report tail.
- [ ] **Step 3: The stamp rule.** After the state is selected at run start, if
  `state.visual_bible is None and state.bible_action_keys` then clear the
  stamps, with the "a stamp describes a bible body" comment.
- [ ] **Step 4: Bible.** Reconcile this run resolves to one of: stamp match
  (nothing, as today), `needs_bible` (restore at `bible_key` or call), or a
  missing stamp (restore, else stamp-only as today; a drifted stamp joins
  `needs_bible`). On a successful call write `bible.json` with the raw
  `VisualBibleReconcileResult` and `bible_inputs(chunk)`. The restored replay
  suppresses only the `prev_hash is None and had_render_assets` invalidation;
  `sanitized` and hash comparisons are unchanged.
- [ ] **Step 5: Tests in `tests/test_plan_body_manifests.py`.** After a
  finished-page run: extract/bible manifests exist and verify. Delete
  `cache.json`, re-run: `chunk_cache.elements` and the reconcile-derived bible
  are present with zero chat calls, and `bible_action_keys` is repopulated.
  A resume whose bible is restored does not clear `pages_done`.
- [ ] **Step 6: Commit** as
  `feat(pipeline): restore extract and bible bodies from manifests`.

## Task 3: Beats and page plans

- [ ] **Step 1: Beats.** Where the page-plan stage ensures beats, try the
  helper at `beats_key` first; a hit stamps `action_key`, fills `beat_cache`,
  and runs `sync_adaptation_plans`; a miss keeps the charge/call path and then
  `record_ok`s `beats.json` (`beats_inputs(chunk, extract_key)`).
- [ ] **Step 2: Page plan.** Where a missing `pageset` is planned, try the
  helper at the recomputed `_page_plan_key(...)` (after beats are ensured); a
  hit skips charge/call and runs the existing tail (rewrite from bible, stamp
  `action_key`, cache, drop, save, report); the call path additionally
  `record_ok`s `page_plan.json` with the tail body (`page_plan_inputs(chunk,
  _beats_payload(beats), beats_key)`) — never on the restore path.
- [ ] **Step 3: Tests.** Beats and `page_plan` manifests exist after a run;
  with `cache.json` deleted the re-run spends zero chat calls and the reloaded
  `beat_cache` and `page_cache` equal the pre-deletion snapshot body-for-body.
- [ ] **Step 4: Commit** as
  `feat(pipeline): restore beats and page plans from manifests`.

## Task 4: Storyboard and page scripts

- [ ] **Step 1: Storyboard.** On a missing board, try the helper at
  `storyboard_key`; a hit fills the chunk cache, stamps the key, saves, and
  reports; a miss keeps the charge/call path and writes `storyboard.json`
  (`storyboard_inputs(chunk, storyboard_cast)`).
- [ ] **Step 2: Page script.** When `_page_script_plan` reports missing, try
  the helper at `script_key`; a hit goes straight to `_store_page_script`;
  a miss keeps the charge/call path (including the policy-rejection empty
  script) and writes `page_script.json` (`page_script_inputs(chunk, ...,...)`).
- [ ] **Step 3: Tests.** Extend the new pipeline test file with a
  panel-compose run (the suite default): storyboard manifest verifies; cache
  loss plus a re-run restores the board with zero calls. With
  `INKSTONE_PAGE_SCRIPT=1` and a fake that answers `plan_page_script`: script
  manifest verifies and restores with zero calls.
- [ ] **Step 4: Commit** as
  `feat(pipeline): restore storyboard and page scripts from manifests`.

## Task 5: Robustness and the headline resume

- [ ] **Step 1: Corrupt body falls back.** Overwrite the extract CAS object
  with junk, delete `cache.json`, re-run: the extract stage calls once, the
  run completes, and a fresh extract manifest verifies.
- [ ] **Step 2: Pre-slice project.** Delete every chat-stage manifest and
  `cache.json`, re-run: the stages re-call (billable, as today) and rewrite
  their manifests.
- [ ] **Step 3: Zero-call headline.** A finished-page run, then `cache.json`
  deleted, then a re-run with counting fakes: `chat.calls == image.calls == 0`
  and `chunk_cache`, `beat_cache`, `page_cache`, `visual_bible`,
  `bible_action_keys`, `pages_done`, and `generated` equal the snapshot.
- [ ] **Step 4: Budget.** A resume that restores every body completes under a
  budget with no headroom (no `BudgetPaused`), reusing the budget harness in
  `tests/test_budget_pipeline.py` if cheap; otherwise assert the skip in the
  zero-call test only.
- [ ] **Step 5: Commit** as
  `test(pipeline): cover the manifest-backed plan resume`.

## Task 6: Records

- [ ] **Step 1: Spec status.** Mark the design Accepted/landed and append the
  revision note (what the implementation changed, if anything).
- [ ] **Step 2: Design of record.** Append a `Revision 2026-10-19,
  plan-body manifests` paragraph to
  `docs/architecture/2026-09-28-content-addressed-pipeline-design.md` §8:
  the chat caches are manifest-backed; `cache.json` is a hot copy.
- [ ] **Step 3: State-split spec sentence.** Replace the "still recomputed by
  re-planning" note with the landed behavior.
- [ ] **Step 4: ROADMAP.** Update the remaining-work sentence: chat-body
  manifest-backing is done; per-stage runners remain.
- [ ] **Step 5: CHANGELOG.** `### Added` bullet; update the Upgrade notes
  sentence about deleting `cache.json` (restores now, calls only for stages
  without a manifest).
- [ ] **Step 6: Commit** as `docs(plan): record the plan-body manifest backing`.

## Task 7: Task reports

- [ ] **Step 1: Write** the five task reports under
  `.superpowers/sdd/2026-10-19-plan-body-manifests/` and commit them with
  `git add -f` as `docs(sdd): add plan-body manifest task reports`.
- [ ] **Step 2: Final gate.** Full suite, `ruff check`, `ruff format --check`,
  clean tree; report the numbers.
