# Inkstone Roadmap

> This file is the **single progress-control document** for humans and AI.
> It records what is released, what only exists locally, and what is approved next.
> Update it whenever implementation status changes.
> Inkstone is an independent, from-scratch implementation (not a fork), so we do not carry over the
> upstream `server.py` / video / audio modules; the comic-specific layer is written to this project's standard.

## How to read and update this roadmap

Status definitions:

- **Released**: committed to `main` and CI is green.
- **Prototype on `main`**: committed code that is experimental or estimate-only; not a product capability or quality gate.
- **Local prototype**: present only in a working tree; not a product capability.
- **Planned**: approved direction, not yet implemented.

Developer onboarding for the shipped tree is [`docs/ONBOARDING.md`](ONBOARDING.md).
The **target** architecture is now versioned:
[`docs/architecture/2026-09-28-content-addressed-pipeline-design.md`](architecture/2026-09-28-content-addressed-pipeline-design.md)
(Chinese version: [`…-design.zh-CN.md`](architecture/2026-09-28-content-addressed-pipeline-design.zh-CN.md)).
Its three original blockers (L2 continuity cascade, dynamic page DAG, missing
tombstones) and its majors were verified against the shipped tree and have been
folded into the design (§2, §4, §6, §12); they are now design requirements rather
than open findings. The separate review document was never committed to this repo,
so it is not linked here. Treat this roadmap, the onboarding guide, and the released code as
the source of truth for **current status**; the architecture document is the source
of truth for **where the design is heading**. Where they disagree, status wins until
the migration lands. Additional long-form drafts may still live under local
`docs/architecture/`.

When completing a change:

1. update the relevant item below;
2. do not mark an item Released until it is committed and CI passes;
3. explicitly record unfinished local experiments as Local prototype;
4. if the developer workflow or system boundary changes, update
   [`docs/ONBOARDING.md`](ONBOARDING.md) (and this roadmap) in the same change.

## Current implementation status

| Area | Status | Notes |
|---|---|---|
| Core TXT → comic pipeline | Released | Segmentation, extraction, portraits, storyboard, panels, layout, PDF / Webtoon export, `state.json` resume. **Default render mode is `finished_page`** (one image per comic page); `panel_compose` is the explicit legacy fallback (`INKSTONE_RENDER_MODE=panel_compose`). **Finished-page text:** deferred lettering — blank art + font overlay ([spec](superpowers/specs/2026-07-31-deferred-lettering-design.md)). |
| Providers and reliability | Released | Agnes + OpenAI-compatible routing, rate limit, retry and JSONL error collection. **Local temp:** default Agnes `BASE_URL` is `apihub.agnes-ai.cn` (domestic reachability); revert to `.com` or make env-configurable when access stabilizes (`TODO(temp)` in `core/api/chat_provider.py` / `agnes_image.py`). |
| Cross-chapter identity | Released | L1/L2 consistency, alias review, stale-only redraw; L3 is experimental and off by default |
| Web UI and unattended supervisor | Released | Local browser UI, cancel, retry, review, deadline pause / resume; job/project JSON exposes `render_mode`, `pages_done`, `skipped_pages` |
| Colab operations | Released | Background jobs, download progress, alias adopt after 404/401 |
| Page-PDF recovery and source-language dialogue prompt | Released | Existing panels can be re-exported to PDF; new runs request dialogue in source language |
| Density estimate (D1) | Prototype on `main` | CLI estimator only; A/B/C labels match product brief; does not constrain generate |
| Old PageScript / coverage (D2) | Prototype on `main` — do not treat as gate | Opt-in via `INKSTONE_PAGE_SCRIPT=1`; coverage never vacuous-passes skips |
| Chapter-complete adaptation | Planned | SourceUnit → NarrativeBeat → constrained storyboard / lettering → structural coverage |
| CBZ, chapter reader, identity CLI | Planned | Follow the chapter-complete MVP; not current blockers |

### Prototype guardrail (D1/D2 on `main`)

Do **not** present the current D1/D2 code as a completed quality feature:

- density tiers currently estimate cost/pages only; they do not control actual panel count;
- PageScript is created after storyboard, so it cannot restore omitted narrative beats;
- coverage currently checks non-empty fields and substring matches, not reader-visible information;
- policy-rejected pages must not be excluded from a final completeness denominator.

Keep these prototypes only as migration material until they conform to the target architecture.

## Next approved work

### P0 — Make the current state honest and deterministic

