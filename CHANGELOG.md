# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Upgrade notes — prompt-harness identity
- **A prompt edit now invalidates the chat stage it belongs to.** Every chat
  stage's action key carries a digest of the request's stable identity: the
  system prompt, stage instructions, tool schema, model, and the stage's declared
  inputs. Editing `core/harness/data/common/system.md`, a tool schema, or a
  prompt builder now misses the old key instead of silently reusing the stored
  plan. Request digests use schema 3; render/letter/export keys stay on schema 2
  and do not move.
- **Accumulated context is pinned out of the digest.** The bible key still
  excludes the character table and bible prose; the page-plan key still excludes
  the recent-layout strings and the bible body. Those are accumulated context,
  and folding them in would cascade one edit through every later chunk. A genre
  or project-style change therefore does not re-reconcile the whole book.
- **The first run after upgrading re-plans each chat stage once.** Pages repaint
  only when the new plan's bytes differ from the stored plan, because the render
  key does not include the upstream plan key. Run `inkstone migrate --check` to
  see what a re-run would redo before spending anything: it reports billable
  chat calls, local-only redos, and how many pages stay reusable. Use `--budget`
  on a long book, and delete `cache.json` only if you intend to re-plan every
  stage.

### Added
- `inkstone verify` also reports the finished-page projection. "This page is
  done" is stored as `pages_done`, as `generated.pages`, and as a `render.page`
  manifest; the first two live in one document and are written together, so a
  disagreement between them is provable without re-deriving a key. The check
  fails on a page that is done without an artifact (or the reverse), a duplicate
  done entry, or a page that is both done and tombstoned/stale. A project that
  predates the projection reports `absent` instead of failing: `record_page_artifact`
  writes the artifact before the done entry in one document, so "every done page
  has no artifact" cannot come from a crash between two writes. The manifest is
  still not compared — it records inputs and outputs but not the page key, and
  re-deriving that key is the second derivation path the design forbids.
- Migration cost forecast: `inkstone migrate --check` reports what a re-run
  would redo without calling any provider. It compares each manifest's recorded
  `stage_src` against the hash current code computes, so a prompt or stage-code
  edit shows up as a provable miss rather than a guess. Billable chat calls,
  local-only redos, and reusable versus repaintable pages are reported
  separately, because a chat re-plan does not force a repaint unless the plan
  bytes change. `--json` gives automation the same numbers.
- Declared read edges: `core/pipelines/stage_registry.py` now names which stage
  produces each stage's hard inputs, and the pipeline rejects a registry whose
  edges are missing, name an unknown producer, or point at a later phase. This
  makes §5's declared-dependency rule machine-checkable instead of prose.
- Manifest stage validation: `verify` and `migrate` now reject a manifest
  whose `stage` is outside the declarative stage catalog. `migrate --json`
  gives automation a machine-readable upgrade report.
- Manifest migration: `inkstone migrate --out <dir>` reports and
  `--apply` rewrites legacy/incomplete `index/*.json` manifests to the
  current schema. Malformed files and key/filename mismatches are reported
  and never rewritten.
- Stage catalog: `core/pipelines/stage_registry.py` declares every stage,
  its scheduler phase, contract, and manifest stage. `inkstone stages`
  prints it, and `creative_comic` validates the catalog before the first
  billable call.
- Content-addressed planning: beats, page_plan, storyboard, and page_script
  now write `ok` manifests beside their caches. A lost `cache.json` restores
  them from CAS/manifests instead of re-paying the chat call, so those stages
  join extract/bible/render/letter/export in the manifest-hit rule.
- L2 references: finished-page rendering now consults the ledger's
  authoritative reference path before the character's cached portrait path,
  matching the priority table in the content-addressed design.
- Web theme toggle: a persisted light/dark switch in the nav (localStorage,
  applied before first paint). The light theme adjusts surfaces, borders,
  badges, buttons, inputs, and the tab slider; dark remains the default.
- Web A/B diff markers: the compact comparison now marks corresponding
  pages with ◆ inside each candidate plan, so the summary and the page list
  agree.
