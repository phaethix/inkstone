# Manifest-Backed Artifact Projection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to execute this plan task-by-task.

**Goal:** Make manifests and verified CAS bytes the recovery source for finished-page artifacts while preserving existing `ProjectState` fields and output paths.

**Architecture:** Strengthen the shared CAS read boundary so a cache hit requires a matching manifest and byte hash. Centralize finished-page projection updates, then use the validated store in the existing plan/render loop and current export helpers. Keep legacy fingerprint recovery, existing stage keys, and two-pass orchestration intact.

**Tech Stack:** Python 3.10+, Pydantic, local filesystem CAS/manifests, pytest/ruff already configured in the repository.

## Global Constraints

- Preserve single-process, single-writer operation and atomic manifest writes.
- Keep action keys pure and keep budget, gate, ledger, and human decisions out of keys.
- Preserve page identity `c{ci:04d}-p{idx:04d}` and deterministic output paths.
- Do not move plan caches or authoritative human state out of `state.json` in this slice.
- A missing or corrupt manifest/CAS object must never become a successful projection entry.
- Keep `verify` read-only and ensure it does not reconcile or repair project state.
- Keep legacy artifact reuse governed by the current fingerprint/path checks when no valid manifest exists.

---

## File map

- Modify `core/comic/cas.py`: safe manifest-entry reads and verified byte resolution for single and multi-output manifests; reuse the same checks in `verify`.
- Create `core/comic/artifact_projection.py`: narrowly scoped operations that add or drop a finished-page projection (`generated.pages` and `pages_done`) without touching human/control state.
- Modify `core/pipelines/creative_comic.py`: pass expected stages into artifact reads and route finished-page projection updates through the new boundary; keep plan/render control flow.
- Modify `docs/ROADMAP.md`: record the landed artifact recovery slice without claiming that the general scheduler or state-cache migration is complete.

## Task 1: Verify manifest identity and CAS bytes at the shared read boundary

**Files:**
- Modify: `core/comic/cas.py`

**Interfaces:**
- Add `read_manifest_entry(root: Path, key: str, *, stage: str | None = None) -> Manifest | None`. It validates that JSON parses, the payload key matches the requested key and index filename, and the optional stage matches. Malformed or mismatched entries return `None` to cache readers.
- Add `verified_manifest_files(root: Path, key: str, *, stage: str | None = None) -> tuple[Manifest, list[tuple[str, bytes]]] | None`.
- Require `outcome == "ok"`, at least one output, and SHA-256 equality for every CAS object before returning output bytes.
- Change `stored_output(root, key, *, stage: str | None = None) -> bytes | None` to return the first verified output.
- Change `stored_files(root, key, *, stage: str | None = None) -> list[tuple[str, bytes]] | None` to return all outputs only when all verify.
- Keep `verify` strict: malformed entries, key mismatches, missing objects, and hash mismatches remain diagnostics, not cache misses.

- [ ] Implement one safe entry reader and one shared verified-output resolver.
- [ ] Make both stored-output helpers delegate to the shared resolver.
- [ ] Refactor `verify` to reuse the same identity/content checks while retaining diagnostics and read-only behavior.

## Task 2: Define the finished-page projection boundary

**Files:**
- Create: `core/comic/artifact_projection.py`

**Interfaces:**
- `record_page_artifact(state: ProjectState, page_key: str, page: GeneratedPage) -> None`: replace that page's generated record, clear that page's stale marker after successful completion, and add its key to `pages_done` once.
- `drop_page_artifact(state: ProjectState, page_key: str) -> None`: remove only the generated record and `pages_done` key for that page; it must not clear tombstones or human decisions.
- The module must not modify aliases, review queues, page plans, chunk caches, or the consistency ledger.

- [ ] Implement the two focused state mutations with stable ordering and idempotent behavior.
- [ ] Document that `generated.pages` and `pages_done` are derived output projection fields; stale markers clear only when the corresponding artifact is successfully recorded.

## Task 3: Integrate verified restoration with the existing finished-page path

**Files:**
- Modify: `core/pipelines/creative_comic.py`

**Interfaces:**
- Pass `stage="render.page"` to render manifest resolution, `stage="letter"` to lettering resolution, `stage="layout"` to collage resolution, and `stage="export"` to PDF/CBZ/Webtoon resolution.
- Use `read_manifest_entry` for cache-hit/tombstone checks so malformed entries cannot crash or be mistaken for a hit.
- Use `record_page_artifact` for CAS-restored finished pages and newly generated/lettered finished pages.
- Use `drop_page_artifact` only when a positional page disappears from the current plan; preserve stale markers until successful rerender.

- [ ] Preserve the current fast path when a valid local blank and current lettering already exist.
- [ ] On a render manifest hit, restore the blank, letter from its manifest or local computation as today, then write the finished-page projection once.
- [ ] If a manifest exists but any CAS byte is missing/corrupt or its stage does not match, treat it as unavailable; do not copy those bytes into project output paths.
- [ ] Keep policy rejection and budget/gate pause behavior unchanged.
- [ ] Confirm old states without manifests still follow their fingerprint/path migration behavior and do not trigger a book-wide redraw solely because this read boundary changed.

## Task 4: Record the migration boundary in the roadmap

**Files:**
- Modify: `docs/ROADMAP.md`

- [ ] Add a dated status note that finished-page artifact projection restores from verified render/letter manifests and export artifacts restore from verified manifests.
- [ ] State explicitly that the general scheduler, moving plan caches out of `state.json`, and deleting the legacy fingerprints remain separate work.

## Verification note

This implementation plan does not authorize adding or running tests. Review the changes statically and do not claim runtime or test verification in the completion report.
