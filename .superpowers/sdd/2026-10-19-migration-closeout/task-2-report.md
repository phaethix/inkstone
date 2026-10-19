# Task 2 report: one-time invalidation announced

## Change

`CHANGELOG.md` `[Unreleased]` gained an `### Upgrade notes` section at the top,
covering everything resolved item 21 and item 27 require of the announcement:

- one recompute per existing project under the new keys; recorded human
  decisions (review queue, dismissed aliases, merged aliases) survive;
- batch framing ("batch job, not an incident"), not "everything voided at
  once";
- a checkable expectation from 1 RPM and page count: roughly one minute per
  page, so a 300-page book is on the order of five hours of wall clock;
- `--budget N` pauses when spent, `--carry-over` re-arms the same budget on a
  later calendar day, so the recompute settles over several days;
- the mandated framing: progress is counted in produced pages, not
  percentage.

The release notes at release time should quote this section; no separate
release-notes file exists in the repository.

## Verification

- Rendered markdown reviewed at the top of `CHANGELOG.md`; wording kept
  consistent with the `### Changed` entries it announces.

## Commit

- `f87027f` `docs(changelog): announce the one-time key invalidation`