- [x] Deferred lettering for finished pages: model paints empty chrome; Inkstone overlays caption/dialogue/sfx with CJK-capable fonts. Unlettered art under `pages/blank/` enables resume re-lettering without re-calling the image API. Spec: [`docs/superpowers/specs/2026-07-31-deferred-lettering-design.md`](superpowers/specs/2026-07-31-deferred-lettering-design.md) (local on `feat/deferred-lettering` until merged).
- [ ] Make density a real contract: persist it in `ProjectState`, include it in
  the structure fingerprint (`render_fingerprint` covers style/model/L3), pass a
  budget to planning, and invalidate affected caches.
  - **Local (v0.1.2):** structure/render fingerprint split landed — style, model,
    and L3 changes soft-invalidate panels/portraits only; `chunk_cache` is reused.
    Legacy states still compare the combined hash until migrated.
  - **Local (v0.1.3):** defect-review patch — sparse chunk panel ordering, preserve
    content-policy `skipped` across soft-invalidate, Latin word-wrap, unique
    `unnamed_*` identities, fill empty appearance on merge (see
    `docs/superpowers/plans/2026-07-27-v0.1.3-defect-review-fixes.md`).
  - **Local (post-0.1.3):** page layout letterbox (no aspect squash), README
    provider honesty, web Origin/body caps, dotenv no longer skips on preset
    `AGNES_API_KEY`, `inkstone plan` marked `[experimental]` (see
    `docs/superpowers/plans/2026-07-27-deep-dive-layout-web-fixes.md`).
- [ ] Reconcile README, configuration defaults, CLI help and historical docs so
  they do not contradict released behavior.
- [ ] Isolate, rename or remove the old PageScript / coverage prototype so it
  cannot be mistaken for a release-quality gate.
- [ ] Calibrate estimates with a public sample; do not promise fixed panels per
  chunk without evidence.
- [ ] Resolve the three blockers recorded in the [content-addressed pipeline
  design](architecture/2026-09-28-content-addressed-pipeline-design.md)
  before starting any migration phase:
  1. separate **hard inputs** from **soft L2 reference images**, so an alias merge
     redraws affected pages only and never cascades through the book-wide
     `previous_page_blank` continuity chain;
  2. define the scheduler as a **two-phase dynamic DAG** (plan, then expand) with
     **positional** page identity, replacing the model-generated `page_id` in
     `_page_state_key`;
3. add **tombstone manifests** (`outcome: ok | rejected | stopped | awaiting_human`) so
     content-policy rejections are not re-attempted — and quota burned — on every
     resume.

### P0 — Content-addressed pipeline migration (0a-0h)

Per the [content-addressed pipeline design](architecture/2026-09-28-content-addressed-pipeline-design.md) §9.

- [x] **0b** Positional page identity: a page's identity is `c{ci:04d}-p{idx:04d}`,
  converging on the panel-key convention. Model-generated `page_id` no longer
  participates in identity; a deterministic, zero-quota rewrite on load migrates
  legacy `c{ci:04d}:{page_id}` keys so a resumed project is not repainted.
- [x] **0d** Consistency ledger (`consistency.json`, §12): an explicit,
  authoritative record of which pages each character appears on. It is
  CAS-independent and zero-quota — page sets are derived from `page_cache`, and
  the ledger doubles as the decided L2 reference source for Phase 1 (resolved
  item 17). Landed: schema + atomic IO, derivation, corruption recovery,
  reference versions/pending review, alias-merge queries, pipeline maintenance,
  a `ledger_pending` web snapshot field, and `inkstone identity --view`. The
ledger enters no `action_key` or fingerprint (guarded by a test).
- [x] **0g** Minimal reclaimer: `inkstone prune --older-than <N>d [--apply]` deletes
  generated image assets whose reference count is zero **and** whose age exceeds
  the threshold, dry-run by default (§7, resolved items 3 and 22). CAS-independent
  and zero-quota: today artifacts are written at deterministic, overwritten paths,
  so the root set is `state.json` + `consistency.json`; `collect_live_refs` is the
  seam Phase 4's `gc` extends. It ships before Phase 3, the entry point that writes
  image bytes.
