# Design: Deferred lettering for finished pages

**Date:** 2026-07-31  
**Status:** Approved in chat (placement: planner boxes + heuristic fallback)  
**Source note:** `.issue/2026-07-31-10_14-deferred-lettering-cjk.md` (local, untracked)  
**Product ask:** Thoroughly fix (1) CJK glyph distortion on finished pages and (2) mixed Chinese/English lettering — without abandoning whole-page generation.

## §1 Goals and non-goals

### Goals

- Reader-visible caption / dialogue / sfx on finished pages are **rasterized with real fonts** (reuse `fonts.py` + LayoutEngine bubble/caption/sfx drawers).
- Image model output for finished pages is **art + empty lettering chrome only** (no readable glyphs).
- Lettering language matches the **source excerpt language** before any image or overlay work.
- Keep dynamic whole-page composition as the default product path.
- Resume can **re-letter** without re-paying the image API when a blank page already exists.

### Non-goals (this change)

- Computer-vision bubble detection.
- OCR-based QC of painted glyphs.
- Auto-switching entire projects to `panel_compose` based on CJK ratio.
- Commercial typesetting (tails, speaker attribution, kerning wars).
- Nesting `page_cache` into `ChunkCache`.

## §2 Problem statement (verified)

| Symptom | Mechanism today |
|---|---|
| Distorted Chinese on finished pages | `render_finished_page_prompt` asks Agnes to paint CAPTION/DIALOGUE/SFX in-image |
| Mixed CN/EN | Soft language reminders; art-direction English in the same image prompt; no post-plan language gate |

`panel_compose` already letters correctly via PIL. Finished-page mode must adopt the same **text authority** (schema strings + font rasterization), not the image model.

## §3 Architecture

```text
segment → extract / portraits (unchanged)
  → plan_comic_pages (+ language lock + lettering boxes)
  → render_finished_page_prompt (BLANK LETTERING)
  → generate page image → save blank under pages/blank/
  → letter_finished_page(blank, plan) → pages/page_*.png
  → bind PDF / webtoon
```

Hard rules:

1. **Never** pass lettering strings into the image model as text to paint.
2. **Always** overlay from plan fields after generation (or on resume).
3. Language lock runs on the plan **before** caching as final / before image.

## §4 Data model

### `LetteringBox`

Normalized page coordinates (origin top-left, unit square):

- `kind: Literal["caption", "dialogue", "sfx"]`
- `panel_id: str` — joins to `PagePanelSpec` for the text payload
- `x, y, w, h: float` — each in `[0, 1]`; `w,h > 0`; clamp on ingest so `x+w ≤ 1.05` etc. with soft clamp to page

Text is **not** duplicated on the box: overlay reads `panel.caption` / `.dialogue` / `.sfx` by `(panel_id, kind)`.

### `ComicPagePlan`

Add:

- `lettering_boxes: list[LetteringBox] = []`

Planner should emit one box per non-null lettering field. Missing boxes → heuristic fallback at overlay time (do not fail the plan).

### `GeneratedPage`

Extend:

- `mode: Literal["finished", "finished_lettered", "composed_fallback"]`
- `blank_local: str | None = None` — path to unlettered art
- `local` — reader-facing lettered (or composed) page

Default new finished-page outputs use `mode="finished_lettered"`. Legacy `mode="finished"` pages without `blank_local` remain loadable; re-run soft-invalidates render to regenerate under the new path.

### Fingerprints

Include a lettering-pipeline version token in `render_fingerprint` (e.g. `lettering=deferred_v1`) so upgrading this feature soft-invalidates old in-image-lettered pages without wiping `page_cache` / `chunk_cache`.

## §5 Language lock

### Source language

`source_lettering_script(text) -> Literal["cjk", "latin", "mixed", "unknown"]`

Reuse / extend `text_requires_cjk`:

