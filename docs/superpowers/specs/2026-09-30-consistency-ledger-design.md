# Phase 0d: Consistency Ledger Design

> Design for §12's consistency ledger (`consistency.json`) and §4's resolution of
> item 17 (the ledger doubles as the authoritative L2 reference source).
> Prerequisite reading: the architecture doc §4, §6, §8 invariants, §12.

**Status:** approved direction; implemented as Phase 0d of the migration.

## Goal

Give the pipeline an explicit, authoritative record of **which pages must be
visually consistent with each other**, so that:

1. a character's page set is a ledger query, not a cache walk;
2. a superseded reference version makes its affected page set computable;
3. an alias merge lists the shared-character pages from the ledger instead of
   inferring them from caches;
4. the ledger is ready to become the L2 reference source (priority 2 in §4's
   table) once the CAS lands in Phase 1.

## Why it cannot be derived from artifacts

§5 excludes the L2 reference image from the `action_key`, and the price is an
invisible continuity break (§4). §6 makes `needs_review` authoritative human
state, but it only covers alias suggestions. Neither answers "which pages must be
visually consistent with each other" — that is product intent, not a function of
any upstream output. §12 invariant 9: **the ledger is not derived from artifacts
and is authoritative state at the same level as `needs_review`.**

## Scope boundary (what 0d does and does not do)

0d is scheduled to need **no CAS and no `index/`** (§9, §12). The ledger stores a
character's authoritative reference as a *path plus a content hash placeholder*;
the hash becomes a real CAS content hash only when Phase 1 introduces the CAS.
Therefore 0d delivers:

- the schema, storage, and lifecycle of `consistency.json`;
- runtime maintenance of the page set per character, derived from `page_cache`
  (the plans the pipeline already produced) — zero quota;
- the query surface: per-character pending page set for the UI, and a
  ledger-backed replacement for the alias merge's cache walk;
- the **decision** that the ledger is the tier-2 reference source, encoded as an
  explicit priority table with a fallback, so Phase 1 has nothing left to decide.

0d does **not** wire the ledger's reference bytes into image requests. That
requires the CAS and is Phase 1. Today's reference assembly keeps using
`CharacterAsset.portrait_local`; the ledger records the same path so the two
agree, and the priority table is the single documented place where the
substitution will happen.

## Data model

`consistency.json`, sibling of `state.json` (§6 layout):

```json
{
  "schema": 1,
  "characters": {
    "方鸿渐": {
      "pages": ["c0000-p0002", "c0001-p0005"],
      "reference": {
        "path": "assets/portraits/方鸿渐.png",
        "content_hash": null,
        "version": 1
      },
      "pending_pages": [],
      "updated_at": "2026-09-30T10:00:00Z"
    }
  }
}
```

| Field | Meaning | Source |
|---|---|---|
| `characters` | canonical name → ledger entry | bible canonical names |
| `pages` | positional page identities the character appears on | derived from `page_cache` |
| `reference.path` | the authoritative reference image's local path | `CharacterAsset.portrait_local` |
| `reference.content_hash` | CAS content hash; `null` until Phase 1 | reserved |
| `reference.version` | listed separately from the bible version, for auditing | incremented per regeneration |
| `pending_pages` | pages that may need redraw because the reference changed | set when a portrait is regenerated |
| `updated_at` | last mutation timestamp | `_now_iso` |

Three properties are load-bearing:

1. **Positional page identity only.** `pages` uses the 0b format
   `c{ci:04d}-p{idx:04d}`, never a model `page_id`.
2. **Canonical names only.** Entries are keyed by the bible canonical name, so an
   alias merge collapses into the kept character's entry (§12 rule 3).
3. **Not a derived cache.** It is loaded and saved like `state.json`, and a
   missing `consistency.json` is reconstructed from `page_cache` on the next
   maintenance pass rather than treated as an error.

## Components

- `core/comic/ledger.py` (new) — the `ConsistencyLedger` Pydantic model, atomic
  load/save, `rebuild_from_state()`, `pending_pages()`, `record_reference()`,
  `rename_character()`. No pipeline imports, so it stays testable in isolation
  and cannot introduce an import cycle with `identity.py`.
- `core/schemas.py` — `ConsistencyLedger` may live here instead if the model must
  serialize alongside state; decided in the plan. Either way, no `action_key`
  input is touched (invariant 10).
- `core/comic/identity.py` — `merge_character_alias` reads the ledger to list the
  shared-character page set, replacing its `page_cache` walk; it also rewrites
  the ledger key `new_name` → `keep_name`.
- `core/pipelines/creative_comic.py` — after each chunk's plans are cached and
  after each page renders, call `ledger.rebuild_from_state(state)` (idempotent,
  zero-quota) and save; after a portrait is (re)generated, call
  `record_reference(...)` which bumps `version` and fills `pending_pages`.
- `web/server.py` — `_state_snapshot` gains a `ledger_pending` aggregate
  (character → pending page set) so the UI can show breaks aggregated by
  character (§4 invariant 11), quiet by default.
- `core/cli.py` — `identity --view` (currently a D3 placeholder) prints the
  ledger: each character, its page count, and its pending page set.

## The reference priority table (decided here, executed in Phase 1)

§4's table, recorded in one place rather than left implicit in code:

| Priority | Source | When it applies |
|---|---|---|
| 1 | Bible sheet image | Always first when present |
| 2 | On-page characters' ledger reference | Resolved by the character's authoritative reference version |
| 3 | Adjacent page's blank | Only when `panel_continuity` is on and tiers 1-2 have not filled `max_refs` |

Corollary the design must honour: **tier 2 needs a fallback.** A character with
no authoritative reference yet (first generation, new character) leaves tier 2
empty, so assembly falls through to tier 3 or the sheet alone rather than
erroring. This is the most common case (a new character's first appearance) and
must not be a failure path.

## Invariants 0d must not violate

- `consistency.json` enters **no** `action_key` (§8 invariant 3, §12 invariant 10).
- The ledger is **not** derived state and must be in `gc`'s root set later
  (§8 invariant 6d); 0d records the reference path and hash field so Phase 4 can
  wire that without a schema change.
- Continuity flags are **per character**, quiet by default, and only escalate to a
  human when one character's pending pages exceed a threshold (§4 invariant 11).
  0d stores `pending_pages`; the escalation policy is 5b.

## Testing

TDD, zero quota, no network, no images:

- schema round-trip: dump → validate → equal;
- `rebuild_from_state` produces positional page sets from synthetic
  `page_cache` and is idempotent;
- a missing/corrupt `consistency.json` reconstructs rather than raising;
- alias merge lists the shared-character pages from the ledger and renames the
  key;
- `record_reference` bumps `version` and fills `pending_pages` with exactly the
  character's page set;
- the ledger's serialized keys never appear in any fingerprint or key computation
  (guarded by a test asserting a content change to the ledger does not change
  `render_fingerprint`).