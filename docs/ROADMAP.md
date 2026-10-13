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
| Density estimate (D1) | Prototype on `main` | Tiers set the planning budget. `plan` is still an uncalibrated page-count estimate |
| Old PageScript / coverage (D2) | Prototype on `main` — do not treat as gate | Opt-in via `INKSTONE_PAGE_SCRIPT=1`; coverage never vacuous-passes skips |
| Chapter-complete adaptation | Prototype on `main` | Source units, adaptation plans, beat ids, lettering, and `inkstone structure` are in tree. A chapter counts only when every check passes and a page is stored. The Journey excerpt is not validated |
| CBZ, chapter reader, identity CLI | Prototype on `main` | `--format cbz`, `inkstone read`, the web Read pages link, and `identity --view` are in tree. They are not a validated-chapter gate |

### Prototype guardrail (D1/D2 on `main`)

Do **not** present the current D1/D2 code as a completed quality feature:

- density tiers now set the planning budget and the structure fingerprint; the page-count estimate is still uncalibrated;
- PageScript is created after storyboard, so it cannot restore omitted narrative beats;
- coverage currently checks non-empty fields and substring matches, not reader-visible information;
- policy-rejected pages must not be excluded from a final completeness denominator.

Keep these prototypes only as migration material until they conform to the target architecture.

## Next approved work

### P0 — Make the current state honest and deterministic

- [x] Deferred lettering for finished pages: model paints empty chrome; Inkstone overlays caption/dialogue/sfx with CJK-capable fonts. Unlettered art under `pages/blank/` enables resume re-lettering without re-calling the image API. Spec: [`docs/superpowers/specs/2026-07-31-deferred-lettering-design.md`](superpowers/specs/2026-07-31-deferred-lettering-design.md) (local on `feat/deferred-lettering` until merged).
- [x] Make density a real contract: persist it in `ProjectState`, include it in
  the structure fingerprint (`render_fingerprint` covers style/model/L3), pass a
  budget to planning, and invalidate affected caches. Tier B at 8 panels per
  chunk keeps the fingerprint a project already stored, and the panels a planner
  returns are kept. A or C replans, drops the derived caches, and keeps only
  that many panels.
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
- [x] Reconcile README, configuration defaults, CLI help and historical docs so
  they do not contradict released behavior. The CLI describes the shipped
  commands. `identity` is the ledger. `plan` no longer prints a required-info
  count that was only the page count.
- [x] Isolate, rename or remove the old PageScript / coverage prototype so it
  cannot be mistaken for a release-quality gate. `coverage` prints a prototype
  banner before any ratio. It stays off unless `INKSTONE_PAGE_SCRIPT=1`.
- [ ] Calibrate estimates with a public sample; do not promise fixed panels per
  chunk without evidence. `inkstone plan --out` uses panel and page counts a
  project has already planned. The Journey to the West excerpt has no committed
  drawings, so its estimate stays the experience rate.
- [ ] Phase 3 stays closed. The 5x gate still fails: 15 image calls is not five
  times a target of ≤17, and the 600-page count is already flat. Hard and soft
  inputs, positional page identity, and letter/export/layout tombstones have
  landed. The two-phase scheduler and render/extract action keys have not.

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
  alias still on the same 15 pages, leaves the re-run at 15 stale keys, 15 image
  calls, and 0 chat calls. The current architecture already does not grow with book
  length. The 5x gate still fails (15 is not five times a target of ≤17). Phase 3
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
- [x] **0a (letter size)** A letter key uses that accepted size. A square
  fallback re-letters the stored blank and does not call the image provider.
  The same size copies the stored page. An empty `resolved_size` keeps the key
  that was stored for the configured size.
- [x] **0a (bound size)** A PDF, a strip, and a collage record the pixel size of
  the images they bind. Changing the configured page size does not rebuild them
  when those images stay. Mixed pages record each size in bind order.
- [x] **2 (manifest time)** A new letter, export, or layout manifest records when
  it was written. Rewriting that manifest in place keeps the original time.
- [x] **call audit** An image or chat call appends an intent before the provider
  is touched, and a failure appends the exception class. The prompt and the error
  text stay out of `runs.jsonl`. A resume that does not call does not append
  another line. A chat call outside a run writes nothing.
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
- [x] **2 (source closure)** A direct callee of a local stage is in that stage's
  hash. Wrapping CJK and Latin on different lines is part of the collage and
  the strip. The font resolver, the PDF batch size, and the webtoon pixel cap
  stay out.
- [x] **4 (gc)** `inkstone gc` lists `cas/` objects nothing names, once they are
  older than the threshold. A manifest input or output, a rejected manifest's
  previous bytes, a ledger content hash, and a hash named in `gate.json` stay.
  `--apply` is what deletes. An unreadable manifest refuses the plan.
- [x] **4 (pending pages)** A sample page that still awaits a decision keeps the
  `cas/` object whose bytes are that page or its blank. An accepted page, a
  released sample, and a `--yes` sample do not. An unreadable `state.json`
  refuses the plan.
- [x] **4 (ledger bytes)** Recording a portrait copies its bytes into `cas/` and
  stores that content hash on the ledger. `gc` leaves the object in place, so
  the authoritative reference still resolves after the age threshold.
- [x] **4 (older portraits)** A ledger whose reference has a path and no content
  hash copies that file into `cas/` on the next run. The version and the pending
  pages stay as they were. A missing file, or a path outside the project, stays
  unset.