- [x] **5a** Quota budget. `inkstone generate --budget N` caps billable calls for
  that run. Each stage has its own reservation (a render overrun cannot spend the
  extract reservation) and one call is withheld as a recovery reserve when the
  budget is at least 2. Exhaustion pauses at the item boundary, writes one
  `runs.jsonl` stop, and a resume at the same boundary does not append another or
  issue the call. `--no-carry-over` is the default: a later calendar day does not
  re-arm the allowance; `--carry-over` does. The budget enters no fingerprint.
  Shared accounts live in `quota.jsonl` and `budget.json` under the data directory
  (`INKSTONE_DATA_DIR`, default `~/.inkstone`). Omitting `--budget` keeps today's
  uncapped run, so existing projects are not paused by surprise.
- [x] **5b** Sample gate. A render batch larger than 30 pages, spanning two chunks
  when the book has two, pauses for a human decision before the remainder is
  drawn. `inkstone gate --decision accept|redraw|accept-and-flag` records the
  per-page card; accept and accept-and-flag release the rest once every sample
  page is decided that way, and the acceptance rate then moves the next sample
  (`round(n * 1.618)`, lock, or halve). `--yes` is the audited escape hatch.
  A resume at the same boundary does not append another stop or issue the
  call. The gate enters no fingerprint. Shared state is `gate.json` under the
  data directory when the CLI or web pass it, otherwise the project directory.
- [x] **0h growth** Doubling the alias-merge sample from 300 pages to 600, with the
  alias still on the same 15 pages, leaves the re-run at 15 stale keys, 16 image
  calls, and 1 chat call. The current architecture already does not grow with book
  length. The 5x gate still fails (16 is not five times a target of ≤17). Phase 3
  stays off the generate path. Phase 2's object store is the part that has started.
- [x] **1 (contracts)** Stage input declarations. `page_plan` may read earlier
  layouts only through a window of 8; the declared input is that window plus the
  summarizer source hashes, not the intent strings and not `page_cache`. Chat
  stages do not take `{page_size, panel_continuity, l3_enabled}`, and those knobs
  do not change the structure fingerprint. The L2 continuity image stays a soft
  reference of `render.page`. This does not add CAS or action keys.
- [x] **1 (page bible)** A finished-page prompt carries bible entries only for the
  characters on that page. An off-page canon cannot appear in the prompt, so a
  later key for the page cannot depend on the rest of the book. Style, era, and
  color stay global.
- [x] **0a (resolved size)** A finished page records the size the provider
  accepted. When the portrait-shaped request is rejected, `resolved_size` is the
  square fallback, not the configured string. Old `state.json` files load with
  the field empty.
- [x] **1 (render mode)** Switching a finished-page project to `panel_compose`
  plans the missing storyboard. A finished-page completion is not treated as a
  storyboard hit.
- [x] **1 (remaining stages)** `portrait`, `page_script`, `render.panel`,
  `letter`, and `export` declare their inputs. None of them reads an accumulated
  window. The previous panel and the canonical portrait stay soft references.
  Lettering does not take the font path as an input.
- [x] **1 (human state)** Replacing the projection because the source changed
  keeps `needs_review`. Page tombstones belong to the discarded projection and
  are dropped with it.
- [x] **1 (dismissed alias)** Dismissing an alias records the pair. A later
  extract, including one after the source changes, does not put that pair back
  on `needs_review`. Old `state.json` files load with the record empty.
- [x] **1 (merged alias)** Merging an alias records the pair. A later extract
  that sees both names folds them again before portraits and planning, and does
  not reopen the review queue. The cached extract text stays unchanged.
- [x] **1 (alias pair)** A dismiss or a merge matches the two names in either
  order. A later extract that introduces the canonical name first does not
  reopen the queue, and a merge still folds toward the recorded canonical.
- [x] **1 (bible summary)** `bible` reads one current summary, not earlier
  chunks. Its declared input is that summary plus the summarizer source hashes.
  The summary body stays out of the declaration.
- [x] **2 (store)** A project can hold `cas/` objects and `index/` manifests.
  An object is written once under its content hash. Rewriting a manifest keeps
  the key and records `supersedes`. `inkstone verify --out ...` checks that
  every output exists and matches its hash, and that a non-ok outcome has a
  reason. It does not reconcile `state.json`.
- [x] **2 (letter)** Finished-page lettering writes a manifest under the project.
  The same blank, plan, and environment copies the stored bytes instead of
  drawing again. A different blank letters again. Render keys stay off the
  generate path.
- [x] **2 (export)** Binding the page images into `comic.pdf` writes a manifest.
  The same page bytes and the same layout copy the stored PDF. A changed page
  binds again.
- [x] **2 (verify in CI)** A CI job builds a temporary project from a few KB of
  synthetic bytes, runs `inkstone verify`, and fails when one object is
  deleted. No example book is committed.
