# Task 3 report: finished-page render nodes

## Change

The render pass expands an ephemeral page graph after the plan barrier: one
`render.page:{ci:04d}:{pi:04d}` node per planned page, chained in reading order
so each page still reads the blank of the page before it. The existing one-page
executor moved into `_render_page_node` with no behavior change (manifest
restore, tombstone hits, stale checks, letter resume, projection updates,
`state.save`, progress, gate hold, size fallback, stricter retry). Dispatch
runs the chunk's nodes through `StageGraph.ready` and completes each node after
its handler returns; chunk skip paths (`skipped_chunks`, completed chunks)
advance their nodes through `_complete_render_chunk_nodes`. Scheduler ids are
never persisted: `page_state_key` still derives only from `(chunk, page)`.

## Behavior boundaries

- Page order, keys, paths, save points, gate admission, quota charges, and
  error propagation are unchanged. Skips inside the extracted handler are
  `return`s from the closure instead of `continue`s of the outer loop; the
  inner retry loop keeps its own `continue`s.
- The graph is rebuilt in memory for every run and is not persistence or a
  recovery source. Plan-side caches stay in `state.json`.
- Loop-bound names (`state`, `chunk`, `ci`, `pageset`, `effective_style`) are
  bound as default arguments, matching the `_render_portrait` pattern and
  keeping ruff `B023` clean.

## Static review

- Diff compared against the pre-extraction page loop for keys, paths, save
  boundaries, and quota charges; no stage key, output path, progress label, or
  `state.json` field changed.
- This commit also normalizes pre-existing format drift and import order in
  `creative_comic.py`; main already failed `ruff format --check` and
  `ruff check` for that file.

## Verification

- No tests were added. Because this landed directly on `main` (the plan's
  static-only constraint applied to the isolated worktree), the existing suite
  was run after the commit series: `755 passed, 5 skipped`.
- `ruff check .` and `ruff format --check .` pass with the locked ruff 0.17.0.

## Commit

SHA: `f193e40` (preceded by `a763ce4`, which formatted the other drifted
modules).