- Web visual pass: animated hero gradient, focus-visible rings, custom
  scrollbars, softer card depth, hover feedback on experiment rows/cards,
  checked-page chips, a shimmering progress bar, a responsive tab bar on
  mobile, clickable/keyboard-accessible result panels, and left/right
  keyboard navigation for the showcase carousel. Reduced-motion users get
  the static layout.
- Web A/B and sequence views: candidate cards now carry a compact
  page-level diff (page count plus the first differing layout intents), and
  a sequence shows a round timeline (done / picked / awaiting pick) above
  the current round.
- Web continuity review: each pending character now expands into page chips
  with checkboxes, Select all/Clear, Confirm selected, and Confirm all.
  `POST /api/project/<id>/ledger/confirm` accepts an optional `pages` list,
  so the Web UI can confirm a subset without dropping the rest of the
  pending set.
- Web A/B and sequence actions report failures through a temporary toast
  instead of a blocking `alert`; the continuity panel uses the same visual
  language.
- One-release harness fallback: `INKSTONE_HARNESS=0` forces the builtin
  tree, ignores `INKSTONE_HARNESS_DIR` / `--harness-dir`, forces the `general`
  genre, and disables craft-lesson injection. `review/manifest.json` records
  `harness_fallback: true`. The image renderer and export keys are unchanged.
  Intended for comparing behavior while the harness data layer settles.
- Sequential recursion in the A/B tab: `/api/sequences` lists sequence
  directories, `/api/sequence/<id>` returns the current round plus its
  candidate plans/structure reports, and `/api/sequence/<id>/pick|advance`
  reuse the CLI selection and compose writers. The SPA lists sequences above
  standalone experiments, keeps labels hidden before a pick, and advances to
  the next round from the same page.
- Sequential recursion: `inkstone experiment sequence plan --book ...
  --candidate <dir> --rounds N --out <dir>` creates round 1 with the current
  checkpoint (`base` or `--base`) plus the candidate directories. After a
  normal `experiment pick`, `experiment sequence advance --run <dir>` composes
  the pick into `rounds/<n>/checkpoint` and plans the next round from that
  checkpoint plus the same candidates. `sequence status` reports progress;
  the final advance marks the sequence `done`. Each round is a normal
  experiment packet, so the CLI, review files, and selection format are
  unchanged.
- Project-level harness override: `inkstone generate --harness-dir <tree>`
  or `INKSTONE_HARNESS_DIR` runs all six chat stages with a validated tree
  (extract, beats, bible, storyboard, page_plan, page_script). The tree is
  checked before the first billable call, its digest is recorded in
  `review/manifest.json`, and page-plan keys use its request digest. The
  image renderer and export keys are unchanged. `inkstone prompt
  --harness-dir` now applies to every stage, not only page_plan.
- Candidate-level A/B planning: by default each candidate now runs
  `extract`, `key_beats`, and `page_plan` under its own harness, so the probe
  compares the whole planning path. `--shared-evidence` keeps the cheaper
  page_plan-only probe (4 chat calls for base + one candidate instead of 6).
- `inkstone experiment compose --run <dir> --choice A --out <harness-dir>`
  materialises the picked candidate; `--merge-from B --component
  stage.page_plan` takes named components from another candidate. The tree is
  copied to staging, merged explicitly, validated with `Harness.validate()`,
  and only then moved into place. It writes `compose.json` + `ADOPTION.md`,
  marks the experiment `composed`, and never overwrites the builtin tree or
  an existing output directory.
- Web A/B card: the local UI lists experiments under
  `INKSTONE_EXPERIMENTS_DIR` (default `comic_out/experiments`), shows both
  anonymised plans plus their structure reports, keeps candidate labels hidden,
  and records a pick with a required `why` through
  `POST /api/experiment/<id>/pick`. The same `selection.json` writer is used by
  the CLI. After a pick, the card also composes the chosen tree into
  `<experiment>/adopted/` through `POST /api/experiment/<id>/compose`, with an
  optional `stage.page_plan` merge from the other candidate; the output path is
  fixed by the server, never chosen by the browser. `GET /api/experiments` and
  `GET /api/experiment/<id>` back the page.
