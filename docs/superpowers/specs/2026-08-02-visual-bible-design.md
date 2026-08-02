# Design: Project Visual Bible (character + color consistency)

**Date:** 2026-08-02  
**Status:** Approved in chat (approach B; C reserved as extension)  
**Repro:** `comic_out/93520df389e3`（《一个陌生女人的来信》）— fragmented identities (`R·` / `男人（被叙述者）` / `李先生` / …) and per-page color drift  
**Product ask:** Thoroughly fix (1) character consistency and (2) color unity; large change OK. Ship B first; leave hooks for C (visual sheet image).

## §1 Goals and non-goals

### Goals

- One **project-level Visual Bible** locks style, color palette, and canonical character identity for the whole run.
- Semantic aliases for the same person are **reconciled into a canonical character** (with optional age stages), not left as independent portraits.
- Finished-page and portrait prompts always carry **style + color bible + face/outfit locks**.
- Finished pages **always** attach portrait references for on-page characters (derive from `panel.characters` when `reference_characters` is empty).
- Bible changes bump render fingerprint so resume soft-invalidates portraits/pages honestly.

### Non-goals (phase B)

- Generating a composite color/character sheet image as an extra i2i reference (**phase C**).
- Turning on L3 face-swap by default.
- Blocking generation behind a mandatory human review wall (low-confidence aliases still go to `needs_review`).
- CV-based recoloring or post-hoc color grading of finished pages.
- Rewriting the panel_compose path beyond sharing bible helpers (finished_page is the primary consumer).

## §2 Problem statement (verified on 93520df389e3)

| Symptom | Mechanism today |
|---|---|
| Same person looks different across pages | Extract creates new names each chunk; `detect_character_aliases` is string-only and never auto-merges; `needs_review` was empty while 14 near-duplicate identities accumulated |
| Outfit / hair / age drift within a page | `l1_prompt` rebuilt from loose appearance text; no outfit/face lock; no stage model |
| Color jumps page to page | `style_guide` comes from per-chunk extract; no project palette; each finished page is an independent generation |
| Missing portrait conditioning | Many page plans have `reference_characters=[]` even when panels list characters |

## §3 Approach

- **B (this spec):** Visual Bible in state + LLM reconcile + prompt/ref hardening + fingerprint.
- **C (extension only):** optional `sheet_ref_local` visual sheet image prepended to i2i refs. B leaves the field and collection hook; does not generate the sheet.

## §4 Data model

### `ColorSwatch`

- `name: str` — e.g. `ink_black`, `skin_warm`, `accent_rose`
- `hex: str` — `#RRGGBB`
- `usage: str` — short English purpose line

### `ColorBible`

- `palette: list[ColorSwatch]` — 4–6 swatches
- `lighting: str` — e.g. `soft even cel lighting, muted European period tones`
- `forbidden: list[str]` — e.g. `neon`, `hyper-saturated`, `photoreal skin`

### `CharacterStage`

- `stage: Literal["child", "teen", "adult", "elder", "default"]`
- `appearance: Appearance` — stage-specific body/outfit
- `outfit_lock: str` — short English lock for clothes/colors
- `hair_lock: str`
- `portrait_key: str` — key into portraits map (usually `canonical_name` or `canonical_name@stage`)

### `CharacterCanon`

- `canonical_name: str`
- `aliases: list[str]`
- `face_lock: str` — shared across stages (bone structure / eyes / distinguishing face traits)
- `palette_notes: str` — role-relative color constraints
- `stages: list[CharacterStage]` — at least one (`default` if age is irrelevant)
- `role: str`

### `VisualBible`

- `version: str` — pipeline token, start at `bible_v1`
- `style_guide: str` — **project-locked** English art direction (not replaced per chunk)
- `color: ColorBible`
- `characters: dict[str, CharacterCanon]` — keyed by `canonical_name`
- `sheet_ref_local: str | None = None` — **C extension**; always `null` in B
- `content_hash: str` — sha256 over style + color + face/outfit locks (for fingerprint)

### `ProjectState`

Add:

- `visual_bible: VisualBible | None = None`

Page / panel character strings may use `Name@stage` (e.g. `陌生女人@teen`). Resolvers strip the suffix for canon lookup and select the stage.

## §5 Identity reconcile flow

### When

1. **First bible:** after the first successful extract when `state.visual_bible is None`, before portraits.
2. **Per chunk:** after `merge_characters` yields `new_names`, run a light reconcile against the existing bible.

