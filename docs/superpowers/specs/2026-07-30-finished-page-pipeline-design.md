# Design: Finished-page comic pipeline (default)

**Date:** 2026-07-30  
**Status:** Draft for review (sections §1–§3 approved in chat)  
**Product ask:** Every bound page should read like a designed manga page (dynamic panels + in-art lettering), so LTR PDF page-flips feel like a comic — not a 2×2 collage of isolated shots.  
**Inspiration:** Conceptual only from `codex-novel-to-comic-studio` (whole-page finished output). **Do not** copy that repo’s agent/file layout, skill markdown contracts, or compose helpers. Inkstone stays automated, schema-first, and resumable.

## §1 Goals, non-goals, pipeline skeleton

### Goals

- Default output unit is one **finished A4-portrait comic page** (dynamic panel geometry + captions / dialogue / SFX rendered in-image).
- Bind finished pages directly into a left-to-right flip PDF.
- Keep Inkstone strengths: Pydantic tool schemas as single source of truth, provider injection, atomic `state.json` resume, honest skip on content-policy rejects.

### Non-goals (v1)

- Multi-agent / multi-approval markdown workflows.
- Human visual-bible approval gates as a required path.
- CBZ-first distribution (PDF remains primary; CBZ can follow later).
- Copying the reference project’s directory tree or `compose.py`.
- Promoting legacy D2 `PageScript` into the finished-page contract.

### Default pipeline

```text
segment → extract / portraits (keep)
  → plan pages (new: per-page story job + panel map + lettering)
  → render page prompt (deterministic from schema)
  → generate finished page (one image call per page; character refs)
  → bind PDF (page images in order)
fallback (explicit): per-panel generate + LayoutEngine lettering
```

### Differentiation vs the reference studio

| Studio pattern | Inkstone choice |
|---|---|
| Free-form director brief `.md` as primary artifact | Structured `ComicPagePlan` via forced tool call; prompt is a pure render |
| Heavy human approval checkpoints | Automated resume; optional UI mode switch only |
| Panel compose as rare fallback (simple grid) | Keep existing panel + `LayoutEngine` as **explicit** fallback / debug mode |
| Agent skill swarm | Single pipeline module + small pure helpers |

## §2 Data model and state

### New schemas (primary contract)

**`PagePanelSpec`** — in-page panel (planning only; not a separate image by default):

- `panel_id`
- `role`: establishing / action / reaction / inset / splash / …
- `shape_hint`: wide / tall / diagonal / inset / bleed (prompt geometry, **not** pixel boxes)
- `shot`, `action`, `characters`, `setting_ref`
- `dialogue` / `caption` / `sfx` (same language as source)
- `lettering_notes`: placement hints (avoid covering faces / key action)

**`ComicPagePlan`** — one page:

- `page_id` (stable, e.g. `p0003`)
- `purpose`: story job + page-turn hook
- `layout_intent`: manga geometry in natural language; **forbid** “2x2-only” plans
- `panels: list[PagePanelSpec]` (typically 3–6; splash allowed)
- `reference_characters` / `setting_refs` for L2 refs

**`ComicPagePlanSet`** — one planning unit (v1 aligned to current text chunks):

- `unit_id`
- `pages: list[ComicPagePlan]`

**`GeneratedPage`** — artifact:

- `local` path under `pages/`
- `page_id`, `unit_index`, `page_index`
- lettering snapshot from plan (for QC / fallback)
- `mode`: `finished` | `composed_fallback`

### `ProjectState` additions

- `page_cache: dict[str, ComicPagePlanSet]` (billable plan cache, like `chunk_cache`)
- `generated.pages: dict[str, GeneratedPage]`
- `pages_done` / `stale_pages` / `skipped_pages`
- `render_mode`: `finished_page` (default) | `panel_compose`
- `stage` extended through `page_plan` → `pages` → `export`

### Relationship to legacy models

