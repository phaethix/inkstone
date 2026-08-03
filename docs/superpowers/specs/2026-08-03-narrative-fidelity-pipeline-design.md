# Design: Narrative Fidelity Pipeline (post–bible_v3)

**Date:** 2026-08-03  
**Status:** Draft for sequential implementation  
**Repro / product ask:** `.issue/2026-08-03-15_04-review.md` (41-page 《一个陌生女人的来信》 review)  
**Depends on:** Visual Bible v1–v3 (`docs/superpowers/specs/2026-08-03-visual-bible-v3-design.md`), deferred lettering  

## §1 Problem (general, not novel-specific)

bible_v3 hardens era / gender / diegetic prop text. Review still fails on four orthogonal axes:

1. **Identity drift across pages** — same canon jumps hair color/length/age; stages exist but planner rarely emits `Name@stage`; refs are optional.
2. **Layout template collapse** — pages converge on “center full-body standing + corner closeups”; no anti-repetition.
3. **Voice + timeline muddle** — letter narration lands in the reader’s dialogue bubbles; present (reading letter) vs past (life story) share one visual grammar.
4. **Caption-only drama** — key plot beats are narrated in captions without being staged as drawable scenes.

Goal: fix these as **generic finished-page pipeline capabilities**. Any novel is a regression sample; no Zweig hardcoding in code.

## §2 Non-goals

- VLM post-hoc image QA loops (optional later).
- Full character-sheet image generation (Phase C stub remains deferred unless Identity phase needs a minimal sheet).
- Perfect literary fidelity of every beat (planner quality improves via structure + prompts, not human editorial).
- Pixel-editing existing comic_out PNGs (re-run regenerates).
- Replacing deferred lettering with in-image text.

## §3 Phased delivery (do in order)

```text
Phase A  Identity lock hardening
   ↓
Phase B  Layout anti-template
   ↓
Phase C  Voice + timeline layers
   ↓
Phase D  Monoscene / key-beat planner gate
```

Each phase: design slice (below) → unit tests → wire → fingerprint bump if render inputs change → mergeable PR. Do **not** start B until A lands; same for C/D.

---

### Phase A — Identity lock hardening

**Symptoms addressed:** R/heroine/child look like different people; age span flattened; wrong stage outfit/hair.

**Design:**

1. **Hair + age locks as first-class strings**  
   - Ensure every stage has non-empty `hair_lock` (already) and add optional `age_look: str` on `CharacterStage` (e.g. `about 13`, `about 41`) when reconcile provides it; sanitize fills from role/stage heuristics when blank (`child`→young, `adult`→middle-aged defaults are soft, not novel-specific).
2. **Action → stage resolution**  
   - `resolve_panel_stage_refs(plan, bible)`: if panel characters are bare canonical names and action/purpose/caption mention age cues (童年/少女/临终/写信/少年… + EN), rewrite to matching `portrait_key` / `Name@stage` when that stage exists.
3. **Forced portrait refs**  
   - `collect_finished_page_refs`: for every on-page character with a resolved stage portrait, **require** that path in refs (already prefers stage keys); fail soft-log if missing file after `ensure_stage_portrait_assets`.
4. **Prompt harden**  
   - Per character: `hair_lock` + `age_look` + “do not change hair color/length across panels for this identity unless stage changes”.
5. **Fingerprint**  
   - Bump identity token or bible version note (`identity_v3` or keep metaphor_v2 + bible hash already covers lock text). Prefer new `identity: "stage_lock_v1"` in render fingerprint when stage rewrite is active.

**Files:** `core/schemas.py`, `core/comic/visual_bible.py`, `core/comic/page_prompt.py`, `core/pipelines/creative_comic.py`, `core/screenwriter.py` (reconcile: fill age_look), tests.

**Exit criteria:** Unit tests: action cue → `@child` rewrite; page prompt contains hair/age lines; refs include stage portrait paths. Manual: re-run polluted project → fewer hair color swaps (best-effort).

---

### Phase B — Layout anti-template

**Symptoms addressed:** 41 pages share center full-body + letter-holding pose; no scene blocking.

**Design:**

1. **Layout catalog (enum-ish strings)**  
   - Document allowed `layout_intent` tokens: `splash_action`, `dialogue_grid`, `inset_memory`, `widescreen_scene`, `diagonal_motion`, `crowd_establishing`, `object_closeup`, … Planner must pick from catalog + free detail.
2. **Anti-repetition memory**  
   - When planning page N, pass last K pages’ `layout_intent` + dominant `shot` summary; instruct “do not reuse the same layout_intent as the previous page; avoid consecutive full-body standing hero shots.”
3. **Pose banline in page prompt**  
   - `ANTI_CENTER_STANDEE_LINE`: forbid repeating centered full-body standing character holding a letter/prop as the page hero if `layout_intent` is not splash; prefer environment + action staging.
