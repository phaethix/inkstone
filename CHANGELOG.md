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
  key does not include the upstream plan key. `inkstone migrate --check` is not
  implemented yet; use `--budget` on a long book and delete `cache.json` only if
  you intend to re-plan every stage.

### Added
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

### Fixed
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
