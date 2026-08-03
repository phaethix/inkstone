# Design: Visual Bible v3 — Era / Gender / Diegetic Gates

**Date:** 2026-08-03  
**Status:** Approved (plan A)  
**Depends on:** `docs/superpowers/specs/2026-08-02-visual-bible-v2-hardening-design.md`  
**Product ask:** General pipeline fix for identity gender drift, period wardrobe anachronism, and diegetic prop gibberish — not novel-specific.

## §1 Goals and non-goals

### Goals

- Lock project-level `era` and per-canon `gender` + `narrative_function` as structured fields.
- Sanitize polluted state: infer gender/function when safe, repair historical outfits that contain modern streetwear tokens, bump to `bible_v3`.
- Era-conditioned wardrobe defaults (historical ≠ forced Vienna 1900s for every project; contemporary does not get early-20th-century defaults).
- Generation gates: portrait and finished-page prompts inject explicit gender, era wardrobe banlines, and diegetic-text ban (blank paper / abstract ink — no letterforms).
- Demote high-merges that conflict on gender or `letter_reader` ↔ `letter_writer`.
- Soft-invalidate via fingerprint when hardening mutates locks.

### Non-goals

- VLM / post-hoc image QA loops.
- Composition de-duplication or narrative pacing.
- Per-novel hardcoding (e.g. Zweig-only rules).
- Pixel-editing existing project PNGs (re-run regenerates).
- Phase C visual sheet generation.

## §2 Problem statement (verified generally)

| Symptom | Mechanism |
|---|---|
| Famous male character drawn as glamorous woman | No `gender` field; face_lock lacks sex; model defaults female |
| Hoodie / sneakers in period stories | Soft period prompt; outfit_lock may contain modern tokens; default outfit always “early 20th century European” |
| Contemporary novels forced into 1900s dress | Same hardcoded `DEFAULT_OUTFIT_LOCK` |
| Letter/book props full of gibberish glyphs | Deferred lettering bans chrome only; diegetic prop text still generated |
| Reader vs writer identity confusion | No `narrative_function`; merges/aliases can collapse distinct roles |

## §3 Schema

### VisualBible

- `era: str` — free-text era lock (`Vienna c.1900–1910`, `contemporary China`, `unspecified`, …)
- `era_forbidden_wardrobe: list[str]` — optional; if empty, derive from era class
- `version` → `"bible_v3"` after sanitize/apply hardening

### CharacterCanon

- `gender: Literal["male","female","nonbinary","unknown"]` (default `unknown`)
- `narrative_function: str` — `letter_reader` | `letter_writer` | `protagonist` | `love_interest` | `servant` | `parent` | `child` | `extra` | `""`

### VisualBibleReconcileResult

- Add `era: str` (and optional forbidden list if present on bible) so create/update can set project era once.

## §4 Sanitize and apply

`sanitize_visual_bible_state`:

1. Keep v2 illegal-name / role-alias cleanup.
2. Infer / fill `era` from existing `era` or `style_guide` heuristics; classify `historical` | `contemporary` | `unspecified`.
3. Per canon: infer `gender` and `narrative_function` when markers are clear; leave `unknown` / push `needs_review` when not (do not invent).
4. Prepend idempotent gender phrase to `face_lock` (`adult man,` / `adult woman,`).
5. Historical: rewrite outfit_locks containing modern tokens to era-safe defaults; fill blanks with era-derived outfit (not a fixed Vienna string when era text exists).
6. Contemporary: blank outfits get a neutral modern-casual default — **never** early-20th-century European.
7. Set `version = "bible_v3"`, refresh hash (payload includes era/gender/function/outfit).

`apply_reconcile` high-merge demotion also when:

- genders conflict (`male` vs `female`), or
- narrative functions are `letter_reader` vs `letter_writer`.

## §5 Prompt gates

- `l1_from_canon` leads with gender phrase when known; includes locks.
- Portrait path: gender + era wardrobe + “single human matching gender; no gender swap”.
- Page prompt when bible present:
  - Era-conditioned wardrobe banline (replace always-on early-20th line).
  - Per-character `identity: {name} ({gender}, {narrative_function})`.
  - When `lettering=deferred`: `DIEGETIC_TEXT_LINE` — letters/books/newspapers/signs show blank aged paper or abstract ink only; no letterforms / pseudo-script.

## §6 Fingerprint

- Token: `visual_bible: "bible_v3"`.
- `bible_hash` includes era + gender + narrative_function + locks.

## §7 Testing

- Gender inference + face_lock prefix; conflicting-gender merge demotion; letter_reader↔writer demotion.
- Historical outfit strip; contemporary does not force 1900s default.
- Page prompt contains gender/era/diegetic lines; fingerprint `bible_v3`.
- Sanitize bumps version.

## §8 Files

- `docs/superpowers/specs/2026-08-03-visual-bible-v3-design.md`
- `core/schemas.py`
- `core/comic/visual_bible.py`
- `core/comic/page_prompt.py`
- `core/screenwriter.py`
- `core/pipelines/creative_comic.py`
- `tests/test_visual_bible_v3.py` (+ fingerprint/prompt test updates)
