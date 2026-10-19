# Task 5 report: export scheduling and boundary

## Change

Finalization is now scheduled through export nodes after all render work:

- A `_schedule_export` helper adds only the nodes this run will execute: the
  finished-page Webtoon, CBZ, or PDF step; or the panel layout/Webtoon step
  plus its CBZ/PDF bind, which depends on `export.layout`.
- The helper validates the graph, fails loudly when any render node is still
  incomplete, and records completion after the existing export helper returns.
- An outstanding-render check before finalization turns a missed completion
  into a `RuntimeError` instead of a silently skipped export.
- `docs/ROADMAP.md` records the two-phase ephemeral scheduler foundation and
  states that plan nodes remain composite and that plan-cache relocation,
  per-stage runners, legacy-fingerprint removal, and action-key changes remain
  incomplete.

## Behavior boundaries

- Export helper calls, `perf.measure` boundaries, filenames, manifest keys,
  restore behavior, progress labels, `state.stage` transitions, and return
  values are unchanged.
- Export node ids are ephemeral; nothing is persisted and no key derivation
  changed.

## Verification

- No tests were added. The existing suite was run (direct-to-`main` landing):
  `755 passed, 5 skipped` at this head, the same counts as the pre-change
  baseline.
- `ruff check .` and `ruff format --check .` pass with the locked ruff 0.17.0.

## Commit

SHA: `def8b84` (code); this report and the ROADMAP note land in the following
`docs` commit.
