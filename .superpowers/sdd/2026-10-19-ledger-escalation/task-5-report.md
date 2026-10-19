# Task 5 report: records

## Change

- Escalation spec: status → `Implemented (2026-10-19)`; revision history gains
  the implementation note (including the boundary-time `rebuild_from_state`
  decision).
- 0d ledger spec: the "the escalation policy is 5b" sentence gains the landed
  reference (threshold, boundary pause, confirmation) and links the escalation
  design.
- Design doc (`docs/architecture/2026-09-28-content-addressed-pipeline-design.md`,
  gitignored, local-only): a new dated revision paragraph, "Revision 2026-10-19,
  escalation", records the landing — threshold 30, the plan→render boundary
  hold with the deduplicated stop record, the three confirmation surfaces, the
  quiet panel below threshold, and that the ledger/threshold enter no key or
  fingerprint.
- `docs/ROADMAP.md`: dated local P0 note at the top of the migration section;
  the 5b checkbox gains the follow-up sentence about the landed escalation.
- `CHANGELOG.md`: `### Added` bullet naming the threshold, the boundary hold,
  the confirmation surfaces, and the panel.

## Verification

- Diff for the commit is docs-only (CHANGELOG, ROADMAP, two tracked specs); the
  design-doc edit stays local by policy.
- Full suite and ruff were last run at Task 4 (`788 passed, 5 skipped`; ruff
  clean); no code changed after.

## Commit

- `caf312a` `docs(ledger): record the escalation policy`