- Planning-only blind A/B: `inkstone experiment plan --book ... --candidate base
  --candidate <harness-dir> ...` runs extract + beats once, then only the
  page-plan request under each candidate (no image calls), anonymises the
  candidates A/B/C, and writes `experiment.json`, per-candidate `prompt.json` /
  `plan.json` / `structure_report.json`. `inkstone experiment pick --run ...
  --choice A --why "..."` records the human choice in `selection.json` and
  reveals the label. The MVP probes page_plan only; extract/beats stay on the
  builtin harness, and it does not compose a harness automatically.
- External harness loading: `inkstone harness validate --dir <tree>`,
  `inkstone harness list --dir <tree>`, and
  `inkstone prompt --stage page_plan --harness-dir <tree>`. A tree is the same
  layout as `core/harness/data/`; it is validated before its first use.
- Lesson library MVP: `inkstone lesson add|list|retire` stores one
  human-approved craft note per file under
  `<INKSTONE_DATA_DIR>/library/<genre>/`, with a `cue`, `lesson`, evidence
  (run plus page/decision), confidence, and genre. `add` refuses a missing
  evidence run, a page decision without a page, a page that is not stale for a
  redraw, and any measurement marker; `--no-entry` records nothing. Page
  planning retrieves up to three active notes by genre plus cue-keyword overlap
  and injects them through the new `{{craft_notes}}` slot; the notes are pinned
  out of the page-plan key request so adding a lesson does not invalidate a
  book that was already planned. Disable injection with `INKSTONE_LESSONS=0`.
- The local `review/` packet now records the plan output under
  `plans/<stage>.c<chunk>.json` and a `pages.json` index, so a lesson can cite
  the page and the plan a human judged.
- `inkstone prompt --stage <extract|beats|bible|storyboard|page_plan|page_script>`
  prints the exact request a stage would send, including its digest; `--json`
  emits the full payload. This is the first way to review an effective prompt
  without spending a provider call.
- `inkstone harness validate|list` and the editable harness tree under
  `core/harness/data/`: the invariant system prompt now lives in
  `common/system.md`, the six `stages/*.md` user-message templates, and
  `genres/general.md` / `genres/wuxia.md` form the first domain pack.
  `render_stage()` substitutes named slots and fails on an unknown or missing
  slot. `INKSTONE_GENRE=wuxia` adds the domain notes to page planning and
  storyboarding; the default `general` adds nothing.
- A local `review/` evidence packet per run: `manifest.json`, `source.txt`,
  `prompts/<stage>.c<chunk>.json`, `structure_report.json`, and
  `decisions.jsonl`. It is written for humans and never enters a model prompt;
  disable it with `INKSTONE_REVIEW_PACKET=0`.
- A quality-verdict tripwire rejects a system prompt or tool schema carrying
  `structure_report`, `acceptance rate`, `quality score`, `redraw rate`, or
  `gate.json`. Source text is deliberately not scanned: a novel may legitimately
  contain those phrases.

### Changed
- `creative_comic`'s run inputs are now one `ComicRunOptions` dataclass instead
  of the same eighteen keyword parameters written into both the public function
  and its private body. The public keyword signature is unchanged, so every
  caller (CLI, Web, supervisor, probe, examples, tests) is untouched.
  `_creative_comic` takes one argument and unpacks it into the local names the
  body already used, so no stage code changed. The two functions that enter a
  `stage_source_hash` — `_portrait_key_material` and `_panel_key_material` —
  stayed in their module on purpose: `stage_source_hash` hashes
  `module.qualname` plus the source text, so relocating them would change the
  portrait and panel keys and repaint stored work.
- The export, layout, webtoon and CBZ stages moved out of
  `core/pipelines/creative_comic.py` into `core/pipelines/export_stages.py`
  (`creative_comic.py` 3,801 → 3,556 lines). The four stage-source constants
  moved with them and are re-exported from the pipeline module, because
  `core.comic.migrate._STAGE_SRC_CONSTANTS` reads them there by name; all twelve
  stage sources still resolve and no key changed. Tests that patched
  `ExportEngine` / `LayoutEngine` through the pipeline module now patch the class
  where it is defined (`core.comic.export` / `core.comic.layout`), which is the
  same object and no longer depends on which module happened to import it.

