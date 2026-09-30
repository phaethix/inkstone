# Phase 0g — Minimal Reclaimer (`prune`)

## Status

Design. Approved direction from the content-addressed pipeline design, §7 and §9
(phase table row **0g**, resolved items 3 and 22 in §13).

## Problem

The CAS grows without bound, and Phases 0–3 produce no reclamation path at all.
`render` is the entry point that writes image bytes, so the reclaimer must ship
before Phase 3 rather than being deferred to Phase 4 (`gc`). This phase implements
only the already-settled policy; it decides nothing new:

> Delete only when the reference count is zero **and** the object is older than a
> threshold. (§13 resolved item 3)

Both conditions are required. Age alone can delete the only copy of an expensive
page; reference counting alone cannot see an orphan whose reference was never
recorded (the crash-after-write-before-manifest path, §8 crash table).

## What exists today (grounding)

The CAS and `index/` manifests do **not** exist yet; they arrive in Phases 2–3.
Every artifact today is written at a deterministic path and **overwritten in
place** on regeneration:

- `panels/id-{sha256(state_key)}.png`
- `assets/portraits/id-{sha256(name)}.png`
- `pages/page_c{ci:04d}_p{idx:04d}.png`
- `pages/blank/page_c{ci:04d}_p{idx:04d}.png`

Because paths are overwritten rather than versioned, the reachable set is exactly
the set of paths the live state still references. Orphans are produced when a
state entry disappears while its file remains:

- a character is renamed or alias-merged (`id-{hash(old_name)}.png` is orphaned);
- a chunk is re-planned with fewer panels or pages;
- a superseded page plan left a `page_c*_p*.png` behind.

`_reconcile_state()` already drops state entries whose **files** are missing, but
nothing performs the reverse — files whose **entries** are gone are never freed.

This phase therefore implements the settled policy over today's real reclaimable
bytes. The literal CAS target arrives later; the root-set abstraction built here
is what Phase 4's `gc` extends, not replaces.

## Design

### Deletion condition

A candidate file is reclaimable when all hold:

1. it lives inside the project's `output_dir` (containment check, reuse the
   existing `_is_within` semantics);
2. it is one of the artifact globs — `panels/*.png`,
   `assets/portraits/*.png`, `pages/*.png`, `pages/blank/*.png` — so nothing
   outside the asset set is ever touched (`comic.pdf`, `webtoon.png`,
   `state.json`, `consistency.json`, logs are structurally out of scope);
3. its path is **not** in the live root set (reference count zero);
4. its mtime is older than `now − N days` (age beyond the threshold).

Condition 3 is the reference count; condition 4 is the age dimension. Both are
required, exactly as resolved item 3 states.

### Root set (live references)

The root set is every artifact path the authoritative state still points at:

- `state.generated.panels[*].local`
- `state.generated.portraits[*]`
- `state.generated.pages[*].local` and `[*].blank_local`
- `state.characters[*].portrait_local`
- `consistency.json`: `characters[*].reference.path`

The ledger is included because it is authoritative product intent and one of
`gc`'s explicit root sets (§8 invariant 6d); omitting it would let a single prune
delete the only copy of a character's reference image. Paths are normalised
(`Path.resolve()`) before comparison so a relative-vs-absolute mismatch cannot
cause a false "unreferenced" verdict.

### Safety rules

- Dry-run is the default; deletion requires an explicit `--apply`. This makes
  "dry-run first" the path of least resistance and satisfies resolved item 22's
  `--dry-run` requirement. `--dry-run` is also accepted explicitly as a no-op
  alias.
- A file is deleted only if a prior plan listed it, and the plan is computed in
  the same invocation (`--apply` re-plans first, then deletes; it does not trust
  a stale list).
- Deletion is a plain `Path.unlink()` on already-resolved, contained paths; no
  directory is removed, so empty asset dirs remain harmless.
- If `state.json` is absent, the run fails loudly rather than treating everything
  as unreferenced — an absent state must never authorise a mass delete.

### Command shape

`inkstone prune --older-than <N>d [--dry-run | --apply]`

- `--older-than` is required and accepts `d` (days) or `h` (hours) suffixes;
  a bare integer means days.
- `--dry-run` (default): print the plan — count, total bytes, per-file paths —
  and delete nothing.
- `--apply`: perform deletion and report deleted count and reclaimed bytes.

Output is human-readable and mirrors the existing CLI's plain style.

## Components

- `core/comic/prune.py`:
  - `collect_live_refs(output_dir, state, ledger) -> set[Path]` — the root set.
  - `plan_prune(output_dir, older_than_days, now=None) -> PrunePlan` — read state
    and ledger, enumerate candidates, return the reclaimable set with sizes.
  - `apply_prune(plan) -> PruneResult` — delete and total the reclaimed bytes.
  - `parse_older_than(text) -> timedelta` — parse `Nd` / `Nh` / bare integer.
- `core/cli.py`: a `prune` subcommand wired to the module, matching the existing
  `identity` subcommand pattern.

`PrunePlan` and `PruneResult` are small dataclasses; the plan carries
`(path, size_bytes, mtime)` records so a dry-run and an apply report identically.

## Error handling

- Unparseable `--older-than`: exit non-zero with a usage message.
- Missing `state.json`: exit non-zero, delete nothing.
- A corrupt `consistency.json`: fall back to an empty ledger via
  `ConsistencyLedger.load_or_rebuild` semantics; the state roots still protect
  every file a live character references, so a corrupt ledger cannot authorise a
  wrong delete.
- A file that vanishes between planning and deletion: ignore the `FileNotFoundError`
  and continue (idempotent).

## Testing

TDD, `tests/test_phase0g_prune.py`:

1. an unreferenced, old file in `panels/` is planned and (on apply) deleted;
2. a referenced file is never planned, even when old — for each root source
   (panel, portrait, page, blank page, `portrait_local`, ledger reference);
3. a recent unreferenced file is not planned (age threshold);
4. `--dry-run` reports but deletes nothing (byte-for-byte file still present);
5. a file outside the asset globs (`comic.pdf`, `webtoon.png`) is never planned;
6. `parse_older_than` accepts `7d`, `12h`, `3` and rejects garbage;
7. a missing `state.json` deletes nothing and raises/returns an error;
8. containment: a root-set path outside `output_dir` does not protect, and does
   not cause, an out-of-tree delete;
9. idempotence: applying twice reclaims zero bytes the second time.

Plus a CLI test asserting `prune` with no `--apply` leaves files on disk.

## Non-goals

- No CAS, `index/`, or manifest awareness — those do not exist yet (Phases 2–3).
- No `supersedes` chain handling; in-place overwrite means no superseded bytes are
  retained, so there is nothing to audit-protect yet.
- No cross-project or global reclamation; `prune` operates on one project's
  `output_dir`, consistent with every other command.
- No automatic scheduling; the operator runs it, as resolved item 22 implies.

## Seam for Phase 4

`collect_live_refs` is the single place that names the root set. When manifests and
the CAS land, `gc` extends it with every manifest's `outputs`, the gate's pending
artifacts, and every non-`ok` manifest's `supersedes`, and switches the candidate
enumeration from "files in asset dirs" to "objects in the CAS store". The
retention rule (refcount zero and age beyond threshold) and the dry-run-first CLI
carry over unchanged.