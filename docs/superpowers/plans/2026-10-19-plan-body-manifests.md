# Plan-Body Manifests Implementation Plan

Design: `docs/superpowers/specs/2026-10-19-plan-body-manifests-design.md`.

Checkboxes reflect the tree as verified on 2026-10-20. Tasks 1-4 have landed in
code; Task 5, the design-of-record revision (Task 6 steps 2-3), and the task
reports (Task 7) remain open. This supersedes the earlier convention that left
every box unchecked in favour of `.superpowers/sdd/2026-10-19-plan-body-manifests/`
- that directory was never created, so the code and this plan are the record.

Caveat on the gate numbers below: the 2026-10-20 verification ran against a
working tree that still carried uncommitted L2 reference-policy changes
(`core/comic/visual_bible.py`, `core/comic/cas.py`,
`core/pipelines/creative_comic.py`, `tests/test_phase3_keys.py`,
`tests/test_visual_bible.py`, and this file). The suite and lint numbers are
real for that snapshot, but the tree was not clean. See "Gaps this plan does
not cover" for requirements that no task below owns.

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

- [x] **Step 1: Add six inputs helpers.** `extract_inputs(chunk)`,
  `bible_inputs(chunk)`, `beats_inputs(chunk, extract_key)`,
  `page_plan_inputs(chunk, beats_json, beats_key)`,
  `storyboard_inputs(chunk, cast)`,
  `page_script_inputs(chunk, storyboard_json, elements_json)`. Each returns the
  ordered `sha256:` digest list the corresponding key already builds.
  Landed at `cas.py:191`, `259`, `289`, `361`, `391`, `422`.
- [x] **Step 2: Refactor the six key functions** to call these helpers, so the
  key and the manifest inputs cannot drift. All six now pass the helper result
  as `inputs=`.
- [x] **Step 3: Tests in `tests/test_cas.py`.** The helpers return the digests
  the key embeds; `beats_action_key` over changed chunk/upstream key still
  changes; `test_phase3_keys.py` stays green (pinned key values unchanged).
  Assertions at `test_cas.py:648-653`.
- [x] **Step 4: Commit** as
  `refactor(cas): expose the plan-stage input digests`. Commit `62e4da0`.

## Task 2: Extract and bible restore, plus the stamp rule

- [x] **Step 1: The restore helper.** In `core/pipelines/creative_comic.py`,
  add `_restore_stage_body(root, key, stage, model)` — `stored_output(...)`,
  `model_validate_json`, warn on `ValueError` and return `None` (a miss).
  Landed at `creative_comic.py:241`.
- [x] **Step 2: Extract.** In the chunk loop, on a cache miss try the helper at
  `extract_key`; a hit fills `elements`, keeps `fresh_extract` false, records
  nothing; a miss keeps today's charge/call path and then `record_ok`s
  `extract.json` (`stage="extract"`, `_EXTRACT_STAGE_SRC`, `snapshot.chat`,
  `extract_inputs(chunk)`). Both paths run the existing cache/save/report tail.
  Restore at `2289`, manifest write at `2310`.
- [ ] **Step 3: The stamp rule.** After the state is selected at run start, if
  `state.visual_bible is None and state.bible_action_keys` then clear the
  stamps, with the "a stamp describes a bible body" comment.
  Not implemented: `bible_action_keys` is only ever read at `2366` and written
  at `2382`/`2448`; no run-start clearing exists. The `needs_bible` test at
  `2367` includes `state.visual_bible is None`, so a lost bible still restores,
  which is why the missing rule does not show up as a failed restore. The
  comment in `test_plan_body_manifests.py:100` already claims the rule ran,
  so that explanation is ahead of the code. A similar stale comment sits at
  `test_plan_body_manifests.py:76`, which still says beats/page_plan "are not
  yet manifest-backed and still call" - they are, and they do not.
- [x] **Step 4: Bible.** Reconcile this run resolves to one of: stamp match
  (nothing, as today), `needs_bible` (restore at `bible_key` or call), or a
  missing stamp (restore, else stamp-only as today; a drifted stamp joins
  `needs_bible`). On a successful call write `bible.json` with the raw
  `VisualBibleReconcileResult` and `bible_inputs(chunk)`. The restored replay
  suppresses only the `prev_hash is None and had_render_assets` invalidation;
  `sanitized` and hash comparisons are unchanged.
  Restore at `2376` and `2384`, manifest write at `2415`.
- [x] **Step 5: Tests in `tests/test_plan_body_manifests.py`.** After a
  finished-page run: extract/bible manifests exist and verify. Delete
  `cache.json`, re-run: `chunk_cache.elements` and the reconcile-derived bible
  are present with zero chat calls, and `bible_action_keys` is repopulated.
  A resume whose bible is restored does not clear `pages_done`.
  Three tests: `:39`, `:50`, `:112`.
- [x] **Step 6: Commit** as
  `feat(pipeline): restore extract and bible bodies from manifests`.
  Commit `9f34e5e`.

## Task 3: Beats and page plans

