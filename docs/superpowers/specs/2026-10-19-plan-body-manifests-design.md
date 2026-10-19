# Plan-Body Manifests Design (Migration Follow-Up)

**Status:** Accepted · **Date:** 2026-10-19 · **Depends on:** the state split
(`2026-10-19-state-split-design.md`) and the content-addressed pipeline design
(`docs/architecture/2026-09-28-content-addressed-pipeline-design.md`, §4, §8).

## Goal

Make every billable planning stage's *body* recoverable from its manifest, so
that losing `cache.json` costs nothing: no chat call, no image call, and the
reloaded state equals the state before the loss.

The state split left one sentence aspirational: "the chat-stage cache bodies
are still recomputed by re-planning, not from manifests" (state-split spec,
Out of scope). This design closes that gap. After this slice, `cache.json` is
a hot copy — every heavy field in it can be rebuilt from verified manifests
plus the light checkpoint:

| `cache.json` field | Stage manifests that rebuild it |
| --- | --- |
| `chunk_cache.elements` | `extract` |
| `chunk_cache.storyboard` | `storyboard` |
| `chunk_cache.page_script` | `page_script` |
| `beat_cache` | `beats` |
| `page_cache` | `page_plan` |
| `visual_bible` (+ `bible_action_keys`) | `bible` |

`generated.*`, `pages_done`, `chunks_done`, and the ledger were already
projectable; this closes the last unbacked bodies.

## Today

- The six plan stages compute an action key that covers every input
  (`stage_src`, env, chunk text, upstream keys, bounded history) and cache
  their body in one of the four heavy caches.
- On a cache miss — `cache.json` deleted or a drifted key — the pipeline calls
  the provider again. Deleting `cache.json` therefore costs one chat call per
  stage per chunk, and because a re-called planner may answer differently, the
  recomputed render keys may drift and cascade into repaints.
- Render-side bodies (`render.page`, `letter`, `export`, `layout`) already
  write manifests and restore from them; the chat stages do not.

## The contract

For each of the six stages, **the manifest at the action key holds the stage's
raw output**, and **a verified hit at that key replaces the provider call**:

1. **Write (fresh call only).** After the provider call succeeds, `record_ok`
   stores the body as `<stage>.json` at the action key, with `stage`,
   `stage_src`, `env`, and the key's input digests. The task-report-only
   exception is the page-script policy rejection, whose stored empty script is
   also the cached body.
2. **Restore (cache miss only).** Where the pipeline would charge and call, it
   first tries `stored_output(root, key, stage=...)` — an `ok` manifest whose
   every referenced CAS object verifies. A hit is used exactly where the call's
   return value would go: same tail, same stamps, same `state.save`, same
   `_report`. A hit is **not** charged (`_charge` is skipped, so the budget and
   the audit binding are untouched) and is **not** written back (no manifest
   rewrite outside the rewrite entry point, §8).
3. **Restore failure is a miss.** A missing, non-`ok`, unverifiable, or
   unparsable manifest falls through to the provider call, so the run is never
   blocked by a bad artifact.

Bodies per stage:

| Stage | Body model | Cache field it refills |
| --- | --- | --- |
| `extract` | `StoryElements` | `chunk_cache[key].elements` (+ key stamp) |
| `bible` | `VisualBibleReconcileResult` (the raw reconcile) | `visual_bible` via `apply_reconcile` replay |
| `beats` | `KeyBeatSet` | `beat_cache[key]` (+ key stamp) |
| `page_plan` | `ComicPagePlanSet` (post-rewrite, stamped) | `page_cache[key]` |
| `storyboard` | `Storyboard` | `chunk_cache[key].storyboard` (+ key stamp) |
| `page_script` | `PageScript` | `chunk_cache[key].page_script` (+ key stamp) |

Notes:

- **Extract restores are not "fresh".** `fresh_extract` stays `False` for a
  restored extract: the chunk did not newly pay for extraction, so the bible
  stage does not treat it as a reason to re-reconcile.
- **The bible replays the raw reconcile**, not the resulting bible: the
  reconcile's side effects (merges, stage links, review rows) are the stage's
  output too, and `apply_reconcile` is a pure function of `(state, result)`.
  Replayed merges are idempotent where it matters: `merge_character_alias`
  no-ops for an already-folded alias, and `offer_alias_suggestion` dedupes
  against `dismissed_aliases` / `merged_aliases` / `needs_review`.
- **The page-plan manifest stores what the cache stores**: the post-rewrite,
  action-key-stamped plan set. Restore runs the same tail, so the rebuilt
  cache body is byte-equal to the deleted one.
- **Storyboard bodies are stored before `assign_panel_beat_ids`**, which the
  restore tail re-applies — same final cached body on both paths.

