# Task 3 report: progress readers migrated

## Change

- `web/server.py`:
  - `list_projects` loads with `caches=False`: a corrupt `cache.json` can no
    longer hide a project whose checkpoint is intact.
  - `_load_timing_elapsed`'s legacy checkpoint fallback loads with
    `caches=False` (timing is a light field).
  - `_load_project_state` stays a full load, with a comment: merge/dismiss
    rewrites the heavy caches and its save must persist them.
  - `_load_checkpoint_state`: full load with a `caches=False` fallback when the
    full load raises `ValidationError`; a corrupt `state.json` still returns
    `None` as before.

## Deviation from the plan (deliberate)

The plan said `_load_checkpoint_state` should load with `caches=False`.
Implementation showed that would regress the resumed progress bar: the
estimate calls `panel_progress_counts` / `page_progress_counts`, which read
`chunk_cache` / `page_cache` for their *denominator* ("planned"). Without
those, `total_chunks` collapses to 1 and `planned` clamps to `finished`, so a
resumed 300-page book would show its finished count as the whole plan. The
implemented shape keeps the full load for the normal case and only falls back
to the projection-only read when the cache file is the unreadable one — the
"progress readers cannot be blocked by a corrupt cache" guarantee holds, and
the estimate stays honest otherwise. The spec's reader table and revision
history were corrected in Task 4; the plan document is left as written intent.

## Tests

`tests/test_web_server.py` gained:
- `test_seed_job_progress_survives_a_corrupt_cache_file` — corrupting
  `cache.json` still seeds `("resume", progress > 0.3)` through the fallback.
- `test_list_projects_survives_a_corrupt_cache_file` — the project stays
  listed with a corrupt cache file.

## Verification

- `tests/test_web_server.py`: 35 passed; combined with the state-split file:
  44 passed.
- Full suite (after this task, run before the docs commit):
  `765 passed, 5 skipped` (763 + 2 new web tests).
- `ruff check` / `ruff format --check` pass on both changed files.

## Commit

- `ad0f5cd` `fix(web): load progress readers without the cache file`
