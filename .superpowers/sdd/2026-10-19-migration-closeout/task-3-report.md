# Task 3 report: legacy combined fingerprint deleted

## Change

- Deleted `_PIPELINE_STATE_VERSION`, `_legacy_combined_fingerprint`, and
  `_input_fingerprint` from `core/pipelines/creative_comic.py`, plus the
  legacy hash computed at run start and the adopt-in-place comparison.
- A `state.json` without a structure fingerprint (pre-split states) now takes
  the accepted one-time recompute: `_fresh_state(carried=persisted)` keeps
  `needs_review`, `dismissed_aliases`, and `merged_aliases`, and drops the
  rest of the discarded projection (including tombstones, which name pages of
  that projection). The decision point carries an explicit comment.
- `migrate_legacy_page_keys()`, the fingerprint-matching paths, the render
  soft-invalidation path, and the fresh-state path for changed structure are
  unchanged.

## Test updates

- Removed `test_legacy_combined_fingerprint_aliases_input_fingerprint`
  (the alias with `_input_fingerprint` no longer exists).
- Replaced `test_legacy_state_with_matching_combined_fingerprint_migrates`
  with `test_pre_split_state_takes_the_one_time_recompute`: it seeds a
  review-queue entry and a tombstone, zeroes the split fingerprints, and
  asserts the fingerprints are adopted, the tombstone is dropped (proving
  the rebuild path ran), and the human decision survives.
- `test_gate_does_not_enter_fingerprint_inputs` and
  `test_budget_does_not_enter_fingerprint_inputs` now iterate the two live
  fingerprint functions (`_structure_fingerprint`, `_render_fingerprint`)
  instead of the deleted combined one.

## Boundary checks

- No action key, output path, manifest stage, `state.json` schema field,
  progress label, quota/gate call, or `state.save` boundary changed; the diff
  is confined to the fingerprint/migration decision.
- `grep` confirms no reference to the three removed names remains outside the
  design records (`build/lib` copies excluded).
- Diff stat: 4 files, 27 insertions, 98 deletions.

## Verification

- Full suite on the direct-to-`main` landing:
  `754 passed, 5 skipped` — one fewer test than the 755/5 baseline because
  two bridge tests collapsed into one.
- `ruff check .` and `ruff format --check .` pass.

## Commit

- `d930ef0` `refactor(pipeline): drop the legacy combined fingerprint`