### The bible stamp rule

`bible_action_keys` is a light stamp map: "this chunk's reconcile is already in
`visual_bible`". It is meaningful only together with the bible body, so:

- **If the bible body is absent, the stamps describe nothing.** At run start,
  when `state.visual_bible is None` and stamps are present, the stamps are
  cleared. Each chunk then reproves its reconcile: restore the manifest at
  `bible_key`, or call.
- **A chunk with `needs_bible` false and no stamp** (post-clear chain rebuild,
  or a legacy state) first tries its manifest; a hit replays it (rebuilding the
  chain chunk by chunk), a miss keeps today's stamp-only tolerance — the
  current bible is trusted, exactly as before this slice.
- **A chunk with `needs_bible` false and a matching stamp** does nothing, as
  today: the body already includes the reconcile.

### The invalidation guard

The bible apply block currently wipes render records when
`prev_hash is None and had_render_assets` — a guard against assets made under a
*bible that had to be rebuilt from nothing*. A **restored** reconcile is exact
(the key covers the inputs), so that branch is suppressed when the reconcile
came from a manifest; every other invalidation (`sanitized`, a changed content
hash) fires on both paths, because the replay must reproduce the original
run's transition sequence. A reconcile that was actually re-called keeps the
guard: a rebuilt bible still invalidates assets it cannot prove.

## What does not change

- No key, fingerprint, `ProjectState`, `cache.json`, or `consistency.json`
  schema change. The refactor of each key function to share its inputs helper
  is value-preserving and pinned by the existing key tests.
- `cache.json` is still written and still the fast path; the manifests are the
  recovery source.
- The corrupt-`cache.json` load still refuses. "Restore instead of refusing" is
  a later question.
- Chat stages get no rejection tombstones: `skipped_chunks` already records a
  content-policy rejection in light state, and it is checked before the stage.
- `gc` already keeps every manifest output id; chat bodies ride the existing
  root set.

## Failure handling

- Provider failures keep today's behavior: logged, stage skipped or chunk
  skipped, no manifest written (nothing was produced).
- A manifest written for a reconcile whose `apply_reconcile` then failed stays
  in place; the next run restores it and re-applies, without a call.
- Partial loss: a chunk whose manifest is missing while the bible body was
  lost keeps the stamp-only tolerance above (its contribution may be absent
  until a later reconcile). A resume after a full loss is exact.

## Acceptance criteria

1. A finished-page run writes verified `ok` manifests at the recomputed keys
   for `extract`, `bible`, `beats`, and `page_plan`, with the stage's
   `stage_src` and the chat env.
2. A panel-compose run writes the `storyboard` manifest; with
   `INKSTONE_PAGE_SCRIPT=1`, the `page_script` manifest as well.
3. Delete `cache.json` and re-run with counting fakes: zero chat calls, zero
   image calls.
4. After that resume, `chunk_cache`, `beat_cache`, `page_cache`,
   `visual_bible`, and `bible_action_keys` equal the pre-deletion snapshot.
5. The resume does not wipe render records: `pages_done` and
   `generated.pages` are unchanged by the restored bible replay.
6. A corrupted (unverifiable) body falls back to the provider: the stage calls
   once and the run completes.
7. A project with no chat manifests (pre-slice) behaves as today: cache loss
   re-plans, billable.
8. Restores never charge: the zero-call tests assert provider counters, and the
   `_charge` sites are visible in the diff.
9. Full suite + `ruff check` + `ruff format --check` are green.

## Out of scope

- Per-stage runners; panels/portraits/export as manifest-backed bodies are
  already partly landed where applicable, and panel/portrait *records* remain
  light-state (keyed, not manifest-backed) for now.
- Removing `cache.json` or making a corrupt one recoverable.
- Migrating old projects: they gain manifests on their next successful run per
  stage; until then their cache loss behaves as today.
- Any change to what a key covers.

## Risks

- **Alias re-adds.** A restored extract still lists aliases folded in a past
  reconcile, and `merge_characters` re-adds unknown names. This is identical to
  today's re-extract path; the resume re-folds via the bible replay, and the
  alias queue dedupes. Not a regression, and not fully fixable without
  redefining extract semantics.
- **Replay non-idempotency.** The replay applies the original reconcile to
  post-reconcile light state. The acceptance tests compare full cache bodies
  and render records after a resume, which is where any divergence would show.
- **Manifest sprawl.** Six small JSON bodies per chunk join the index; they are
  tiny next to render PNGs and already gc-rooted.

## Revision history

- 2026-10-19 — initial design, accepted with the implementation plan
  (`docs/superpowers/plans/2026-10-19-plan-body-manifests.md`).
