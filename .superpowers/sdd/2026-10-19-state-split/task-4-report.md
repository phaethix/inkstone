# Task 4 report: contract recorded

## Change

- Design doc (`docs/architecture/2026-09-28-content-addressed-pipeline-design.md`,
  gitignored, local-only):
  - §6 storage tree gains `cache.json` beside `state.json`.
  - §6 "What the projection must and must not contain" gains a landed note:
    the four heavy caches live in `cache.json` now; cache first, checkpoint
    second; render saves write the checkpoint only; the chat-stage bodies are
    still re-plan-recomputable rather than manifest-backed.
  - §9's two-part-contract item is marked **Done (2026-10-19)** with a pointer
    to the state-split design.
  - §13 item 7 is marked **Decided and landed (2026-10-19)** with the field
    line (caches expendable; `state.json` keeps authoritative human state,
    identity/config, light progress; single writer stays `apply_review`).
  - A new dated revision paragraph, "Revision 2026-10-19, state split",
    records the landing and that losing `cache.json` costs a re-plan, never a
    human decision.
- `CHANGELOG.md`: `### Changed` bullet (the split, write order, reader
  behavior, what is unchanged) and an `### Upgrade notes` bullet (split on the
  first full save, transparent `ProjectState.load`/`save`, deleting the cache
  recomputes plan stages, a corrupt cache raises until deleted).
- `docs/ROADMAP.md`: dated local P0 note; remaining migration work listed as
  manifest-backing the chat-stage cache bodies and per-stage runners.
- Spec correction (`docs/superpowers/specs/2026-10-19-state-split-design.md`):
  the reader-table row for `_load_checkpoint_state` now documents the
  full-load-with-fallback behavior from Task 3, and the revision history
  carries the implementation note.

## Verification

- Full suite: `765 passed, 5 skipped`.
- `ruff check .` and `ruff format --check .`: clean (199 files).
- Diff for the commit is docs-only (CHANGELOG, ROADMAP, spec); the design doc
  edit stays local by policy.

## Commit

- `5a8382d` `docs(pipeline): record the two-part state.json contract`
