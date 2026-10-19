# Two-Part `state.json`: Plan-Cache Relocation Design

**Date:** 2026-10-19
**Status:** Draft for review
**Parent:** [Content-Addressed Execution Pipeline](../../architecture/2026-09-28-content-addressed-pipeline-design.md) §6 (storage layout), §8 invariant 6 (projection vs authoritative state), §10 (risk), §13 item 7 (`state.json` split)
**Follow-up to:** [Two-Phase Scheduler Foundation](2026-10-18-two-phase-scheduler-foundation-design.md) — that slice deferred plan-cache relocation explicitly ("Plan-side caches remain in `state.json` for this slice") and named moving `chunk_cache`, `page_cache`, `beat_cache`, or `visual_bible` out of `state.json` as out of scope.

## Goal

Decide and implement the two-part `state.json` contract. `state.json` stays the
small authoritative checkpoint — human decisions, identity/config records, and
light progress records — and the four heavy planning caches move to a sibling
`cache.json`. The per-page and per-panel saves in the render loop stop
rewriting the heavy payloads.

This is the data-model decision §13 item 7 says "must be decided before Phase 0".
It is file-level: the `ProjectState` model, every action key, every fingerprint,
the manifest schema, and the output layout are unchanged.

## Today

- One `state.json` carries everything: authoritative human state, progress
  records, and the four heavy chat-model caches (`chunk_cache` page-era extract
  and storyboard, `page_cache` finished-page plans, `beat_cache` key beats,
  `visual_bible`).
- The pipeline saves 26 times per run. Nine of those saves run once per page or
  per panel in the render loop; each rewrites the whole document. Local
  `state.json` files already reach 300–580 KB, and §8 calls this out as the
  write-cost and UI-progress-freeze problem for 300-page books: "move the heavy
  format caches (`chunk_cache`, `page_cache`, `beat_cache`, `visual_bible`) out
  of the hot path so what remains is small enough to write often."
- The four caches are not reconstructible from `index/` + `cas/` today: only
  artifact stages (render.page, letter, layout, export) write manifests. The
  chat-stage caches are running state that costs billable chat calls to
  recompute. They are caches, not derived-projection material in the invariant-6
  sense; §6's "recomputable from manifests plus the log" applies to progress
  records (`pages_done`, `generated.*`, `chunks_done`, cache key sets) and to
  the already-manifest-backed artifact bytes.
- Readers of `state.json`: the pipeline itself, `web/server.py` (checkpoint
  loading, progress seeding, project listing, merge/dismiss and regen actions),
  `core/cli.py` (`status`, `rebuild`, `gate`, `identity`, planned counts), and
  batch tools (`gc`, `prune`, `merge_cost_probe`, `structure_report`,
  `export_page_pdf`, `run_until_complete`). All of them go through
  `ProjectState.load` / `ProjectState.save`.

## The contract

### Field classes

| Class | Fields | File |
|---|---|---|
| Authoritative human state (single writer: the merge/dismiss review actions) | `needs_review`, `dismissed_aliases`, `merged_aliases` | `state.json` |
| Identity / config records | `project_id`, `source_file`, `source_fingerprint`, `structure_fingerprint`, `render_fingerprint`, `density`, `panels_per_chunk`, `render_mode`, `model_snapshot`, `created_at`, `errors`, `active_elapsed_seconds` | `state.json` |
| Light progress records (small; rebuilt by a re-run) | `stage`, `source_units`, `adaptation_plans`, `characters`, `settings`, `chunks_done`, `panels_done`, `stale_panels`, `skipped`, `skipped_chunks`, `pages_done`, `stale_pages`, `skipped_pages`, `tombstones`, `generated`, `bible_action_keys` | `state.json` |
| Heavy planning caches | `chunk_cache`, `page_cache`, `beat_cache`, `visual_bible` | `cache.json` |

`tombstones` stay in `state.json`: they are small and §8 requires them durable
at page granularity. `visual_bible` moves with the other caches: relative to
§6/§8's split it is the same class — a billable-to-rebuild planning output, not
a human decision and not a progress record.

### Files

`cache.json` is the sibling of `state.json` under the project directory and
holds exactly the four heavy fields. Both files use the same atomic write
(`mkstemp` + `fsync` + `os.replace`) and `indent=2` JSON.

### Write rules

- `ProjectState.save(path)` — full save: writes `cache.json` first, then
  `state.json` (heavy fields excluded from the latter).
- `ProjectState.save(path, caches=False)` — projection-only save: writes
  `state.json` only and never touches `cache.json`.
- Order rationale: a crash between the two writes leaves the cache *ahead* of
  the checkpoint, never behind. Cache-ahead loses nothing — the next run finds
  completed stage caches for work the checkpoint has not recorded yet and
  reuses them. Cache-behind would re-run stages the checkpoint says are done.
- Light saves are allowed only where no heavy field has changed since the last
  full save. In the pipeline that is exactly the render loop:
  - `_render_page_node` per-page saves: L2418 (letter reuse), L2511 (letter
    write), L2557 (content-policy tombstone);
  - the per-chunk page-pass saves at L2658 and L2672;
  - the continuity-panel saves at L2897 and L2902;
  - the panel-batch saves at L2957 and L2963.
  Every other save site — 17 of the 26 — stays full: run preflight, sanitize,
  extract, portraits, beats, page plan, storyboard, page-script, and the final
  save at L3052 all either mutate heavy caches or run at plan frequency.
