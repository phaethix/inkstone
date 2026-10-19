# Task 1 report: state serialization split

## Change

- `core/schemas.py` gained `CACHE_FILE_NAME = "cache.json"`, the module-level
  `_CACHE_FIELDS` tuple (`chunk_cache`, `page_cache`, `beat_cache`,
  `visual_bible`), a shared `_atomic_write_text` (the old `save` body), and a
  `ProjectCaches` model for the cache file's own (de)serialization.
- `ProjectState.save(path, *, caches=True)` writes `cache.json` from
  `ProjectCaches` first and then `state.json` with the four fields excluded.
  `caches=False` writes `state.json` alone and never touches the cache file.
- `ProjectState.load(path, *, caches=True)` reads `state.json`, then overlays
  the sibling `cache.json` when it exists (cache wins over inline heavy fields;
  that state only arises from a crash between the two writes). `caches=False`
  never opens the cache file. A corrupt `cache.json` raises on the default
  load; a missing one is normal (pre-split or deleted).
- `ProjectState` itself is unchanged: every field stays on the model and
  `model_dump()` still contains all four caches.

## Tests

New `tests/test_state_split.py` (9 tests): on-disk split + round trip;
`model_dump()` still carries the caches; write order (cache file before the
checkpoint, via a `_atomic_write_text` spy); pre-split inline migration and
split-on-save; projection-only save leaves `cache.json` byte-identical while
persisting `state.json`; cache-wins precedence; missing cache file loads with
empty caches; corrupt cache raises by default and is ignored with
`caches=False`; corrupt `state.json` still raises for both modes.

## Verification

- `tests/test_state_split.py`: 9 passed.
- Focused regression set (fingerprint split, phase 0b migration, finished-page
  pipeline, gc, cli rebuild, structure report, tombstones, run_until_complete):
  86 passed.
- Full suite: `763 passed, 5 skipped` (754/5 baseline + 9 new tests).
- `ruff check` / `ruff format --check` pass on both changed files.

## Commit

- `094277f` `feat(state): split state.json into checkpoint and cache files`
