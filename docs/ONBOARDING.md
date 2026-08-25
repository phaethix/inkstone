# Inkstone onboarding

Developer map of the shipped codebase. For **what is released vs prototype vs planned**,
use [`ROADMAP.md`](ROADMAP.md). Historical plans live under [`superpowers/`](superpowers/)
and are **not** current architecture.

All code, comments, docs, and commit messages are in English ([CONTRIBUTING.md](../CONTRIBUTING.md)).

## Project overview

Inkstone is a local-first novel-to-comic generator: a `txt` novel in, comic pages
out (PDF or webtoon PNG). It is **Agnes-native and zero-cost** — one free API key,
no GPU, no paid plan.

| | |
|---|---|
| Languages | Python 3.10+ |
| Frameworks | pydantic, Pillow, requests, tenacity, stdlib `http.server` (web UI) |
| Package | `inkstone` (`core.cli:main`) |
| Version | 0.1.0 (M1–M4 released) |

The product trade-off is explicit: free cloud Agnes cannot match GPU identity
methods (IP-Adapter / InsightFace). Consistency uses L1 prompt hardening + L2
reference img2img. Optional L3 face overlay is **off** by default.

## Architecture layers

```
CLI / Web UI
    ↓
run_until_complete   (unattended retries on 429/503; 24h wall-clock pause)
    ↓
creative_comic       (orchestration + state.json resume)
    ↓
screenwriter         (forced function calling)  +  comic/* (identity, lettering, export)
    ↓
ChatProvider / ImageProvider   (Agnes default; openai_compat optional)
```

| Layer | What belongs here | Key files |
|---|---|---|
| Providers | Swappable chat/image APIs, rate limit, retry, error log | `core/api/` |
| Contracts | Pydantic models; LLM tool schemas generated from the same types | `core/schemas.py` |
| Planning | Extract, storyboard, finished-page plans, visual-bible reconcile, key beats | `core/screenwriter.py` |
| Comic domain | Consistency L1/L2/L3, bible, lettering, layout, export, alias merge | `core/comic/` |
| Pipeline | Segment → plan → paint → letter → bind; fingerprints; resume | `core/pipelines/creative_comic.py` |
| Supervisor | Keep a job alive through free-tier failures | `core/pipelines/run_until_complete.py` |
| Surfaces | CLI, local web UI, examples, launch scripts | `core/cli.py`, `web/`, `examples/`, `scripts/` |
| Config | Every env read | `core/config.py`, `.env.example` |

Two render modes (`INKSTONE_RENDER_MODE`):

- **`finished_page` (default)** — one image call per comic page; model paints empty
  lettering chrome; Inkstone overlays caption / dialogue / sfx with real fonts.
  Unlettered art is cached under `pages/blank/` so resume can re-letter without
  another image call.
- **`panel_compose` (legacy)** — storyboard → per-panel images → `LayoutEngine`
  grid/strip → export. Recovery path when finished pages fail.

## Key concepts

**Character consistency (no GPU)**

| Layer | Strategy | Default |
|---|---|---|
| L1 | Appearance-derived prompt hard-description | on |
| L2 | Reference img2img from portraits / prior pages | on |
| L3 | PIL/OpenCV face overlay | off (`INKSTONE_L3=1`) |

**Visual Bible** — project-level character canon (`ProjectState.visual_bible`).
Reconcile merges aliases, locks `face_lock` / `hair_lock` / stage portraits, and
applies era / gender / diegetic gates. `face_lock` must not be overwritten by a
later reconcile. Appearance claims should carry `appearance_evidence` (verbatim
source quotes); empty evidence is marked unverified at L1 inject time.

**Alias review** — near-duplicate names (e.g. `祥子` vs `骆驼祥子`) go to
`state.needs_review`. **Never silent-merge.** A merge marks affected pages/panels
stale for selective redraw.

**Deferred lettering** — image model does not paint readable glyphs. Inkstone
composites CJK-capable fonts. Lettering language must match the source excerpt.

**Key beats** — turning points that must be *drawn*, not caption-only. Uncovered
must-draw beats trigger a planner retry note.

**Layout anti-template** — planner sees recent `layout_intent`s; consecutive
center-standee pages are discouraged.

**Voice / timeline** — narration → `caption` (empty speaker); spoken lines →
`dialogue` + `speaker`; page-level `timeline` (`present` / `past` / `liminal`).