- Keep `Storyboard` / `GeneratedPanel` / `LayoutEngine` for `panel_compose` and finished-page failure fallback.
- Do **not** elevate D2 `PageScript` / coverage into this path.
- Old projects (panel-era `state.json`) remain readable on the panel path; new runs default to finished pages. No forced in-place migration of old assets.

### Fingerprints

- `structure_fingerprint`: include page-plan semantics (and density later if wired).
- `render_fingerprint`: include finished-page mode, page size target, model snapshot.
- Switching `render_mode` soft-invalidates page/panel renders; cached plans may be reused when structure is unchanged.

## §3 Generation, consistency, fallback, export

### Page planning (chat, forced tool call)

Input: source unit text + extracted characters/settings (+ style).  
Output: `ComicPagePlanSet` validated by Pydantic.  
Cache under `page_cache[unit_key]` so resume does not re-pay for planning.

Schema / system reminders must require:

- dynamic panel geometry and reading path
- source-language lettering
- per-page purpose and page-turn hook
- no layout that is only a flat labeled `2x2` / `3x2` grid with no intent

### Prompt render (pure, offline)

`render_finished_page_prompt(plan, characters, settings, style) -> str`

Deterministically expands the plan into an image prompt: panel map, shots, exact lettering strings, text-safety rules, A4 portrait framing, style guide, identity locks from L1 descriptions.  
This function is the sole authority for the finished-page image prompt (same spirit as `ConsistencyEngine.build_panel_prompt` for panels).

### Finished-page image (default)

- One `generate_single_image` call per page.
- Prefer portrait sizes supported by the provider (e.g. `1024x1536` when available); normalize/bind at export if needed.
- References: portraits for `reference_characters` (+ critical setting refs when available). Reuse L1/L2; **no L3 face-swap on full pages** (seam risk).
- Write `pages/page_XX.png` and `GeneratedPage(mode="finished")`.
- Resume granularity: `page_id` (parallel to today’s panel keys).

### Failure policy

1. Content-policy reject → `skipped_pages` (honest; no blind retry loop).
2. Optional light QC (e.g. required dialogue substring missing from plan metadata only — **do not** OCR unless we later add it) → one stricter re-prompt.
3. Still failing, or `INKSTONE_RENDER_MODE=panel_compose` / `render_mode=panel_compose` → fallback: generate from `PagePanelSpec` list via existing panel pipeline + `LayoutEngine`, mark `mode="composed_fallback"`.

### Binding

- Default PDF = ordered finished (or fallback-composed) page images; LTR flip.
- Webtoon: stack page images, or keep panel-strip behavior under `panel_compose`.
- Default path must **not** assemble a 2×2 collage before export.

### Web / CLI

- Default `finished_page`.
- Progress should expose page completion, not only panel counts.
- Keep an explicit “panel compose” mode for A/B and recovery.

### Honesty

Free-tier image models have ceilings on in-image text and identity. Finished-page mode optimizes for **page-shaped comics**; it does not claim commercial print parity with stronger closed image2 stacks. Document this in README when shipping.

## Implementation sketch (not a plan yet)

Ordered work once this spec is approved and an implementation plan exists:

1. Schemas + state fields + fingerprint hooks + tests.
2. `plan_comic_pages` screenwriter tool + cache wiring.
3. `render_finished_page_prompt` + unit tests (golden-ish string contracts).
4. Pipeline branch in `creative_comic` for finished pages; env/config for mode.
5. Export bind from `generated.pages`; UI progress.
6. Fallback path + docs/ROADMAP honesty notes.

## Spec self-review

- [x] No TBD/placeholder sections left in approved scope.
- [x] No contradiction: finished-page is default; panel path is explicit fallback only.
- [x] Scope capped: no studio file tree, no required human bible gates, no D2 promotion.
- [x] Consistency: L3 excluded on full pages; L1/L2 retained.
- [x] Old projects: no forced migration.
