# Task 4 report: migration record refreshed

## Change

- Design doc §9 gained a `Revision 2026-10-19` paragraph: the scheduler
  landed, `_PIPELINE_STATE_VERSION` and the legacy combined fingerprint are
  deleted, the one-time invalidation is announced, the probe verdict is
  recorded, and plan-cache relocation and per-stage runners remain later
  work. Existing decisions and revision text were not rewritten. (This file
  is under the gitignored `docs/architecture/`, so the edit is local only.)
- `docs/ROADMAP.md` gained the matching local note at the top of the P0
  migration section, superseding the previous notes' "still exists" status
  statements without rewriting them.

## Verification

- Cross-checked the new revision against the §13 revision added in Task 1
  and the CHANGELOG entries; the three records agree on the verdict and the
  remaining work.

## Commit

- `a2e13a8` `docs(pipeline): record the migration close-out`