### Tool output (structured)

- `merges: [{alias, canonical, confidence, reason}]`
- `stages: [{name, stage, of_canonical, reason}]`
- `keeps: [{name, reason}]` — new independent canons
- `color_patches: [...]` — optional; default empty (palette stays stable)

### Auto vs human

- `confidence == "high"` (same person) → **auto** `merge_character_alias` + update canon aliases; drop alias portrait; rewrite names in `page_cache` / current plans.
- `confidence == "low"` → append `needs_review`; treat as keep for this run (no silent merge).
- Stage links do not create a second root character; they attach under `of_canonical`.

### Relation to string alias detection

- Keep `detect_character_aliases` as a cheap pre-hint list passed into the reconcile prompt.
- LLM reconcile is authoritative for semantic aliases (`R·` vs `男人（被叙述者）`).

### Failure

- On timeout / parse failure: log warning, skip merges for this chunk, keep existing bible; do not block image generation.

## §6 Prompt, references, fingerprint

### Prompts

`render_finished_page_prompt` and portrait generation inject:

1. `Style: {bible.style_guide}`
2. `Color bible:` palette hexes + lighting + forbidden
3. Per character: `face_lock` + stage `hair_lock` / `outfit_lock` + `palette_notes` (via canon-aware `ensure_character_l1`)
4. Hard line: `do not change hair color, outfit colors, or skin tone across panels unless action says costume change`

Chunk-local `elements.style_guide` may **initialize** an empty bible once; it must not override a locked bible.

### References (B)

- Finished page refs = portraits for every resolved on-page character (from `reference_characters` ∪ all `panel.characters`).
- Missing portrait → generate that canon/stage portrait before the page.
- Optional: previous page `blank_local` as continuity ref; **portrait slots win** if at the provider cap.
- C hook: if `sheet_ref_local` is set, prepend it as the first ref (unimplemented generator in B).

### Pipeline order (finished_page)

```text
extract → merge_characters → reconcile(visual_bible)
  → portraits(canon / stages)
  → plan_comic_pages (emit canonical names / Name@stage)
  → render prompt (style + color + locks) + refs
  → blank → letter
```

### Fingerprint

`_render_fingerprint` includes:

- `visual_bible: "bible_v1"`
- `bible_hash: visual_bible.content_hash`

Do **not** write `visual_sheet` in B (avoid useless invalidation). C may add `visual_sheet=v1` later.

Bible first create or `content_hash` change → `_soft_invalidate_render` (clear portraits/pages; keep structural caches; page plans may already have rewritten names).

## §7 Phase C extension points (no implementation in B)

| Hook | B behavior |
|---|---|
| `VisualBible.sheet_ref_local` | Always `None` |
| `build_visual_sheet(bible) -> Path` | Stub / no-op returning `None` |
| Ref collection | `if sheet_ref_local: refs = [sheet] + portraits` branch present but dead |
| Fingerprint `visual_sheet` | Omitted until C ships |

## §8 Migration (93520df389e3 and peers)

1. Re-run project with B code.
2. Missing bible → first-chunk reconcile builds bible and merges obvious aliases.
3. New fingerprint → portraits + pages regenerate under locked style/color.
4. No manual state edit required; optional: user can clear `pages/` / portraits if they want a clean tree before resume.

## §9 Testing

- Schema round-trip for `VisualBible` / stages / `Name@stage` resolve.
- Reconcile apply: high-confidence merge updates canon + calls alias merge; low-confidence → `needs_review` only.
- Prompt contains color hexes + face_lock for on-page metaphor-safe characters.
- Finished-page ref list non-empty when panels list characters even if `reference_characters=[]`.
- Fingerprint changes when `content_hash` changes; unchanged bible → stable hash.
- Sheet hook: with `sheet_ref_local` set in a unit test, it appears first in refs (C readiness).

## §10 Files (expected touch set)

- `core/schemas.py` — bible models + `ProjectState.visual_bible`
- `core/comic/visual_bible.py` (new) — build/reconcile/apply/hash/resolve stage
- `core/comic/identity.py` — canon-aware L1 / locks
- `core/comic/page_prompt.py` — inject style/color/locks
- `core/comic/consistency.py` — ref collection + sheet hook
- `core/screenwriter.py` — reconcile tool schema + prompts
- `core/pipelines/creative_comic.py` — wire order + fingerprint
- Tests under `tests/test_visual_bible*.py` (+ prompt/fingerprint updates)