- [x] **1 (layout contract)** `layout` declares the panels, the drawn text, and
  the page geometry. `export` declares the bound images and the binding. Neither
  reads an accumulated window or a render-only knob.

### P1 — Chapter-complete adaptation MVP

- [x] Introduce normalized, globally addressable `SourceUnit` records. A unit is
  `u{index:04d}` plus a character span of the normalized source. The spans
  concatenate back to that source. They are stored on the project and do not
  enter a fingerprint. A heading that uses 回 is still one unit.
- [x] Generate per-chapter `AdaptationPlan` and `NarrativeBeat` records with
  required / optional status and causal dependencies. A plan is stored per
  source unit. A key beat the extractor marked must-draw is required; the rest
  are optional. Each beat after the first depends on the previous beat in that
  unit. A chunk that sits in two units is left unassigned. The copy does not
  call a model and does not enter a fingerprint.
- [x] Generate beat-constrained storyboard panels with explicit `beat_ids`.
  A panel keeps a beat id only when that beat was already extracted. Each
  required beat is written onto a panel. Optional beats stay unnamed unless a
  panel already named them. A chunk with no beats keeps empty `beat_ids`, and
  the storyboard call does not gain another model call.
- [x] Add a reader-visible lettering layer: separate caption, dialogue and SFX.
  - **Local:** MVP lettering fields + layout drawers shipped (caption bar /
    dialogue bubble / sfx outline). A detected face rectangle moves the box
    off that face. No detected face leaves the box where it was.
- [x] Add structural coverage gates for source traceability, beat coverage,
  visible text, causal order and blocked content. `inkstone structure` reads
  the stored project. A check with nothing to read is absent, not a pass.
  Skipped chunks and tombstones fail the blocked check. The report does not
  call a model and is not a readability or quality gate.
- [ ] Validate one public-domain chapter end-to-end before attempting a whole book.
  A chapter counts only when every structural check passes and a page is stored.
  The Journey to the West excerpt is one source unit; beats, lettering, and a
  stored page are absent, so it is not validated.

### P2 — Delivery experience and proof

- [ ] Add CBZ export and improve PDF typography, including CJK caption support.
  - **Local:** `--format cbz` zips the page images in filename order and writes
    `ComicInfo.xml` with that page count. A second
    bind copies the stored archive. Pillow writes pages at 150 dpi. img2pdf
    uses that same dpi in its page layout, so a PNG without a dpi tag is not
    left at the converter default of 96. A CJK
    caption that wraps past its bar is drawn smaller so the glyphs stay inside
    the bar. Dialogue and sfx that do not fit their box do the same and stop at
    the box edge. The caption bar, the dialogue outline, and the sfx stroke
    all stop on the last pixel inside that edge. A box narrower than one glyph
    does not paint past that edge. A closing mark stays with the previous glyph,
    and an opening mark stays with the next, when that pair fits the line. A
    later closing mark does not pull that glyph away and leave the opening mark
    alone. An ellipsis stays with the previous glyph when that group fits the
    line. A halfwidth comma hangs on the previous CJK glyph and leaves a Latin
    word intact. A halfwidth opening mark stays with the next glyph when that
    pair fits the line. A dash, a tilde, and a middle dot hang on the previous
    glyph when that group fits the line. On a page grid, sfx starts below the
    caption bar. A percent sign and a degree mark hang on the previous digit
    when that pair fits the line. A fullwidth percent hangs on the previous
    digit the same way. A letter unit such as cm hangs on the previous digit
    when that group fits the line. A decimal keeps its letter unit with the
    last digit the same way. A permille and a prime hang on the previous digit
    when that pair fits the line. A spaced letter unit keeps that space and
    hangs on the previous digit when that group fits the line. A fahrenheit
    sign, an ohm sign, and a micro sign hang on the previous digit the same
    way. A single-character unit such as ㎡ or ㎏ hangs on the previous digit
    when that pair fits the line. A currency mark hangs on the previous digit
    when that pair fits the line. A space before that mark stays when the
    spaced group fits the line. 块, 角, and 分 hang on the previous digit the
    same way. A thousands group stays together, and its comma hangs on the
    previous digit when that group fits the line. A decimal after that group
    hangs on the last digit the same way. A letter unit after that decimal
    stays with the fraction when the pair fits the line. A classifier such as
    个, 只, 名, 位, or 岁 hangs on the previous digit the same way. A
    caption that mixes CJK and Latin keeps each Latin word intact.
    The PDF is still that raster.
- [ ] Publish a public-domain *Journey to the West* chapter showcase with source,
  plan, coverage report and PDF.
  - **Local (scaffold):** `examples/showcase/journey-west-ch1/` +
    `scripts/run_showcase.sh` (source + plan recipe; generated artifacts not
    committed — option C).
- [x] Add chapter navigation / browser reader and an identity-ledger CLI or view.
  - **Local:** `inkstone read --out` lists stored pages by the chunk id in the
    filename and writes `reader.html`. The page shows its index and the stored
    page count. Arrow keys flip a page. A chunk button
    jumps to that chunk's first page. `inkstone identity --view` already prints
    the ledger. A finished web job links to the same file when pages are stored;
    opening that link writes it. The reader does not call a model.
- [ ] Rewrite README and contributor experience around the validated long-form workflow.
  - **Local:** The README names PDF, CBZ, and PNG, and the shipped commands. It
    says a chapter is validated only when every structural check passes and a
    page is stored. The Journey excerpt is not that chapter.

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