**Fingerprints** — `structure_fingerprint` invalidates extract/storyboard cache
when source/pipeline version changes. `render_fingerprint` soft-invalidates
pages/portraits when style, model, L3, render mode, page size, or bible hash
changes. Resume must not re-pay the chat API for unchanged chunks.

**D1 density / D2 PageScript** — prototypes on `main`, **not quality gates**.
`inkstone plan` estimates cost/pages only; it does **not** constrain `generate`.
`inkstone coverage` reports leftover PageScript fields (`INKSTONE_PAGE_SCRIPT=1`).
Do not present them as completed product features.

**Resume / cancel** — `state.json` is written atomically. The web UI and CLI
share `comic_out/<project_id>/`. Cooperative cancel via `cancel_check`. Content
policy skips are recorded (`skipped` / `skipped_pages`), not retried blindly.

## Guided tour

Read in this order the first time you touch the repo.

1. **Purpose and honesty** — [`README.md`](../README.md), then this file and
   [`ROADMAP.md`](ROADMAP.md).
2. **Run it** — `pip install -e ".[dev]"`, copy `.env.example` → `.env`,
   `python examples/first_panel.py` or `./scripts/start.sh`. Tests: `pytest`
   (offline). Web: `python web/server.py`.
3. **Contracts** — `ProjectState`, `ComicPagePlan` / `ComicPagePlanSet`,
   `VisualBible` / `CharacterCanon`, `CharacterAsset` / `Appearance` /
   `EvidenceQuote` in `core/schemas.py`. LLM tools are `to_tool_schema(...)`.
4. **Orchestration** — `creative_comic` / `_creative_comic` in
   `core/pipelines/creative_comic.py`. Follow `finished_page` first; treat
   `panel_compose` as the fallback branch.
5. **LLM planning** — `core/screenwriter.py` (`SYSTEM_PROMPT` + extract /
   `plan_comic_pages` / `reconcile_visual_bible` / `extract_key_beats`).
6. **Identity + paint + letter** — `core/comic/visual_bible.py`,
   `consistency.py`, `page_prompt.py`, `page_lettering.py`.
7. **Surfaces** — `core/cli.py` → `core/cli_generate.py` →
   `run_until_complete`; `web/server.py` routes (`/api/generate`, review, stop,
   regen).

## File map

### Providers and reliability

| File | Role |
|---|---|
| `core/api/image_provider.py` | `ImageProvider` ABC, `OpenAICompatProvider`, factory |
| `core/api/agnes_image.py` | Default t2i / i2i with retries |
| `core/api/chat_provider.py` | `ChatProvider`, forced function calling |
| `core/api/rate_limiter.py` | Token bucket (free-tier RPM × 0.8) |
| `core/api/retry.py` | Exponential backoff POST |
| `core/api/error_collector.py` | JSONL errors under `logs/` (must not break the run) |
| `utils/image.py` | Download with size cap |

### Pipeline and state

| File | Role |
|---|---|
| `core/pipelines/creative_comic.py` | End-to-end generate; project lock; fingerprints |
| `core/pipelines/run_until_complete.py` | Supervisor; `PausedRun` on deadline |
| `core/pipelines/cancel.py` | `PipelineCancelled` |
| `core/pipelines/timing.py` | Remaining-time estimate for the UI |
| `core/schemas.py` | All contracts + `ProjectState.save` / `.load` |
| `core/config.py` | Env vars (`ImageConfig`, `ChatConfig`, render mode) |

Artifacts for a project:

```
comic_out/<project_id>/
  source.txt
  state.json
  assets/portraits/
  pages/          # lettered pages
  pages/blank/    # unlettered cache
  panels/         # panel_compose only
```

### Comic domain