- [x] **2 (tombstone)** A rejected letter or export manifest is a hit. The next
  run does not letter or bind that key again. `inkstone rebuild --stage
  letter|export --key ...` drops the manifest, and the following run does the
  work. An ok manifest stays.
- [x] **2 (webtoon)** Stacking pages or panels into `webtoon.png` writes a
  manifest. The same images and the same drawn text copy the stored strip. A
  changed panel or a changed line stacks again.
- [x] **2 (layout)** Collaging panels into page sheets writes one manifest for
  every sheet. The same panels and the same drawn text copy those sheets. A
  changed line collages again. A rejected collage is not bound into a PDF until
  `rebuild --stage layout` releases it.
- [x] **2 (font)** A changed font re-letters a finished page from its stored
  blank. The image provider is not called, and the blank bytes stay as they
  were. The same font copies the stored page.
- [x] **2 (stage source)** Letter, layout, webtoon, and export keys hash the
  functions that draw or bind their bytes. A page-grid change is not part of
  lettering. The pipeline function stays out of those hashes.
- [x] **2 (shorter collage)** A collage that writes fewer sheets deletes the
  extra `page_NN.png` files. A finished-page file in the same directory stays.
  The PDF binder therefore sees only the sheets this collage produced.
- [x] **1 (layout contract)** `layout` declares the panels, the drawn text, and
  the page geometry. `export` declares the bound images and the binding. Neither
  reads an accumulated window or a render-only knob.

### P1 — Chapter-complete adaptation MVP

- [ ] Introduce normalized, globally addressable `SourceUnit` records.
- [ ] Generate per-chapter `AdaptationPlan` and `NarrativeBeat` records with
  required / optional status and causal dependencies.
- [ ] Generate beat-constrained storyboard panels with explicit `beat_ids`.
- [ ] Add a reader-visible lettering layer: separate caption, dialogue and SFX.
  - **Local:** MVP lettering fields + layout drawers shipped (caption bar /
    dialogue bubble / sfx outline). Face-aware placement still open.
- [ ] Add structural coverage gates for source traceability, beat coverage,
  visible text, causal order and blocked content.
- [ ] Validate one public-domain chapter end-to-end before attempting a whole book.

### P2 — Delivery experience and proof

- [ ] Add CBZ export and improve PDF typography, including CJK caption support.
  - **Local:** Pillow PDF export is memory-bounded (batched + `pypdf` merge;
    optional `img2pdf`).
- [ ] Publish a public-domain *Journey to the West* chapter showcase with source,
  plan, coverage report and PDF.
  - **Local (scaffold):** `examples/showcase/journey-west-ch1/` +
    `scripts/run_showcase.sh` (source + plan recipe; generated artifacts not
    committed — option C).
- [ ] Add chapter navigation / browser reader and an identity-ledger CLI or view.
- [ ] Rewrite README and contributor experience around the validated long-form workflow.

## M1 — Image Provider abstraction foundation ✅ done

Goal: unify image generation behind a single, swappable interface — ordinary users run Agnes with
zero config; advanced users can switch to any OpenAI-compatible endpoint.

- [x] `core/api/image_provider.py`
  - `ImageOutput` (result with `url` / `b64` forms; `.save()` persists and **downloads without an auth token**)
  - `ImageProvider(ABC)`: abstract `async generate_single_image(...)` contract
  - `OpenAICompatProvider(ImageProvider)`: any OpenAI-compatible image endpoint (Gemini, ...), hedges single-provider risk
  - `get_image_provider(...)`: factory reading `PROVIDER` / `AGNES_API_KEY` / `OPENAI_COMPAT_*`, default agnes
- [x] `core/api/agnes_image.py`: `AgnesImageAPI(ImageProvider)` with exponential-backoff retries + error collection
- [x] `core/api/rate_limiter.py`: thread-safe token bucket (default 20/min × 0.8 safety factor)
- [x] `core/api/error_collector.py`: API failures persisted to `logs/`, self-failures never break the main flow
- [x] `utils/image.py`: `download_image` (bare request, 50MB cap)
- [x] `tests/test_providers.py`: 6 cases — interface conformance + factory missing-key error, **all passing**

Verified: `pytest` → 6 passed.

## M2 — Comic-specific pipeline ✅ done

Goal: assemble text-to-image / image-to-image into a real comic production chain.