- One existing heavy mutation has no immediate save: the storyboard action-key
  stamp (`cached_chunk.storyboard_action_key = storyboard_key`, ~L2694). That
  branch gains an explicit full save so "heavy mutation ⇒ full save" holds
  without exception.

### Read rules

- `ProjectState.load(path)` — reads `state.json`, then overlays `cache.json`
  when it exists. When `cache.json` does not exist, inline heavy fields in
  `state.json` (pre-split files) are kept as-is. When both are present, the
  `cache.json` values win: the only way that state arises is a crash between
  the two writes of a full save, and the cache holds the newer values.
- `ProjectState.load(path, caches=False)` — reads `state.json` only; never
  opens `cache.json`. A corrupt cache file cannot hide an intact checkpoint
  from progress readers.
- A corrupt `state.json` raises (unchanged). A corrupt `cache.json` raises for
  the default load: silently starting from empty caches would re-plan the book
  and can cascade into repaints (the bible drives the render fingerprint), so
  the default is to refuse rather than burn quota. Recovery is explicit and
  documented: delete `cache.json` to recompute. `caches=False` loads are
  unaffected by cache corruption.

### Reader migration

| Reader | After |
|---|---|
| pipeline `creative_comic` | full load; full saves except the nine render hot sites |
| web `_load_project_state` (merge/dismiss, regen) | full load — unchanged; merge rewrites caches and its save must persist them |
| web `_load_checkpoint_state` (progress seeding, pause snapshots) | `caches=False` |
| web `_load_timing_elapsed` legacy fallback | `caches=False` |
| web `list_projects` | `caches=False` |
| CLI `status` / `rebuild` / `gate` / `identity` / planned counts | full — unchanged (`_planned_counts` reads `page_cache`) |
| `gc` / `prune` / `merge_cost_probe` / `structure_report` / `export_page_pdf` / `run_until_complete` | full — unchanged |

### Migration of existing projects

A pre-split `state.json` loads through the inline fallback. The first full save
writes `cache.json` and stops emitting the four fields into `state.json`. No
batch command, no version field, no marker: the two-part contract is the steady
state from the first save on, and both old and new files load through the same
code path.

A missing `cache.json` (deleted by hand, or never written) is not an error:
the pipeline starts with empty caches for the four fields and recomputes the
plan stages under their action keys as needed. Already-rendered pages still
restore from their manifests when the recomputed plan keys match.

## What does not change

- No action key, fingerprint input, manifest schema, output path, or
  `state.json` field schema changes. `cache.json` enters no key and no
  fingerprint (§8 invariant 3).
- Progress labels, budget, gate, quota, and ledger files are untouched.
- `ProjectState` keeps every field; `model_dump()` and `model_validate()`
  behave exactly as before. The split lives in the file I/O layer only.
- The two-phase scheduler's node graph and boundary writes are untouched; it
  saves through `state.save` exactly as today, and each `_state.save(state_path)`
  inside a node handler keeps its existing meaning.

## Failure handling

| Failure | Behavior |
|---|---|
| Crash during a file write | temp file removed; both files individually valid |
| Crash between the two writes of a full save | cache ahead of checkpoint; safe on resume |
| `cache.json` missing | empty heavy caches; plan stages recompute |
| `cache.json` corrupt | default load raises; progress readers (`caches=False`) unaffected |
| `state.json` missing | project treated as new (unchanged) |

## Acceptance criteria

1. Round trip: a state with all four caches populated saves and loads
   unchanged; `state.json` contains none of the four keys; `cache.json`
   contains exactly them.
2. Migration: a pre-split `state.json` with inline heavy fields loads with
   those fields intact; the next full save splits them.
3. Projection-only save: `save(path, caches=False)` leaves `cache.json`
   byte-identical and still persists every `state.json` field.
4. Precedence: when both files carry heavy fields, `cache.json` wins.
5. Corruption: a corrupt `cache.json` raises on the default load and is
   ignored by `caches=False`; a corrupt `state.json` still raises for both.
6. Hot path: after a pipeline run, `state.json` holds no heavy fields and
   `cache.json` holds the four; a render-step save does not rewrite the cache
   file.
7. No key or fingerprint change: `test_stage_contract` and the fingerprint
   tests stay green; the full suite and ruff pass.

## Out of scope

- Making the chat-stage bodies CAS/manifest-backed (the rest of invariant 6's
  "recomputable from manifests plus the log").
- Lazy or partial reads for the pipeline itself; a read API that returns the
  projection without merging caches is limited to `caches=False` for now.
- Per-stage runners, and any `state.json` schema versioning beyond the
  presence/absence of the four keys.

## Risks

- **Cache loss cascades.** Losing `cache.json` re-plans the book; if a re-run
  produces different beats, page-plan keys shift and affected pages repaint.
  Bounded by the budget and gate machinery, and never touches human decisions.
  Called out in the CHANGELOG Upgrade notes.
- **Misclassified light save.** A save site that mutates a heavy field and then
  saves projection-only would lose that mutation on a crash. Bounded to a
  re-plan (never human state), mitigated by the explicit site list above and by
  keeping every non-render site full so mistakes degrade to today's behavior.
- **Corrupt cache blocks the project.** The default load raises, and readers
  that catch broadly (web listing) already skip unreadable projects. Accepted
  trade to avoid a silent full re-plan; the recovery line is in the Upgrade
  notes.

## Revision history

- 2026-10-19: initial design; settles §13 item 7's two-part contract and the
  scheduler slice's deferred plan-cache relocation.
