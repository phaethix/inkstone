# Task 2 report: render hot path off the heavy cache file

## Change

- Switched the nine render-loop save sites to
  `state.save(state_path, caches=False)`:
  - `_render_page_node` letter reuse (L2420), letter write (L2513),
    content-policy tombstone (L2559);
  - the per-chunk page-pass saves (L2660 finished path, L2674 chunk done);
  - the continuity-panel saves (L2902 policy skip, L2907 panel done);
  - the panel-batch saves (L2962 batch, L2968 chunk done).
  Each site was read in place before switching: none of them mutates a heavy
  field since the last full save (the heavy mutations of the chunk — extract,
  bible, beats, page plan, storyboard, page script — all sit above them and
  are followed by full saves).
- Every other save site — 18 now — stays full. The first light site carries a
  comment stating the rule ("no heavy cache changed since the last full save").
- One exception was found and fixed: the storyboard action-key stamp
  (`cached_chunk.storyboard_action_key = storyboard_key`, ~L2697) mutated the
  heavy `chunk_cache` with no immediate save. It now runs
  `state.save(state_path)` right after the stamp, so "heavy mutation ⇒ full
  save" holds without exception even when later render saves are
  projection-only. New full-save site count: 27 total (9 light + 18 full).

## Tests

- `tests/test_finished_page_pipeline.py::test_finished_page_mode_writes_generated_pages`
  now asserts the on-disk split after a real run: `state.json` contains none of
  the four keys and `cache.json` parses with exactly them. The existing resume
  leg of the same test (zero chat calls, zero image calls) doubles as the merge
  proof: the resumed run only skips re-planning because `cache.json` was
  loaded.

## Verification

- Modified pipeline test + state-split tests + d2/fingerprint/creative-comic
  files: 72 passed.
- Full suite: `763 passed, 5 skipped`.
- `ruff check` / `ruff format --check` pass on both changed files.

## Commit

- `b7ff429` `perf(pipeline): keep render saves off the heavy cache file`
