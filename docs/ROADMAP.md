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

Long-form target architecture and developer onboarding notes currently live as
**local working drafts** under `docs/architecture/` and `docs/guides/` (not yet
versioned on `main`). Until they are published, treat this roadmap plus the
released code as the source of truth for status.

When completing a change:

1. update the relevant item below;
2. do not mark an item Released until it is committed and CI passes;
3. explicitly record unfinished local experiments as Local prototype;
4. if the developer workflow or system boundary changes, update the local
   onboarding draft (and this roadmap) in the same change.

## Current implementation status

| Area | Status | Notes |
|---|---|---|
| Core TXT → comic pipeline | Released | Segmentation, extraction, portraits, storyboard, panels, layout, PDF / Webtoon export, `state.json` resume |
| Providers and reliability | Released | Agnes + OpenAI-compatible routing, rate limit, retry and JSONL error collection |
| Cross-chapter identity | Released | L1/L2 consistency, alias review, stale-only redraw; L3 is experimental and off by default |
| Web UI and unattended supervisor | Released | Local browser UI, cancel, retry, review, deadline pause / resume |
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
- [ ] Reconcile README, configuration defaults, CLI help and historical docs so
  they do not contradict released behavior.
- [ ] Isolate, rename or remove the old PageScript / coverage prototype so it
  cannot be mistaken for a release-quality gate.
- [ ] Calibrate estimates with a public sample; do not promise fixed panels per
  chunk without evidence.

### P1 — Chapter-complete adaptation MVP

- [ ] Introduce normalized, globally addressable `SourceUnit` records.
- [ ] Generate per-chapter `AdaptationPlan` and `NarrativeBeat` records with
  required / optional status and causal dependencies.
- [ ] Generate beat-constrained storyboard panels with explicit `beat_ids`.
- [ ] Add a reader-visible lettering layer: separate caption, dialogue and SFX.
- [ ] Add structural coverage gates for source traceability, beat coverage,
  visible text, causal order and blocked content.
- [ ] Validate one public-domain chapter end-to-end before attempting a whole book.

### P2 — Delivery experience and proof

- [ ] Add CBZ export and improve PDF typography, including CJK caption support.
- [ ] Publish a public-domain *Journey to the West* chapter showcase with source,
  plan, coverage report and PDF.
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