### Fixed
- **A newer `state.json` is no longer silently downgraded.** `state.json` now
  carries `schema_version` (currently 2). Files written before the field existed
  load as v1 and are stamped on the next save. A field this build does not know
  is logged at load and merged back at save instead of being deleted by the full
  dump that `extra="ignore"` used to cause — the failure mode was: open a newer
  project once with older code, and the next save removes the field for good. A
  file whose version is ahead of this build keeps the higher stamp, so a
  downgrade stays visible. Scope is top-level keys; a field nested inside a
  sub-model is still lost, which is what the version number is for.
- **UI writes no longer race a running pipeline.** `apply_review`,
  `apply_ledger_confirm`, and the state mutation inside `start_regen_job` take
  the same `<out>/.inkstone.lock` the pipeline holds. Previously they loaded an
  older checkpoint and saved the whole document, so a merge made while a job was
  running could be overwritten by that job's next save. A held lock answers 409
  instead of 500. The regen path releases the lock before starting the job,
  because the job acquires it for its own run.
- `migrate --check` no longer drops a manifest stage it cannot place. A
  registry stage missing from the forecast table was skipped entirely, and a
  known stage with no source hash fell through to "reusable"; both made the cost
  report cheaper for exactly the stage a developer had just changed. Unknown
  stages are listed in `unattributed` (CLI text and `--json`), and an
  unprovable stage counts as a miss.
- The four Web UI knobs (`INKSTONE_UI_HOST`, `INKSTONE_UI_PORT`,
  `INKSTONE_UI_MAX_BODY_BYTES`, `INKSTONE_JOB_TTL_SECONDS`) are read through
  `core.config`, so they join `ALL_ENV_VARS` and the `h_env` partition test. They
  are excluded from `h_env`: none of them can change a drawn byte.
- `core.comic.merge_cost_probe` imports `creative_comic` at call time. The
  module-level edge was a real cycle (`creative_comic` imports `core.comic.*`,
  and `stage_contract` imports a pipeline helper back), so `import core.comic`
  alone dragged in the whole pipeline.
- D-16: `h_env` is now scope-aware. Chat keys already used the request
  digest; the remaining letter/render/layout/webtoon/export scopes no longer
  share chat/image/font fields they do not read. Changing the chat model no
  longer invalidates image, letter, or export keys; changing the image model
  no longer invalidates letter/export keys; changing the font no longer
  invalidates render/export keys. This is a one-time recompute for the
  affected letter/render/export/layout keys, not for chat keys.
- D-18: `BudgetSpec.missing_stages` plus a pipeline startup check reject a
  spec that omits a stage the selected render mode will charge. An unfunded
  stage now raises a configuration error before any provider call instead
  of masquerading as budget exhaustion.
- D-11: `confirm_review(name, pages=...)` accepts a subset, and `inkstone
  identity --confirm NAME --page KEY` exposes it. `pending_pages` still
  reopens for every page when the reference version changes, but a human can
  now review a large cast in batches; the old confirmed state is backfilled
  from `reviewed_version` for pre-existing ledgers.
- D-20: `merge_character_alias` snapshots state and ledger and restores both
  if a later step fails, so a failed ledger rename can no longer leave
  `state.characters` and `consistency.json` disagreeing.
- State split: `source_units` and `adaptation_plans` are derived data and now
  travel in `cache.json` instead of `state.json`, removing the full-text
  rewrite from every projection-only save. Old inline states still load and
  split on the next full save.
- D-15: `tests/test_stage_contract.py` now locks the chat key argument
  surface, so a new key parameter (for example a character table) must be a
  deliberate contract change instead of a silent drift.
- D-13: the finished-page resume short-circuit no longer requires a visual
  bible. A completed chunk is complete from its page records, the same
  evidence the panel branch uses; a missing best-effort bible no longer
  forces a full no-op traversal before the next chunk.
- D-12: a new calendar day now advances `project.day` and clears the stored
  `paused_key` even without `--carry-over`; spent counts are reset only when
  carry-over is requested. A stale boundary can no longer suppress a new
  day's §6 stop record.
- D-14: `GateSession._hold` records a new boundary once per session, so a
  resume whose total chunk count or sample set changed reports the page it is
  actually paused at, while multiple beyond-sample pages in one run still
  produce a single stop.
