# Content-Addressed Migration Close-Out Implementation Plan

> **For agentic workers:** Execute the tasks in order on `main`, one commit per
> task. This closes the last items the parent design records as "not landed":
> the idempotency probe verdict, the one-time invalidation announcement, and
> the deletion of `_PIPELINE_STATE_VERSION` and the legacy combined fingerprint.

**Goal:** Empty the "not landed" list in
[docs/architecture/2026-09-28-content-addressed-pipeline-design.md](../../architecture/2026-09-28-content-addressed-pipeline-design.md)
§9 (the two-phase scheduler already landed) and make the migration record honest.

**Architecture:** No new machinery. Record the probe verdict that resolved item
27 mandates; announce the accepted one-time invalidation (resolved item 21);
delete the legacy combined fingerprint and its adopt-in-place branch so a
stored project without split fingerprints takes the accepted recompute path
once, carrying human decisions; refresh the design record and ROADMAP.

**Tech Stack:** existing pipeline, pytest, ruff.

## Global Constraints

- Work on `main`; one commit per task.
- Do not change action keys, manifests, output paths, `state.json` schema,
  progress labels, quota, gate, or human-decision fields.
- Deleting the legacy bridge is behavior-preserving except for the accepted
  one-time invalidation: a pre-split `state.json` recomputes once instead of
  being adopted in place.
- Tests that covered the removed bridge are updated to the new expected
  behavior (recompute once, decisions carried), not deleted silently.
- The full suite plus `ruff check` and `ruff format --check` are the gate for
  the code task.

## Task 1: Execute the idempotency probe and record the verdict

**Files:**
- Modify: `scripts/probe_image_idempotency.py` (load `.env` like
  `scripts/probe_m2.py` does, so the documented "fill one line" setup works)
- Modify: `CHANGELOG.md`
- Modify: design doc §13 (residual uncertainty 2 answered)

- [ ] **Step 1: Add the minimal `.env` loader to the probe script.** Copy the
  `_load_dotenv()` pattern already used by `scripts/probe_m2.py`; no new
  dependency.
- [ ] **Step 2: Run the probe once** (`uv run python scripts/probe_image_idempotency.py`;
  costs exactly two image calls) and capture the verdict text.
- [ ] **Step 3: Record the verdict in `CHANGELOG.md`** under a `### Notes`
  section: provider, date, identical/different bytes, and the §8 consequence
  (`summary()` wording).
- [ ] **Step 4: Answer residual uncertainty 2 in the design doc** with a dated
  revision paragraph; do not rewrite the existing question text.
- [ ] **Step 5: Commit** as `docs: record the idempotency probe verdict`.

## Task 2: Announce the one-time invalidation

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Add an `### Upgrade notes` section at the top of
  `[Unreleased]`** covering, per resolved items 21/27: one recompute of an
  existing project under the new keys; human decisions survive; the batch
  framing ("recomputed over several days, not voided at once"); a checkable
  expectation from 1 RPM and page count; `--budget N` and the `--carry-over`
  re-arm; and "progress is counted in produced pages, not percentage".
- [ ] **Step 2: Commit** as `docs(changelog): announce the one-time key invalidation`.

## Task 3: Delete the legacy combined fingerprint

**Files:**
- Modify: `core/pipelines/creative_comic.py`
- Modify: `tests/test_fingerprint_split.py`, `tests/test_gate.py`, `tests/test_budget.py`

- [ ] **Step 1: Delete `_PIPELINE_STATE_VERSION`, `_legacy_combined_fingerprint`,
  and `_input_fingerprint`.** The only caller computed the legacy hash for the
  pre-split adopt-in-place comparison; remove that call and the comparison.
- [ ] **Step 2: Route pre-split states through `_fresh_state(carried=persisted)`**
  (they have empty structure fingerprints, so the existing "structure changed"
  branch already does this; keep one explicit, commented branch so the accepted
  invalidation is visible at the decision point). Keep
  `migrate_legacy_page_keys()` and the fingerprint-matching paths unchanged.
- [ ] **Step 3: Update the three affected tests.** Replace the
  `_input_fingerprint` alias test with nothing; rewrite the pre-split migration
  test to assert recompute-once plus human-decision carry; point the gate and
  budget fingerprint-input tests at the two live fingerprint functions.
- [ ] **Step 4: Run the full suite** (`uv run pytest -o addopts="" -q`) and
  `ruff check .` / `ruff format --check .`.
- [ ] **Step 5: Commit** as `refactor(pipeline): drop the legacy combined fingerprint`.

## Task 4: Refresh the migration record

**Files:**
- Modify: design doc §9 (dated revision; the "not landed" list is now empty)
- Modify: `docs/ROADMAP.md` (dated local note)

- [ ] **Step 1: Append a `Revision 2026-10-19` paragraph to §9** recording:
  two-phase scheduler landed earlier the same day; legacy combined fingerprint
  and `_PIPELINE_STATE_VERSION` deleted; the one-time invalidation announced in
  CHANGELOG; the probe executed with its verdict; plan-cache relocation and
  per-stage runners remain later work.
- [ ] **Step 2: Add the matching local note to the ROADMAP** P0 section.
- [ ] **Step 3: Commit** as `docs(pipeline): record the migration close-out`.

## Acceptance criteria

1. `_PIPELINE_STATE_VERSION`, `_legacy_combined_fingerprint`, and
   `_input_fingerprint` no longer exist anywhere outside the design record.
2. A stored project without split fingerprints recomputes once; its
   needs-review, dismissed, and merged alias state survives.
3. The probe verdict and the one-time invalidation announcement are both in
   `CHANGELOG.md`; §13's residual uncertainty 2 is answered with the verdict.
4. Full suite green; ruff lint and format clean.

## Out of scope

- Moving `chunk_cache`, `page_cache`, `beat_cache`, or the visual bible out of
  `state.json` (plan-cache relocation; needs its own spec).
- Per-stage runners, action-key changes, manifest/outcome semantics, and the
  ledger escalation threshold.
