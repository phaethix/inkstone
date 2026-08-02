# Design: Visual Bible v2 Hardening

**Date:** 2026-08-02  
**Status:** Approved in chat (thorough fix for 9910f82a3873)  
**Repro:** `comic_out/9910f82a3873` — wrong merges (伯爵→R, 陌生女人→母亲), English prose as character names/portrait_keys, empty hair/outfit locks, character-sheet insets on finished pages, modern clothing, panels with empty `characters`  
**Depends on:** `docs/superpowers/specs/2026-08-02-visual-bible-design.md` (phase B)  
**Product ask:** Thoroughly fix identity + page visual consistency (option 2: identity + page constraints together).

## §1 Goals and non-goals

### Goals

- Stop illegal English-prose strings from becoming character names or `portrait_key`s.
- Block high-confidence merges across incompatible roles (mother≠daughter, count≠novelist, servant≠master).
- Require non-empty `face_lock` (face only), `hair_lock`, and period-aware `outfit_lock` on every canon/stage.
- Sanitize polluted project state on resume (demote prose names, undo bad aliases) and bump to `bible_v2`.
- Backfill empty `panel.characters` from refs/action so portrait refs attach.
- Forbid character-sheet / turnaround / multi-age collage on finished pages; reinforce period wardrobe in prompts.
- Soft-invalidate via fingerprint when hardening changes apply.

### Non-goals

- Phase C visual sheet image generation.
- Enabling L3 face-swap by default.
- Pixel-editing existing 9910 PNGs (re-run regenerates).
- Perfect narrative fidelity of every action shot (planner quality remains separate; this fix ensures identity/refs/locks).

## §2 Problem statement (verified on 9910)

| Symptom | Mechanism |
|---|---|
| 帝国伯爵 merged into R | Reconcile high-merge without role compatibility guard |
| 陌生女人 alias under 母亲 | Same — mother/daughter roles not blocked |
| English face paragraphs as CharacterAsset names | LLM used description as `portrait_key` / name; no rejector |
| Empty hair/outfit locks | Schema allowed blank; apply did not harden |
| face_lock includes clothes | No face-vs-outfit split in prompts/validation |
| Character turnaround insets on page | Finished-page prompt never forbade design sheets |
| Modern purple hoodie | Outfit locks empty; no period wardrobe hard line |
| Caption about skiing, art is writing desk | Panels with `characters=[]` → weak refs; planner drift (mitigate refs only) |
| Age collage (girl+adult+mother+R) | No anti-collage / stage discipline in page prompt |

## §3 Identity purge (pre/post reconcile)

### Illegal name detector

`is_illegal_character_name(name: str) -> bool` when any of:

- Length > 40 and contains ASCII letters heavily, or
- Matches description patterns (`,`, ` with `, `hair`, `expression`, `wearing`, `build`, etc.) as majority English prose, or
- Equals a known English portrait blurb style (comma-separated trait list ≥ 3 clauses)

Illegal names:

- Must not create a root `CharacterAsset` or canon key.
- If seen as alias/portrait_key, fold into the owning canonical (or drop).
- Existing assets with illegal names: on sanitize, merge portrait into canonical if linked, else quarantine (delete from `characters` after moving useful appearance into canon locks) and soft-invalidate that portrait path.

### Merge guardrail

Before applying `confidence=high` merge:

- Load roles of alias and canonical (from `state.characters` / canon.role).
- If `_roles_incompatible(role_a, role_b)` → force `low` (needs_review), skip auto-merge.
- Incompatible keyword pairs (either side, casefold):  
  `(母|妈|mother|widow)` vs `(女|孩|narrator|少女|女儿)` when both present as distinct role centers;  
  `(伯爵|count|工厂主|情人)` vs `(小说家|作家|novelist)` unless alias is clearly a nickname of the same person;  
  `(仆|butler|约翰)` vs `(主人|novelist|作家)` as person identity (servant is never the master).

Conservative: when unsure, demote to low.

### Lock requirements

On install/upsert canon and each stage:

- `face_lock`: facial features only (eyes, bone, age look) — strip outfit words if present.
- `hair_lock`, `outfit_lock`: non-empty; if blank, derive from `appearance` or from face string leftovers; if still blank, set safe period defaults from bible style (`early 20th century European period clothing` / dark hair).
- Reject / repair stages whose `portrait_key` is illegal; set `portrait_key` to `f"{canonical}@{stage}"` short form only.

### State sanitize (resume)

`sanitize_visual_bible_state(state) -> bool` (True if mutated):

1. Strip illegal-named characters into aliases of matching canon or drop.
2. Remove aliases that fail role-compatibility against their canon.
3. Ensure locks non-empty on all canons/stages.
4. Set `version = "bible_v2"` and refresh `content_hash`.
5. Caller soft-invalidates when sanitize returns True or version/hash changed.

## §4 Storyboard / page plan hygiene

### Character backfill

`backfill_panel_characters(plan, known_names) -> plan`:

- If a panel has empty `characters` but `reference_characters` or `action`/`purpose` mentions a known canonical/alias, append resolved canonical names (cap reasonable).
- Always union page-level `reference_characters` with on-panel characters after rewrite.

### Stage selection (light)

When rewriting names for render, if action text suggests age (`少女`, `童年`, `十三`, `临终`, `写信`), prefer matching stage portrait_key when present. Do not invent stages here.

### Anti multi-stage collage (prompt)

Page prompt hard line: do not depict multiple age versions of the same person in one page unless `layout_intent` explicitly calls for flashback split.

## §5 Finished-page prompt hardening

Inject when bible present (in addition to v1 color/locks):

- `NO character design sheets, turnarounds, model sheets, or multi-view reference collages inside the page.`
- `Period-accurate wardrobe only; no modern hoodies, sneakers, or athleisure unless action explicitly requires costume change.`
- Per-character outfit_lock + hair_lock lines (already via l1_from_canon; ensure locks filled).

## §6 Fingerprint

- Token: `visual_bible: "bible_v2"` (replace `bible_v1`).
- `bible_hash` still from content hash (now includes non-empty locks after sanitize).

## §7 Testing

- Illegal name detection true/false cases.
- Merge guard demotes mother↔daughter and count↔novelist.
- Apply/install fills empty locks; strips outfit from face_lock.
- Sanitize removes English-prose CharacterAsset keys.
- Backfill empties panel.characters from action/refs.
- Prompt contains anti-sheet and period wardrobe lines; fingerprint bible_v2.
- Preferred: unit tests only; no live Agnes calls.

## §8 Files (expected)

- `core/comic/visual_bible.py` — detectors, guards, sanitize, lock fill, backfill helpers
- `core/screenwriter.py` — stronger reconcile instructions (no prose names, no cross-role merges, required locks)
- `core/comic/page_prompt.py` — anti-sheet + period lines
- `core/pipelines/creative_comic.py` — sanitize on load/resume; backfill before render; bible_v2 fingerprint
- `tests/test_visual_bible_v2.py` (+ updates to fingerprint/prompt tests)