- D-21: `clear_tombstones` now removes the released key from `pages_done`
  as well as `skipped_pages` and `stale_pages`. A page that was both done
  and stale could otherwise lose its retry marker while remaining marked
  done, making `rebuild --stage render --key` a silent no-op.
- D-20: `page_state_key` rejects negative or 10,000+ positions so the
  four-digit suffix invariant holds; `ensure_character_l1` no longer appends
  the same evidence/warning suffix on every call; recorded merge chains
  (A→B, B→C) resolve to the final canonical name; the dead panel-record loop
  is removed; and a concurrent merge whose kept name disappeared answers 400
  instead of leaking a `KeyError` as HTTP 500.
- D-08: `migrate_legacy_page_keys` now detects collisions instead of
  silently overwriting: a canonical positional key wins over a legacy alias,
  a discarded key is logged, `pages_done` / `stale_pages` / `skipped_pages`
  are deduplicated, and a duplicate `page_id` in a plan warns and keeps the
  first position.
- D-09: `ProjectState.load()` migrates legacy page keys itself, so the
  supervisor, `_run_structure`, the Web checkpoint fallback, GC, prune, and
  every CLI reader get positional keys without remembering to call the
  migration. The now-redundant explicit calls were removed.
- D-19: `INKSTONE_PDF_BATCH` moved into `core.config` as `ENV_PDF_BATCH` /
  `pdf_batch_size()`, so it is registered in `ALL_ENV_VARS` and visible to
  the registry test. It is excluded from `h_env`: it bounds peak memory and
  does not change output bytes. `core/comic/export.py` delegates to it.
- D-22: scheduler documentation corrected. The plan pass is graph-driven;
  render and export use the graph as a validation layer (assertions, export
  gate, outstanding check) while keeping deterministic sequential dispatch.
- D-17: `resolved_font_digest` caches by resolved path + `mtime_ns` + size,
  so a page no longer re-reads and SHA-256s a multi-megabyte CJK font 2–4
  times. A changed font file changes the key; `reset_caches()` clears it.
- D-23: all atomic writers now share `core/comic/atomic.py`: a new file gets
  the process umask (typically `0644`) instead of `mkstemp`'s `0600`, an
  existing file keeps its mode, the parent directory is fsynced after the
  rename, and a failed `os.fdopen` cannot leak the temp fd. This replaces the
  duplicate implementations in `state.json`, `cache.json`, budget, gate,
  ledger, and CAS.
- D-01: an alias merge can no longer leave `reviewed_version` ahead of
  `reference.version`, which silently disabled continuity review.
- D-02: `rebuild_from_state` clears the page set of a character that no longer
  appears on any planned page, and escalation ignores entries with no pages, so
  a rename cannot leave an unresolvable permanent pause. The entry's reference
  and reviewed version are retained.
- D-03: `load_or_rebuild` no longer swallows `OSError`; only a corrupt ledger
  (`ValidationError` / `JSONDecodeError`) falls back to a rebuild, and the fallback
  is logged.
- D-04: the sample gate's growth branch is clamped to at least one page, so a
  zero `chapter_pages` cannot make every page "beyond the sample" forever.
- D-05: `budget.json` and `gate.json` are read-modify-written under a
  data-directory lock, and an already-open session refreshes the shared spent /
  sample state before it writes. Two projects can no longer lose each other's
  charges or gate decisions.
- D-06: a corrupt shared `budget.json` or `gate.json` is quarantined as
  `<name>.corrupt-<timestamp>`, logged, and audited in `runs.jsonl`; the run
  rebuilds an empty account instead of failing every project. `OSError` still
  propagates, because a permission or I/O failure is not corruption.
- D-07: `GateSession.load` searches the shared data directory first and the
  project directory second, so the CLI and `gc` find a gate the pipeline wrote
  project-locally when no `data_dir` was supplied. `gc` already considers both.
- D-10: a cache-hit beats/page-plan stamp is saved at the mutation, not at the
  next end-of-run save. A budget/continuity pause or a crash can no longer lose
  the stamp and force the next resume to re-derive it.

