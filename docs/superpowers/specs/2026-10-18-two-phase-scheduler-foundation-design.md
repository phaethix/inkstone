# Two-Phase Scheduler Foundation Design

**Date:** 2026-10-18  
**Status:** Draft for review  
**Parent:** [Content-Addressed Execution Pipeline](../../architecture/2026-09-28-content-addressed-pipeline-design.md)  
**Follow-up to:** [Manifest-Backed Artifact Projection](2026-10-18-manifest-backed-artifact-projection-design.md)

## Goal

Replace the implicit outer `for phase` / `for chunk` orchestration in `creative_comic` with an explicit, deterministic two-phase work graph. The graph owns ordering and phase expansion; the existing pipeline code continues to own stage behavior, action keys, cache reads/writes, provider calls, progress reporting, budget, gate, and state persistence.

This is a scheduler foundation. It does not complete the per-stage DAG migration or change any reuse decision.

## Current boundary

`creative_comic` currently plans every chunk before rendering any page or panel. It does this with nested loops over `("plan", "render")` and `chunks`. Each chunk visit contains a composite sequence of extraction, identity merge, bible reconciliation, optional portrait generation, beats, page plan, and—depending on mode—storyboard/page-script or page rendering. Page counts are known only after the plan pass. Page and panel work then uses positional identities, while stage-specific caches and action keys remain embedded in `creative_comic.py`.

The business effects are intentionally ordered: identity and bible state are shared across chunks; page rendering is sequential for continuity and gate behavior; budget/audit state is shared; and `state.json` is written at durable item boundaries. The foundation must preserve these constraints.

## Design

Add a small scheduler module that represents work as immutable nodes with a stable node id, phase, work kind, positional scope, and explicit predecessor ids. It validates unique ids, known predecessors, acyclic dependencies, and phase ordering. Ready nodes are selected deterministically by phase and positional order. The scheduler remains single-process and executes through existing pipeline runners under the project lock; it does not introduce new concurrency.

The initial graph is deliberately coarse at chunk-planning boundaries and fine at render boundaries:

1. A `plan_chunk` node represents the existing composite plan path for one chunk. Plan nodes retain current chunk order because character identity, visual-bible reconciliation, and historical planning inputs share state across chunks.
2. The plan phase is a hard barrier. No render node is eligible until all plan nodes have completed and their resulting page/panel counts are available.
3. The graph expands from those outputs. In `finished_page` mode it creates one render node per positional page. In `panel_compose` mode with panel continuity enabled it creates one node per positional panel. With panel continuity disabled it creates a deterministic panel-batch node per chunk; that executor keeps the existing semaphore and `asyncio.gather` behavior. Finished pages and continuity-dependent panels retain existing sequential order for references, sample-gate selection, quota reservations, and audit attribution.
4. Export nodes are added after render work and depend on the completed artifacts they bind. Existing export selection, action keys, restore behavior, and output paths remain unchanged.

The scheduler does not call providers or read/write project state. The pipeline remains the executor: it asks the scheduler for the next eligible node and dispatches to existing behavior. Node identity is scheduling metadata only and is not an `action_key`, manifest key, or persisted state key.

## Compatibility and constraints

- Preserve the public `creative_comic` signature and CLI/Web progress labels.
- Preserve action-key derivation, legacy fingerprint fallback, manifest semantics, output paths, positional page/panel identities, and `state.json` schema.
- Preserve single-writer behavior, current cache lookup order, cancellation checkpoints, tombstone behavior, per-item durability, and error propagation.
- Preserve the hard plan/render barrier and current render concurrency: finished pages and continuity-dependent panels execute in order; panel batches without continuity keep the existing bounded concurrency.
- Budget, gate, consistency ledger, human decisions, and audit state remain outside scheduler nodes and action keys.
- Plan-side caches remain in `state.json` for this slice. Removing legacy fingerprints and changing action-key semantics remain later work subject to the parent design's phase gates.
- The graph is ephemeral and is rebuilt on each run. It is not a second persistence or recovery source.

## Failure handling

Graph validation fails before any provider call or stage-executor mutation if a node id is duplicated, a predecessor is missing, a cycle exists, or a later-phase node is made eligible before its phase barrier. Existing preflight state loading and reconciliation remain unchanged. A stage failure retains current behavior: execution stops or records the existing rejection/pause outcome; the scheduler does not mark that node complete and does not advance dependent work. A rerun reconstructs the graph from source and current planning outputs.

Cancellation, budget pauses, content-policy tombstones, and human-gate pauses remain enforced at the same pipeline boundaries as today. The scheduler neither consumes nor rewrites their state.

## Acceptance criteria

1. The pipeline constructs and executes an explicit plan graph before constructing render work.
2. Every page/panel render node or panel batch is derived from completed plan output and uses the existing positional state keys.
3. Scheduler traversal and node expansion are deterministic, preserve current per-mode concurrency, and reject malformed graphs before execution.
4. The plan/render barrier prevents a page or panel render from starting while any plan node is pending.
5. Existing cache and manifest hit decisions, provider-call ordering, gate/budget behavior, progress labels, and state writes remain owned by current pipeline logic.
6. `panel_compose` and `finished_page` both flow through the scheduler, including their existing mode-specific planning dependencies.
7. The scheduler creates no persistent files and performs no provider calls itself.

## Out of scope

- Extracting every internal stage into an independent runner or introducing provider/storage ports.
- Moving `chunk_cache`, `page_cache`, `beat_cache`, or `visual_bible` out of `state.json`.
- Changing action keys, manifests, fingerprints, tombstone overrides, quota/gate policy, continuity reference selection, or cache hit semantics.
- Parallel scheduling, retries, a general-purpose workflow engine, or persisted run/event replay.
- Removing `_PIPELINE_STATE_VERSION` or the remaining legacy combined fingerprint.
- Adding or running tests in this implementation slice; validation is limited to static review and repository-required formatting/lint checks only if separately authorized.

## Risks and mitigations

- **Hidden ordering dependencies:** retain chunk order for composite plan nodes and positional order for render nodes; make all scheduler-visible barriers explicit.
- **Progress drift:** node ids are not presented to users; existing stage labels and progress-count helpers remain authoritative.
- **False appearance of a complete DAG:** document that plan nodes are initially composite and internal stages are still orchestrated by the existing executor. Follow-up work may split them only after the foundation demonstrates behavior preservation.
- **Over-expansion:** keep the scheduler generic only over the node/dependency model needed by this pipeline. Do not add persistence, concurrency, provider abstractions, or retry policy.
