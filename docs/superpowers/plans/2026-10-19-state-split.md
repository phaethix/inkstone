# Two-Part `state.json` (Plan-Cache Relocation) Implementation Plan

> **For agentic workers:** Execute the tasks in order on `main`, one commit per
> task. This implements
> [the state-split design](../specs/2026-10-19-state-split-design.md): move the
> four heavy planning caches out of `state.json` into a sibling `cache.json`,
> take the per-page/per-panel render saves off the heavy payload, and migrate
> the progress readers deliberately.

**Goal:** Make `state.json` small enough to write on the render hot path. After
this plan, a full save writes `cache.json` (`chunk_cache`, `page_cache`,
`beat_cache`, `visual_bible`) first and `state.json` second; a projection-only
save writes `state.json` alone.

**Architecture:** File-level split only. `ProjectState` keeps every field and
`model_dump()` is unchanged; a small `ProjectCaches` model gives `cache.json`
its own (de)serialization. No action key, fingerprint, manifest, output path,
field schema, progress label, quota, gate, or ledger change.

**Tech Stack:** pydantic v2, pytest, ruff.

## Global Constraints

- Work on `main`; one commit per task; keep plan-task checkboxes unchecked.
- `save(path)` keeps full semantics (both files); `save(path, caches=False)`
  is the new projection-only form. Light saves are allowed only where no heavy
  field changed since the last full save.
- A full save writes `cache.json` first, `state.json` second (cache-ahead on
  crash is safe; cache-behind re-runs completed stages).
- Default `load(path)` merges `cache.json`; a corrupt cache file raises (never
  silently re-plan). `load(path, caches=False)` reads the checkpoint only, so
  progress readers cannot be blocked by cache corruption.
- Full suite (`uv run pytest -o addopts="" -q`) plus `ruff check .` and
  `ruff format --check .` are the gate for every code task.

## Task 1: Split the serialization (schemas)

**Files:**
- Modify: `core/schemas.py`
- Create: `tests/test_state_split.py`

- [ ] **Step 1: Inventory and name the heavy fields.** Add a module-level
  `CACHE_FILE_NAME = "cache.json"` and a `ProjectCaches` model with exactly
  `chunk_cache`, `page_cache`, `beat_cache`, `visual_bible` (same types as on
  `ProjectState`).
- [ ] **Step 2: Factor the atomic writer.** Extract the existing
  `mkstemp` + `fsync` + `os.replace` body of `ProjectState.save` into
  `_atomic_write_text(path: Path, payload: str) -> None`.
- [ ] **Step 3: Split `save`.** `save(path, *, caches: bool = True)`:
  - `caches=True`: write `cache.json` (from `ProjectCaches`) first, then
    `state.json` via `model_dump_json(indent=2, exclude=<the four fields>)`.
  - `caches=False`: write `state.json` only; never touch `cache.json`.
- [ ] **Step 4: Split `load`.** `load(path, *, caches: bool = True)`:
  - always read `state.json` (raise on corruption, as today);
  - when `caches=True` and the sibling `cache.json` exists, overlay the four
    fields from it (cache wins over inline heavy fields); a corrupt
    `cache.json` raises;
  - when `caches=False`, never open `cache.json`.
- [ ] **Step 5: Tests in `tests/test_state_split.py`** (design AC 1–5):
  round trip; pre-split inline migration; projection-only save leaves
  `cache.json` byte-identical while persisting every `state.json` field;
  `cache.json` precedence when both files carry heavy fields; corrupt
  `cache.json` raises by default and is ignored with `caches=False`; corrupt
  `state.json` still raises for both.
- [ ] **Step 6: Run focused tests plus the files that touch heavy fields**
  (e.g. `tests/test_fingerprint_split.py`, `tests/test_finished_page_pipeline.py`,
  `tests/test_phase0b_legacy_migration.py`) and ruff.
- [ ] **Step 7: Commit** as `feat(state): split state.json into checkpoint and cache files`.

## Task 2: Take the render hot path off the heavy file

**Files:**
- Modify: `core/pipelines/creative_comic.py`
- Modify: one existing pipeline test that runs the fake-provider pipeline
  (e.g. `tests/test_finished_page_pipeline.py`) — assert the split on disk
- Modify: `tests/test_state_split.py` if a unit-level guard is clearer

- [ ] **Step 1: Verify each hot site by reading its block.** The nine
  projection-only saves: `_render_page_node` L2418 (letter reuse), L2511
  (letter write), L2557 (content-policy tombstone); the per-chunk page-pass
  saves L2658 and L2672; the continuity-panel saves L2897 and L2902; the
  panel-batch saves L2957 and L2963. If any site turns out to mutate a heavy
  field, leave it full and record the deviation.