- Prefer **cjk** when CJK ideograph ratio among letters is ≥ ~0.15 (or any CJK present and latin letter ratio low).
- Prefer **latin** when letters are overwhelmingly ASCII/Latin.
- `mixed` / `unknown` → do not auto-strip; still prefer not translating.

### Plan validation

For each non-null `caption` / `dialogue` / `sfx`:

- If source is `cjk` and field has letters but **no** CJK → **mismatch**.
- If source is `latin` and field is CJK-heavy → **mismatch**.
- Pure SFX punctuation / digits alone → allow.

On mismatch in `plan_comic_pages`:

1. Log warning with field samples.
2. **One** forced re-plan with a hard language reminder (mirror storyboard reminder strength).
3. If still mismatched: drop mismatched lettering fields to `None` (honest omission beats English on a Chinese book) and continue; do not infinite-loop.

Art-direction English (`action`, `scene_prompt`, `l1_prompt`) remains allowed and stays **out of** overlay text.

## §6 Blank image prompt

`render_finished_page_prompt(..., lettering="deferred")` default for finished_page:

- Require empty speech bubbles / caption bars / SFX space as chrome only.
- **Forbid** any readable characters (Latin or CJK) in the image.
- Still describe panel geometry, identity, style.
- Do **not** include `CAPTION (exact): …` / `DIALOGUE (exact): …` / `SFX (exact): …` glyph strings.
- May include placement hints from `lettering_boxes` / `lettering_notes` as geometry only (“empty bubble at upper-left of panel 2”).

`strict=True` retries keep blank rules (stricter about no glyphs), not “paint exact strings”.

## §7 Overlay (`letter_finished_page`)

New pure module `core/comic/page_lettering.py`:

```text
letter_finished_page(blank: Image, plan: ComicPagePlan, *, font_path=None) -> Image
```

Algorithm:

1. Build work list: for each panel lettering field that is non-null, resolve box from `plan.lettering_boxes` match `(panel_id, kind)` else **heuristic slot**.
2. Heuristic (deterministic): divide page into `N=len(panels)` vertical bands; place caption near top of band, dialogue mid-lower, sfx upper-side; clamp width ~0.55–0.8 of page width.
3. Convert normalized box → pixel rect; draw using extracted helpers shared with `LayoutEngine` (`_draw_caption` / `_draw_bubble` / `_draw_sfx`) to avoid drift.
4. Return composited RGB image.

Pipeline writes:

- `pages/blank/page_cXXXX_pYYYY.png` → `blank_local`
- `pages/page_cXXXX_pYYYY.png` → `local` (lettered)
- `mode="finished_lettered"`

Re-letter path: if `blank_local` exists and is within output dir, skip image API and only re-run overlay (e.g. after plan lettering fix on soft resume when blank retained).

## §8 Failure policy

| Failure | Behavior |
|---|---|
| Content-policy on image | `skipped_pages` (unchanged) |
| Unsupported size | existing 1024×1024 fallback |
| Language mismatch after retry | strip bad fields; overlay whatever remains |
| Overlay exception | fail the page visibly (raise); do not export half-blank as success without logging |
| `panel_compose` mode | unchanged; LayoutEngine letters as today |

## §9 Web / docs honesty

- README: finished-page default uses **deferred lettering** (model paints art; Inkstone paints text). Remove implication that in-image CJK is the quality path.
- ROADMAP: mark deferred lettering as the finished-page text strategy.
- Progress JSON can expose `mode` per page; no new required UI controls for v1.

## §10 Migration

- Old projects with in-image lettered `mode=finished` pages: soft-invalidate on fingerprint bump → regenerate blank + overlay.
- Do not force-migrate pixel files in place.
- `page_cache` plans without `lettering_boxes` remain valid; overlay uses heuristics.

## Spec self-review

- [x] No TBD in approved scope; CV detection explicitly out.
- [x] No contradiction with finished-page default; panel_compose intact.
- [x] Language lock + blank prompt + overlay + boxes/heuristics all assigned.
- [x] Resume blank reuse specified.
- [x] Honesty docs called out.