- [x] **Step 1: Beats.** Where the page-plan stage ensures beats, try the
  helper at `beats_key` first; a hit stamps `action_key`, fills `beat_cache`,
  and runs `sync_adaptation_plans`; a miss keeps the charge/call path and then
  `record_ok`s `beats.json` (`beats_inputs(chunk, extract_key)`).
  Restore at `2622`, manifest write at `2684`.
- [x] **Step 2: Page plan.** Where a missing `pageset` is planned, try the
  helper at the recomputed `_page_plan_key(...)` (after beats are ensured); a
  hit skips charge/call and runs the existing tail (rewrite from bible, stamp
  `action_key`, cache, drop, save, report); the call path additionally
  `record_ok`s `page_plan.json` with the tail body (`page_plan_inputs(chunk,
  _beats_payload(beats), beats_key)`) — never on the restore path.
  Restore at `2660`, manifest write at `2792`.
- [ ] **Step 3: Tests.** Beats and `page_plan` manifests exist after a run;
  with `cache.json` deleted the re-run spends zero chat calls and the reloaded
  `beat_cache` and `page_cache` equal the pre-deletion snapshot body-for-body.
  Partial: `test_finished_page_pipeline.py:1117` asserts `page_plan_calls == 0`
  but does not compare `beat_cache`/`page_cache` body-for-body, and does not
  assert a zero total chat count.
- [x] **Step 4: Commit** as
  `feat(pipeline): restore beats and page plans from manifests`.
  Folded into the combined commit `7962769`, not a standalone commit.

## Task 4: Storyboard and page scripts

- [x] **Step 1: Storyboard.** On a missing board, try the helper at
  `storyboard_key`; a hit fills the chunk cache, stamps the key, saves, and
  reports; a miss keeps the charge/call path and writes `storyboard.json`
  (`storyboard_inputs(chunk, storyboard_cast)`).
  Restore at `3196`, manifest write at `3243`.
- [x] **Step 2: Page script.** When `_page_script_plan` reports missing, try
  the helper at `script_key`; a hit goes straight to `_store_page_script`;
  a miss keeps the charge/call path (including the policy-rejection empty
  script) and writes `page_script.json` (`page_script_inputs(chunk, ...,...)`).
  Restore at `920`, manifest write at `3304`.
- [ ] **Step 3: Tests.** Extend the new pipeline test file with a
  panel-compose run (the suite default): storyboard manifest verifies; cache
  loss plus a re-run restores the board with zero calls. With
  `INKSTONE_PAGE_SCRIPT=1` and a fake that answers `plan_page_script`: script
  manifest verifies and restores with zero calls.
  Not implemented. `test_d2_pipeline.py:155` only deletes the stage's manifest
  so that a stale cache has to re-plan; it proves nothing about a restore.
- [x] **Step 4: Commit** as
  `feat(pipeline): restore storyboard and page scripts from manifests`.
  Folded into `7962769`.

## Task 5: Robustness and the headline resume

- [ ] **Step 1: Corrupt body falls back.** Overwrite the extract CAS object
  with junk, delete `cache.json`, re-run: the extract stage calls once, the
  run completes, and a fresh extract manifest verifies.
  Not implemented as a test. `_restore_stage_body` does warn and return `None`
  on a `ValueError`, so the fallback path exists unexercised.
- [ ] **Step 2: Pre-slice project.** Delete every chat-stage manifest and
  `cache.json`, re-run: the stages re-call (billable, as today) and rewrite
  their manifests.
  Not implemented.
- [ ] **Step 3: Zero-call headline.** A finished-page run, then `cache.json`
  deleted, then a re-run with counting fakes: `chat.calls == image.calls == 0`
  and `chunk_cache`, `beat_cache`, `page_cache`, `visual_bible`,
  `bible_action_keys`, `pages_done`, and `generated` equal the snapshot.
  Not implemented. The closest test asserts only that extract and bible did not
  call, and that `pages_done`/`generated.pages` survive.
- [ ] **Step 4: Budget.** A resume that restores every body completes under a
  budget with no headroom (no `BudgetPaused`), reusing the budget harness in
  `tests/test_budget_pipeline.py` if cheap; otherwise assert the skip in the
  zero-call test only.
  Not implemented.
- [ ] **Step 5: Commit** as
  `test(pipeline): cover the manifest-backed plan resume`.
  Not made.

## Task 6: Records

- [ ] **Step 1: Spec status.** Mark the design Accepted/landed and append the
  revision note (what the implementation changed, if anything).
  Partial: `Status: Accepted` is already set, but nothing marks it landed and
  the revision history holds only the initial-design entry.
- [ ] **Step 2: Design of record.** Append a `Revision 2026-10-19,
  plan-body manifests` paragraph to
  `docs/architecture/2026-09-28-content-addressed-pipeline-design.md` §8:
  the chat caches are manifest-backed; `cache.json` is a hot copy.
  Not done. The revision log in that document (§9, line 718) still says the
  chat-stage cache bodies are not yet manifest-backed.
- [ ] **Step 3: State-split spec sentence.** Replace the "still recomputed by
  re-planning" note with the landed behavior.
  Not done. `2026-10-19-state-split-design.md:131` still describes a lost
  `cache.json` as recomputing the plan stages.