### Upgrade notes
- **Upgrading recomputes each existing project once.** Stage keys are now
  content-addressed (extract, beats, page plan, bible, portrait, storyboard panel,
  finished-page render) and the legacy combined fingerprint is gone, so an existing
  project's next run recomputes the book under the new keys; recorded human decisions
  (review queue, dismissed aliases, merged aliases) survive. Treat the recompute as a
  batch job, not an incident: at the free tier's 1 RPM it costs roughly one minute per
  page of calls, so a 300-page book is on the order of five hours of wall clock.
  `--budget N` pauses the run when N calls are spent, and `--carry-over` re-arms the
  same budget on a later calendar day, so the recompute can settle over several days.
  Progress is counted in produced pages, not percentage: a resumed run first shows
  what was already produced and then advances.
- **`state.json` is now the small checkpoint; the heavy planning caches live in
  the sibling `cache.json`.** `chunk_cache`, `page_cache`, `beat_cache`, and
  `visual_bible` move there, and the first full save after upgrading splits the
  files. Readers using `ProjectState.load` / `ProjectState.save` are unaffected.
  Deleting `cache.json` recomputes the plan stages under their existing action
  keys (billable chat calls; already-rendered pages still restore from their
  manifests when the recomputed keys match). A corrupt `cache.json` raises
  until it is deleted — deliberately, so a lost cache never silently re-plans
  the book.

### Added
- Continuity-review escalation (§12 invariant 11 — the phase 5b follow-up): when
  one character's pending pages exceed `LEDGER_ESCALATION_THRESHOLD` (30, the same
  order as the sample gate), the run holds the render phase at the plan→render
  boundary — one deduplicated `stop` record per unchanged boundary — until a
  human confirms the affected pages as they are; at or below the threshold the
  flag stays quiet and only the ledger and the panel carry it. Confirming clears
  the pending set via `ConsistencyLedger.confirm_review`,
  `POST /api/project/<id>/ledger/confirm`, or
  `inkstone identity --out <dir> --confirm <name>`. The web UI now renders a
  "Continuity review" panel aggregating pending pages per character.
- M2 comic-specific pipeline: `creative_comic` orchestration (segment → extract → character
  consistency L1/L2/L3 → storyboard → generate → layout → export) with resumable `state.json`
  and content-safety graceful skip on upstream rejection.
- Chat provider abstraction (`ChatProvider` / `AgnesChatAPI`) with forced function calling.
- `core/comic`: `consistency` (L1/L2/L3), `segmentation`, `layout` (page + webtoon output),
  `export` (PDF via `manga2pdf` + pure-PIL webtoon PNG).
- `core/screenwriter` for structured extraction/storyboard planning with content-safety hygiene.
- End-to-end example (`examples/generate_comic.py` + `examples/scene1.txt`).
- M3 long-form + consistency hardening:
  - Cross-chapter character reuse: `CharacterAsset` persisted in `state.json`; `merge_characters`
    dedups by exact name so a portrait is never regenerated for a known character.
  - Alias detection: `detect_character_aliases` flags near-duplicate names (e.g. `方鸿渐` vs `鸿渐`)
    into `state.needs_review` for human review — no auto-merge, so a variant name is never silently
    forked into a second character that would fracture consistency.
  - Billing-free resume: per-chunk `extract`/`storyboard` cached in `ProjectState.chunk_cache`, so a
    resume reuses them and never re-pays the (billable) chat API; `panels_done` dedup avoids duplicate
    panel generation. `InsightFace` embedding / L4 iterative refinement remain deferred (GPU-only).

### Changed
- `state.json` no longer carries the heavy planning caches. `chunk_cache`,
  `page_cache`, `beat_cache`, and `visual_bible` are written to the sibling
  `cache.json`: a full save writes the cache file first and the checkpoint
  second, and the render loop's per-page and per-panel saves write the
  checkpoint only. Progress readers (web listing, resume seeding, timing) read
  the checkpoint without requiring the cache file. `ProjectState` fields,
  action keys, fingerprints, manifests, and output paths are unchanged.
- The render fingerprint no longer carries the manual identity, stage-lock,
  layout, voice-timeline, beats, or lettering tokens. The next resume of an
  existing project repaints its pages once, because the stored fingerprint no
  longer matches. A later lettering change still uses the letter action key and
  does not by itself repaint the blank. The structure fingerprint is the
  normalized source. The next resume of a project stored under the old pipeline
  version rebuilds its extract caches once and keeps the human alias decisions.