| File | Role |
|---|---|
| `core/screenwriter.py` | Structured extract / plan / reconcile |
| `core/comic/segmentation.py` | Chapter/token split; `merge_characters`; alias detect |
| `core/comic/identity.py` | L1 from appearance; evidence check; alias merge/dismiss |
| `core/comic/visual_bible.py` | Canon, stages (`Name@child`), locks, sanitize |
| `core/comic/consistency.py` | L1/L2/L3 engine |
| `core/comic/page_prompt.py` | Finished-page image prompt |
| `core/comic/page_lettering.py` | Overlay caption/dialogue/sfx |
| `core/comic/fonts.py` | CJK-capable font resolution |
| `core/comic/lettering_lang.py` | Source-language lettering hygiene |
| `core/comic/layout.py` | Legacy grid / webtoon compose |
| `core/comic/layout_diversity.py` | Layout catalog + anti-repetition |
| `core/comic/voice.py` | Speaker / timeline sanitize |
| `core/comic/key_beats.py` | Must-draw beat coverage |
| `core/comic/export.py` | PDF (optional `manga2pdf`) + webtoon PNG |
| `core/density.py` | Offline `inkstone plan` estimator (does not control generate) |
| `core/comic/coverage.py` | Legacy PageScript report |

### Surfaces

| File | Role |
|---|---|
| `core/cli.py` | `inkstone generate \| plan \| coverage`; `identity` is a stub |
| `core/cli_generate.py` | tqdm + credential checks; packaged entry (not `examples/`) |
| `web/server.py` | Local UI backend; jobs in memory; artifacts on disk |
| `web/index.html` | Tailwind SPA; GitHub Pages demo mode if `/api/health` is absent |
| `examples/generate_comic.py` | Thin wrapper around `cli_generate` |
| `scripts/start.sh` / `start.ps1` | One-click demo |
| `docs/guides/colab-cli.md` | Headless long jobs on Colab |

## Complexity hotspots

Approach these with the matching tests; they concentrate resume, LLM repair,
and identity logic.

1. **`core/pipelines/creative_comic.py`** (~1600 lines) — dual render modes,
   fingerprint invalidation, content-policy skip, progress. Tests:
   `tests/test_creative_comic.py`, `tests/test_finished_page_pipeline.py`,
   `tests/test_stage_lock.py`.
2. **`core/schemas.py`** (~1500 lines) — dirty LLM JSON coerce/repair; changing
   a field changes the tool schema. Tests: `tests/test_schemas*.py`,
   `tests/test_llm_payload_repair.py`.
3. **`core/comic/visual_bible.py`** — canon merge, stage refs, era/gender gates.
   Tests: `tests/test_visual_bible*.py`.
4. **`web/server.py`** — job lifecycle, Origin/body caps, review/regen.
   Tests: `tests/test_web_server.py`.
5. **`core/comic/identity.py`** — alias merge marks stale keys; metaphor-name
   (虎妞) must stay human. Tests: `tests/test_identity.py`,
   `tests/test_identity_metaphor.py`.

## Development workflow

```bash
conda create -n inkstone python=3.12 -y
conda activate inkstone
python -m pip install -e ".[dev]"
cp .env.example .env   # set AGNES_API_KEY
pytest                 # offline
ruff check .
ruff format --check .
```

- Branch from `main`: `feat/…`, `fix/…`, `docs/…`, `chore/…`.
- Conventional Commits. CI: ruff + pytest on Python 3.10–3.12.
- Agnes calls are async (`asyncio.to_thread` for blocking I/O).
- No silent failures: collect provider errors; never swallow them.
- Update [`ROADMAP.md`](ROADMAP.md) when implementation status changes.
- Put unapproved research in `.issue/`, not in versioned docs.
- Do not copy superseded constraints from `docs/superpowers/` (especially the
  old D2 PageScript gate) into new work.

## Current status (for handoff)

M1–M4 have shipped (providers, comic pipeline, long-form resume, OSS release).
Narrative-fidelity phases A–D (stage lock, layout diversity, voice/timeline,
key beats) are in tree. Source-fidelity evidence (`EvidenceQuote`) and
non-overwritable `face_lock` are on the consistency/fidelity line of work.

**Next approved product work** (see roadmap):

- **P0** — Make density a real contract; isolate or rename D1/D2 so they cannot
  be mistaken for release gates; keep README/CLI/docs consistent with shipped
  behavior.
- **P1** — Chapter-complete adaptation: `SourceUnit` → `NarrativeBeat` →
  beat-constrained storyboard/lettering → structural coverage. Validate on one
  public-domain chapter before a whole book.
- **P2** — CBZ, chapter reader, identity CLI, Journey-to-the-West showcase.

Long-form target architecture drafts under `docs/architecture/` may still be
local-only; until they are published, **roadmap + this file + released code**
are the source of truth.
