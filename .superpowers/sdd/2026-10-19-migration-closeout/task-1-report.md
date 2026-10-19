# Task 1 report: idempotency probe executed and recorded

## Change

- `scripts/probe_image_idempotency.py` now loads `.env` with the same minimal
  loader `scripts/probe_m2.py` uses, so the documented "fill one line" setup
  works without exporting variables by hand.
- Executed the probe once (cost: exactly two image calls):
  - provider: `AgnesImageAPI` (`agnes-image-2.5-flash`), size 1024x1536;
  - first: `6e53513f7c7ebbd460443433598733a1d800712fc8ac841a7aadb2ea493b72c4`
    (1,441,715 bytes);
  - second: `0d5c7e7dc9c728a9a83ca3826b46cc8b619de1bbb5231d54effd0369da179a68`
    (1,336,638 bytes);
  - verdict: **different bytes** — §8's duplicate-charge window stays open;
    it remains "shrinkable, not closable", now as a measurement instead of an
    assumption.
- `CHANGELOG.md` gained a `### Notes` entry with the verdict.
- The design doc (§13) gained a dated revision answering residual
  uncertainty 2. That file lives under `docs/architecture/`, which is
  gitignored by policy, so the edit stays local and is not in any commit.

## Verification

- The probe printed the verdict above; no exceptions, no retries visible.
- `ruff check` and `ruff format --check` pass on the modified script.

## Commits

- `a2407ae` `chore(scripts): load .env in the idempotency probe`
- `9a476a9` `docs: record the idempotency probe verdict`
- (`a307a9c` carried the plan document for the whole close-out.)
