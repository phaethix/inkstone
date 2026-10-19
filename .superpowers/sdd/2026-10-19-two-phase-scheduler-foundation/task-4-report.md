# Task 4 report: panel-compose render nodes

## Change

Panel work is expanded from the planned storyboards after the plan barrier:

- With `panel_continuity`, one ordered `render.panel:{ci:04d}:{pi:04d}` node per
  positional panel, chained across chunks, mirroring the sequential run that
  hands `prev_panel_local` forward. `_render_continuity_panel_node` carries the
  existing skip, gate, policy-rejection, and state-save behavior; the
  `prev_panel_local` handoff moved into the closure via `nonlocal`.
- Without continuity, one `render.panel_batch:{ci:04d}` node per chunk. The
  batch's positional state keys are recorded as in-memory scheduler metadata
  and checked at dispatch before the existing gate filter, bounded semaphore,
  and `asyncio.gather` path runs unchanged.

Chunk-complete resume paths advance their panel nodes through
`_complete_render_chunk_nodes`, so later phases stay behind completed work.

## Behavior boundaries

- Panel order, request construction, reference assembly, L3 processing, skip
  recording, mutation order, and single batch save are unchanged; result-to-
  panel pairing stays positional.
- Batch member ids and node ids never enter `state.json` or any persisted
  artifact.
- Loop-bound names (`state`, `ci`, `_render_panel`) are bound as default
  arguments, keeping ruff `B023` clean.

## Static review

- Diff compared against the previous continuity loop and batch gather body;
  no stage key, output path, progress label, or persisted field changed.
- Phase barrier: every panel node depends on completed plan nodes only, and
  render dispatch still happens strictly after the plan generator exhausts.

## Verification

- No tests were added. The existing suite was run (direct-to-`main` landing):
  focused `test_creative_comic`, finished-page, d2, phase0d, run-until-complete,
  cancel, and tombstone files passed at this commit; the full suite ended at
  `755 passed, 5 skipped`.
- `ruff check .` and `ruff format --check .` pass with the locked ruff 0.17.0.

## Commit

SHA: `db8aaf5`.