4. **Light validation**  
   - Soft sanitize: if ≥3 consecutive pages share identical `layout_intent`, append needs_review or bump a warning in state (no hard fail in v1).

**Files:** `core/screenwriter.py` (`plan_comic_pages`), `core/comic/page_prompt.py`, optional `core/comic/layout_diversity.py`, tests.

**Exit criteria:** Prompt contains anti-standee line; planner user message includes prior layout_intents; unit test for diversity helper.

---

### Phase C — Voice + timeline layers

**Symptoms addressed:** Letter-writer narration in R’s bubbles; present/past visual mush; living child drawn for death beat (partially identity, partially timeline).

**Design:**

1. **Schema**  
   - `PagePanelSpec.speaker: str = ""` — empty = narrator/unspecified; character name for dialogue.  
   - `ComicPagePlan.timeline: Literal["present","past","liminal",""] = ""` (page-level default).  
   - Optional `PagePanelSpec.timeline` override.
2. **Planner rules**  
   - Narration / letter first-person / time-place → `caption`, `speaker=""`.  
   - Spoken lines → `dialogue` + `speaker` = speaking character (never attribute letter-writer I-voice to letter_reader).  
   - If bible has `letter_writer` / `letter_reader`, inject into plan prompt.
3. **Sanitize lettering voice**  
   - If `dialogue` text looks like first-person letter confession and panel characters are only the reader, move to `caption` (heuristic) or strip speaker.
4. **Timeline visual grammar in page prompt**  
   - `present`: cool/static interior, reading frame.  
   - `past`: warmer / slight grain / clear “memory” staging; no letter-holding unless action requires.  
   - Inject when timeline set.
5. **Deferred lettering**  
   - Overlay unchanged (caption vs dialogue chrome); speaker may inform placement notes later — not required for C v1.

**Files:** `core/schemas.py`, `core/screenwriter.py`, `core/comic/page_prompt.py`, `core/comic/lettering_lang.py` or new `voice.py`, fingerprint, tests.

**Exit criteria:** Schema round-trip; sanitize moves mis-attributed letter voice; prompt includes timeline lines.

---

### Phase D — Monoscene / key-beat planner gate

**Symptoms addressed:** Opera box / death candles / doorbell / paying insult only exist as captions.

**Design:**

1. **Beat extraction (LLM tool, once per project or per chunk)**  
   - `extract_key_beats(chunk|whole) → list[{beat_id, summary, must_draw, characters, setting_hint}]` capped (e.g. ≤12/chunk or ≤24/project). Generic: “dramatizable turning points,” not a fixed Zweig list.
2. **Coverage on `ComicPagePlanSet`**  
   - Each page may declare `covers_beats: list[str]`.  
   - After planning a chunk, if any `must_draw` beat uncovered → one repair retry with “stage these beats as panels/pages, not captions only.”
3. **Page prompt**  
   - When `covers_beats` set, require the panel action to depict the beat physically (environment + body), not standing-with-letter metaphor.
4. **Non-goal for D v1:** human-authored beat lists; novel-specific 8 monoscenes stay in review docs as **manual QA checklist**, not code constants.

**Files:** `core/schemas.py`, `core/screenwriter.py`, `core/pipelines/creative_comic.py`, tests.

**Exit criteria:** Uncovered must_draw triggers retry in fake chat test; covered beats appear in plan JSON.

---

## §4 Fingerprint / resume policy

| Phase | Fingerprint change |
|-------|-------------------|
| A | `identity: "stage_lock_v1"` (or additive key) |
| B | `layout: "anti_template_v1"` |
| C | `voice_timeline: "v1"` |
| D | `beats: "v1"` |

Sanitize/resume soft-invalidates when tokens change (existing pattern).

## §5 Testing strategy

- Unit tests per phase (fakes, no Agnes).  
- Update fingerprint tests when tokens bump.  
- Manual regression: re-run `a069a5c667bd` (or successor) after A+B at minimum; full A–D before calling adaptation “publishable.”

## §6 Mapping to review §八

| Review priority | Phase |
|-----------------|-------|
| 1 Lock character sheets / hair / age stages | A |
| 2 Storyboard first; ban standee template | B (+ planner in D) |
| 3 Lettering post (already deferred) | done; C hardens voice |
| 4 Period wardrobe table | done in bible_v3; A reinforces stage outfits |
| 5 Eight monoscenes | D generic beats + manual QA checklist |
| 6 Dual timeline visuals | C |

## §7 Implementation order for agents

1. Land Phase A PR.  
2. Land Phase B PR.  
3. Land Phase C PR.  
4. Land Phase D PR.  

Do not combine phases in one PR unless trivial coupling requires it.