- [ ] **Step 2: Switch the nine sites to `save(state_path, caches=False)`**
  (`_state.save(state_path, caches=False)` inside the handlers). Leave the
  other 17 sites full.
- [ ] **Step 3: Give the storyboard action-key stamp an explicit full save.**
  In the `elif not cached_chunk.storyboard_action_key` branch (~L2694), save
  once after stamping so "heavy mutation ⇒ full save" holds without exception.
- [ ] **Step 4: Assert the split after a real run.** In the chosen pipeline
  test, after the run: `state.json` text contains none of the four keys and
  `cache.json` parses with all four; the run's recorded page/panel progress is
  intact after loading the state.
- [ ] **Step 5: Run the pipeline test files and ruff; then the full suite.**
- [ ] **Step 6: Commit** as `perf(pipeline): keep render saves off the heavy cache file`.

## Task 3: Migrate the progress readers

**Files:**
- Modify: `web/server.py`
- Modify: `tests/test_web_server.py`

- [ ] **Step 1: `_load_checkpoint_state` loads with `caches=False`**
  (progress seeding, pause snapshots — none of them read heavy fields).
- [ ] **Step 2: `list_projects` loads with `caches=False`** so a corrupt or
  unreadable `cache.json` cannot hide a project with an intact checkpoint.
- [ ] **Step 3: `_load_timing_elapsed`'s legacy fallback loads with
  `caches=False`.**
- [ ] **Step 4: Leave `_load_project_state` full** (merge/dismiss rewrites
  caches; its save must persist them) and note why in a comment.
- [ ] **Step 5: Test** in `tests/test_web_server.py`: with a corrupt
  `cache.json`, `list_projects` still lists the project and resume seeding
  still returns progress; merge/dismiss still round-trips its cache rewrite
  through the full load.
- [ ] **Step 6: Run the web tests and ruff.**
- [ ] **Step 7: Commit** as `fix(web): load progress readers without the cache file`.

## Task 4: Record the contract

**Files:**
- Modify: design doc §6 (storage tree gains `cache.json`) and §13 item 7
  (decided, dated) — local-only file
- Modify: `CHANGELOG.md` (`### Changed` bullet + `### Upgrade notes` bullet)
- Modify: `docs/ROADMAP.md` (dated local note in the P0 section)

- [ ] **Step 1: Design doc.** Add `cache.json` to the §6 tree with a one-line
  comment; append a dated revision note recording the two-part contract and
  that the heavy caches are still re-plan-recomputable, not yet
  manifest-backed.
- [ ] **Step 2: CHANGELOG.** `### Changed`: `state.json` no longer carries the
  four heavy planning caches; they live in the sibling `cache.json`; render
  saves write the checkpoint only. `### Upgrade notes`: readers using
  `ProjectState.load`/`save` are unaffected; deleting `cache.json` recomputes
  plan stages; a corrupt `cache.json` raises until deleted.
- [ ] **Step 3: ROADMAP.** Dated local P0 note: two-part contract landed;
  remaining migration work is per-stage runners and manifest-backing the
  chat-stage bodies.
- [ ] **Step 4: Given the previous slice's lesson, skim the CHANGELOG section
  for duplicated wording between `### Changed` and `### Upgrade notes` and
  keep the announcement fact-shaped.**
- [ ] **Step 5: Commit** as `docs(pipeline): record the two-part state.json contract`.

## Task 5: Task reports

**Files:**
- Create: `.superpowers/sdd/2026-10-19-state-split/task-{1..4}-*.md`
  (gitignored; commit with `git add -f` as before)

- [ ] **Step 1: Write the four task reports** (what landed, files, evidence:
  commands and outputs).
- [ ] **Step 2: Commit** as `docs(sdd): add state-split task reports`.

## Acceptance criteria

1. Design ACs 1–7 hold: round trip, migration, projection-only isolation,
   precedence, corruption semantics, post-run on-disk split, no key or
   fingerprint change.
2. Full suite green; ruff lint and format clean.
3. The render loop's per-page/per-panel saves never rewrite `cache.json`;
   a full run leaves `state.json` without the four heavy keys.
4. No change to action keys, fingerprints, manifests, output paths, the
   `state.json` field schema, progress labels, quota, gate, or ledger files.

## Out of scope

- Manifest-backing the chat-stage bodies (the rest of invariant 6's rebuild
  path), per-stage runners, and `state.json` schema versioning.
- Lazy pipeline reads beyond the `caches=False` switch.