- Phase 3 extract, beats, page plan, bible, portrait, storyboard panel, and
  finished-page render use action keys. A matching key does not call the provider
  again. A page plan key omits the recent layout strings. A bible key omits the
  bible prose, so a recorded merge does not reconcile every chunk. A stale page
  still repaints. The alias-merge ceiling stays 15 image calls and 0 chat calls at
  300 pages, and the same 15 and 0 at 600 pages. A run plans every chunk before
  it draws a page or a panel. A page script key is that chunk's text, storyboard,
  and extracted elements. An empty stored key is stamped and does not call again.
  A beats key includes that chunk's extract key. A project that already stored a
  beats key replans those beats and its page plan once. A page plan key includes
  that beats key. A project that already stored a page plan key replans the
  pages once. Lettering that is not a contiguous span of the source is dropped.
  A trailing ellipsis stays only when the text before it is in the source. The
  stored blank is lettered again and the image provider is not called. The local
  Journey to the West excerpt now passes every structural check. The README and
  the contributor notes point at that excerpt. The page images and PDF stay in
  `comic_out/`. When that project is already stored, `scripts/run_showcase.sh`
  plans from its page counts and prints the structural check. A plan that
  uses those counts says so, and the tier constants stay the experience rate.
- Measured the cost of one typical alias merge at zero quota (§9 phase 0h, resolved item 26):
  on the fixed 300-page sample (20 chunks x 15 pages, alias on 5 pages of chunks 4/9/14) the
  re-run spends **15 image calls** (the 15 stale pages) and **0 chat calls**. The merge
  is recorded the way a human merge is, so the folded alias is not painted again and does
  not reconcile the bible on the re-run. That replaces the design's "15 to 190" / "15 to 20"
  estimates in the §9 threshold table. The same merge on 600 pages, alias still on those 15,
  spends the same 15 image calls and 0 chat calls, so the re-run does not grow with book length.
- **L3 face overlay is now OFF by default** (`INKSTONE_L3=1` to enable). The cv2/OpenCV
  face-swap pastes a close-up portrait face onto generated panels and deforms stylized faces
  whenever pose/angle/lighting differ (which is most of the time in comic art). Consistency now
  relies on the robust L1 (prompt hard-description) + L2 (reference img2img) path; L3 stays as an
opt-in experiment. `apply_l3` guards were also retuned to use absolute face-pixel size as the
  "big enough" gate instead of a fragile full-frame ratio.
- **Phase 0b: page identity is now positional** (`c{ci:04d}-p{idx:04d}`, matching panel keys).
  The model-generated `page_id` is retained for prompts and auditing but no longer participates in
  identity, so a replan that renames pages cannot orphan recorded pages. Existing
  `state.json` checkpoints with legacy `c{ci:04d}:{page_id}` keys are rewritten deterministically
  on load (zero quota, idempotent), so a resumed project is not repainted.

### Notes
- Executed the idempotency probe (§13 item 27, shipped by Phase 0h): two identical requests to
  Agnes (`agnes-image-2.5-flash`, 1024x1536) returned **different bytes** (1,441,715 and 1,336,638
  bytes; digests `6e53513f…` and `0d5c7e7d…`). The provider is not deterministic, so §8's
  duplicate-charge window stays open — "shrinkable, not closable" — now as a measurement rather
  than an assumption. Re-run with `scripts/probe_image_idempotency.py` (two image calls; the
  script loads `.env`).

## [0.1.0] - 2026-07-20

### Added
- `ImageProvider` abstraction layer: `AgnesImageAPI` (default) and
  `OpenAICompatProvider` (fallback), with a `get_image_provider()` factory.
- Token-bucket `RateLimiter` and an error collector that persists API failures
  to `logs/`.
- Project scaffolding: README, CONTRIBUTING, CODE_OF_CONDUCT, SECURITY,
  SUPPORT, CHANGELOG, issue/PR templates, CODEOWNERS, Dependabot, and a CI
  workflow (ruff lint + format check + pytest on Python 3.10–3.12).
- Upstream attribution and non-fork statement in `NOTICE`.