- [x] **Step 4: ROADMAP.** Update the remaining-work sentence: chat-body
  manifest-backing is done; per-stage runners remain.
  `docs/ROADMAP.md:78` names the four stages as manifest-backed.
- [x] **Step 5: CHANGELOG.** `### Added` bullet; update the Upgrade notes
  sentence about deleting `cache.json` (restores now, calls only for stages
  without a manifest).
  `CHANGELOG.md:42`.
- [x] **Step 6: Commit** as `docs(plan): record the plan-body manifest backing`.
  Commit `f1804bd`, which covers steps 4-5 only.

## Task 7: Task reports

- [ ] **Step 1: Write** the five task reports under
  `.superpowers/sdd/2026-10-19-plan-body-manifests/` and commit them with
  `git add -f` as `docs(sdd): add plan-body manifest task reports`.
  Not done; the directory does not exist.
- [x] **Step 2: Final gate.** Full suite, `ruff check`, `ruff format --check`,
  clean tree; report the numbers.
  929 passed, 5 skipped; `ruff check` clean; 241 files already formatted.
  Verified 2026-10-20. The "clean tree" part of this step did not hold: the run
  covered uncommitted L2 reference-policy edits (listed in the header caveat).
  The numbers are reproducible; the cleanliness claim is withdrawn.

## Gaps this plan does not cover

Two requirements in `2026-09-28-content-addressed-pipeline-design.md` §4 are
stated as behavior but are not implemented, and no task above owns them. They
surfaced during the 2026-10-20 review of this plan, not from the plan itself.

- **Tier 3 now respects `panel_continuity` (fixed 2026-10-20).** The §4
  priority table says the adjacent page's blank applies "Only when
  `panel_continuity` is on and the first two tiers have not filled
  `max_refs`". It previously did not: `collect_finished_page_refs` took no mode
  argument and its only guard was `if len(refs) < max_refs and prev_blank:`,
  so `INKSTONE_PANEL_CONTINUITY=0` still injected the previous blank. The
  function now takes `panel_continuity` and gates tier 3 on it
  (`visual_bible.py:1740`), the render loop passes
  `image_config.panel_continuity` (`creative_comic.py:2922`), and the policy
  digest records the switch so two runs with different settings cannot share a
  key (`visual_bible.py:1773`). Tests:
  `test_the_previous_blank_is_gated_by_panel_continuity` and
  `test_the_reference_policy_tracks_the_continuity_switch`. Both were verified
  red-green: removing the gate fails the first, removing the digest field fails
  the second.
- **`continuity_review` for a redrawn page (fixed 2026-10-20).** §4 asks that
  redrawing page *N* make page *N+1* pending review, because *N+1* was
  generated against a blank that no longer exists. The ledger already had the
  full apparatus — `pending_pages`, `reviewed_pages`, the threshold of 30,
  `escalated()`, `confirm_review`, `ContinuityReviewPaused` — but every path
  into it was driven by `reference.version > reviewed_version`, i.e. a
  *portrait* change. A page redraw moves no reference version, so the case §4
  names left no trace: nothing in the tree computed a successor page.

  `ConsistencyLedger.mark_continuity_pending` now flags every character on the
  successor page (`ledger.py:330`), and the render loop calls it after a stale
  page is repainted (`creative_comic.py:3183`). The successor is
  `next_page_in_book`, which chains across a chunk boundary the way
  `previous_page_blank` already does.

  Two design points worth recording, both learned by breaking them:
  - The flag lives in its own `continuity_pages` field rather than being
    expressed by clearing `reviewed_pages`. Clearing it is indistinguishable
    from the legacy-ledger migration ("`reviewed_version > 0` with an empty
    `reviewed_pages` means everything was confirmed"), so `rebuild_from_state`
    silently un-flagged it. A single-page character hit this every time.
  - `pending_pages` is derived on every rebuild, so a flag written only there
    would be erased at the next render boundary — the same boundary that
    escalates it. `rebuild_from_state` merges `continuity_pages` into pending,
    and `confirm_review` clears both.

  This is the *ledger* mark, not a `state.json` field. An earlier note here
  claimed §4 required a `state.json` schema change; that was wrong. §4 line 315
  (decision 18) supersedes the line-313 wording: the flag is aggregated per
  character and written to `consistency.json`, quiet below the threshold.

  Tests: `test_a_redrawn_page_marks_its_successors_pending`,
  `test_the_continuity_flag_survives_a_rebuild`,
  `test_a_single_page_flag_is_not_mistaken_for_a_legacy_confirmation`,
  `test_marking_continuity_is_idempotent`,
  `test_marking_an_unknown_page_changes_nothing`,
  `test_the_successor_page_is_the_next_in_reading_order`, and the pipeline-level
  `test_a_page_redraw_marks_the_next_page_for_continuity_review`.
  Verified by mutation: dropping the continuity merge in
  `rebuild_from_state` fails the two persistence tests; disabling the call site
  fails the pipeline test.