**New:**
- [x] `core/api/chat_provider.py` — `ChatProvider` + `AgnesChatAPI` + `get_chat_provider()` (forced function calling)
- [x] `core/schemas.py` — `CharacterAsset` / `StoryElements` / `Storyboard` / `ProjectState` (double as function-tool schemas)
- [x] `core/comic/consistency.py` — `ConsistencyEngine`
  - L1: prompt hard-describes character features
  - L2: reference-image img2img (character portrait as `reference_image_paths`)
  - L3: PIL/OpenCV face / feature overlay fallback (cv2 lazy; quality guards skip on failure)
- [x] `core/comic/segmentation.py` — `segment_text` (chapter/token split + overlap) + `merge_characters` (exact-name dedup)
- [x] `core/comic/layout.py` — `LayoutEngine`: N panels on a grid (page) or vertical strip (webtoon) + dialogue bubbles
- [x] `core/comic/export.py` — `ExportEngine`: PDF (`manga2pdf`) + vertical-strip PNG (pure PIL)
- [x] `core/pipelines/creative_comic.py` — orchestration: portraits → panels → layout → export, with `state.json` resumption (regenerates missing panels) and content-safety graceful skip
- [x] `core/screenwriter.py` — screenwriter: forced extract/storyboard + content-safety hygiene (`sanitize_text`, `is_content_policy_rejection`)
- [x] `examples/generate_comic.py` + `examples/scene1.txt` — end-to-end runnable demo (needs `AGNES_API_KEY`)

**Reused (not rewritten):** `get_image_provider`, `AgnesImageAPI`, `RateLimiter`, `error_collector`, `utils.image.download_image`.

**New deps (in requirements):** `pydantic` (schemas), `manga2pdf` (PDF export, optional CLI).

Verified: `pytest` → 64 passed (1 cv2-dependent test skipped), CI green on Python 3.10–3.12.

## M3 — Long-form + consistency hardening ✅ done

- [x] Long text split by chapter / segment → generate per segment → cross-chapter character assets reused
  (`CharacterAsset` persisted in `state.json`; `merge_characters` dedups by exact name so the same portrait is never regenerated).
- [x] Default consistency strategy **L1 + L2** wired and exercised end-to-end (prompt hardening / multi-image reference).
  - **L3 (cv2 Haar face overlay) is opt-in and OFF by default** since it deforms stylized faces when pose/lighting differ; enable with `INKSTONE_L3=1`. Verified by regenerating `comic_out` end-to-end with L3 off.
- [x] Cross-chapter alias detection: `detect_character_aliases` flags near-duplicate names (e.g. `方鸿渐` vs `鸿渐`) into
  `state.needs_review` for human decision — **no auto-merge**, so a variant name is never silently forked into a second character.
- [x] Resumption: checkpoint in `state.json`; per-chunk `extract`/`storyboard` cached in `chunk_cache` so a resume reuses them
  and never re-pays the (billable) chat API; `panels_done` dedup key avoids duplicate panel generation / billing.
- [ ] **Deferred (GPU / optional):** `InsightFace` embedding for precise "which face belongs to which character" matching
  (avoids L3 mis-overlay) and **L4** multi-round iterative refinement. The whitepaper scopes these as local-GPU-only and out
  of the default zero-cost path; revisit only if a GPU branch is added.

Historical verification at M3 completion: `pytest` → 67 passed (1 cv2-dependent test skipped), CI green on Python 3.10–3.12. Current verification commands and status are defined above.

## M4 — Open-source release ✅ done

Goal: make Inkstone genuinely runnable and reviewable by an outside contributor.

- [x] `comic_out/` added to `.gitignore` (generated artifacts no longer committable by accident).
- [x] Initial design, review and hosting notes created; historical copies may
  live under local `docs/archive/` until deliberately republished.
- [x] One-click launch script (`scripts/start.sh` / `scripts/start.ps1`) — sets up env, installs deps, runs the demo.
- [x] Sample-novel demo — runnable `txt` inputs ship in `examples/` (`scene1.txt` + `sample_novel.txt`).
- [x] README gallery — committed 3 sample panels + a downscaled webtoon under `assets/samples/` and referenced them.
- [x] Optional lightweight Web UI: single-file Tailwind SPA (zero build) + zero-dependency stdlib `http.server` backend that runs the pipeline and streams panels.
- [x] Same SPA deployed to GitHub Pages in demo mode (auto-detects backend via `/api/health`) so the public site shows the identical UI and a real generated sample (the "effect").
