# Manifest-Backed Artifact Projection Design

**Date:** 2026-10-18  
**Status:** Draft for review  
**Parent:** [Content-Addressed Execution Pipeline](../../architecture/2026-09-28-content-addressed-pipeline-design.md)

## Goal

Make already-keyed finished-page render, lettering, and export artifacts recoverable from manifests, while keeping `ProjectState` and existing CLI/Web readers compatible. In this slice, manifests and CAS bytes are authoritative for finished-page artifact outputs. `state.json` continues to hold planning caches and human decisions; only its finished-page artifact fields become a rebuildable projection.

## Scope

- Reuse and validate manifests for `render.page`, `letter`, `layout`, `webtoon`, PDF, and CBZ outputs.
- Add one projection boundary for `ProjectState.generated.pages` and `ProjectState.pages_done`, derived from valid finished-page render and letter manifests.
- Restore exported files from manifests at their existing deterministic paths; export paths are returned by the pipeline and are not added to `ProjectState` in this slice.
- Keep the existing two-pass `plan` / `render` orchestration and current stage-specific key derivation.
- Preserve positional output paths expected by the CLI, Web UI, export code, and legacy project readers.
- Preserve human-authored and control state such as alias decisions, `needs_review`, `continuity_review`, `stale_pages`, page tombstones, and the consistency ledger.
- Keep source, extraction, beat, storyboard, and page-plan caches in `state.json` for this slice.
- Preserve the single-writer project lock and atomic manifest writes.

## Out of scope

- Extracting the general DAG scheduler, composition root, or provider ports.
- Moving plan-side caches or human state out of `state.json`.
- Removing the old fingerprints or `_PIPELINE_STATE_VERSION`.
- Changing action-key semantics, render prompts, reference selection, or quota/gate policy.
- Running provider calls during projection rebuild or verification.
- Automatically deleting legacy paths or CAS objects during projection.

## Data flow

1. A stage computes its existing action key and looks up the matching manifest.
2. A valid `ok` manifest resolves each named CAS object by content hash; the pipeline restores the corresponding existing deterministic output path from those bytes.
3. A rejected/non-ok manifest follows the stage's existing pause/tombstone behavior and never triggers an implicit provider call.
4. After manifest resolution or a successful stage write, the projection boundary updates `generated.pages` and `pages_done` from the resolved finished-page artifact records.
5. When a source or plan invalidates an artifact key, only the affected generated-page projection entry is removed or replaced. Human decisions, stale markers, tombstones, and unrelated stage caches survive.
6. Layout and export manifests restore their own deterministic output files and do not independently mark source pages complete.
7. `verify` remains read-only and checks manifest/CAS consistency directly; projection reconstruction is separate and does not repair a corrupt store.

## Compatibility and failure handling

- Existing projects without manifests continue through the current legacy cache/fingerprint path. Legacy file reuse remains governed by the existing fingerprint and path checks until a valid manifest exists for that artifact; it must not be silently reclassified as a manifest hit.
- A manifest is a hit only if its key and stage match the index entry, every required output exists in CAS, and each content hash verifies.
- Projection writes use the existing atomic `state.json` save path. A crash after artifact persistence but before projection persistence is recovered by replaying valid manifests into `generated.pages` and `pages_done`.
- A corrupt manifest or missing CAS object must not produce a successful projection entry. `verify` reports the defect; normal execution treats the artifact as unavailable and follows the current safe miss path.
- Projection reconstruction performs no provider calls and does not delete or rewrite manifests.

## Acceptance criteria

1. A second identical run restores finished-page render, lettering, and export outputs from manifests/CAS and issues no provider calls for those hits.
2. Removing one `generated.pages` entry and its matching `pages_done` key, then rerunning, reconstructs both from valid manifests without provider calls.
3. Deleting or corrupting a referenced CAS object never reconstructs a completed page and remains visible to `verify`.
4. Replanning one page removes or replaces only that page's derived projection; other pages and all human decisions remain intact.
5. Legacy projects still resume under their current fingerprint path, with no unconditional full-book redraw.
6. CLI and Web progress readers continue to observe the same `ProjectState` fields and deterministic output paths.
7. Projection reconstruction is idempotent and performs no network/provider calls.

## Risks

- Some manifests represent multi-page outputs (collages and exports); they must not mark source pages complete on their own.
- `stale_pages` and tombstones affect whether a page may be reused but are not derived from successful artifact outputs; projection reconstruction must preserve them.
- Legacy fallback can become a second source of truth. It remains active only for artifacts with no valid manifest and uses the existing fingerprint/path rules.
