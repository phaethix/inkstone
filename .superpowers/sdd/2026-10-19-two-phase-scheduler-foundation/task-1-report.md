# Task 1 report: ephemeral stage graph

## Interface

Added `core/pipelines/stage_scheduler.py` with `PipelinePhase`, `WorkKind`, immutable `WorkNode`, and mutable in-memory `StageGraph`. The graph supports duplicate-rejecting `add`, deterministic `validate` for missing dependencies, phase-inverted edges, and cycles, and `ready` for phase-barrier-aware dependency eligibility in stable `(unit_index, item_index, node_id)` order. Nodes may be added after validation so later phases can be assembled dynamically.

## Static checks

- `git diff --check` passed for the implementation.
- The module imports only `dataclasses` and `enum`; it has no dependency on `creative_comic`, providers, filesystem APIs, persistent state, or action-key derivation.
- Phase ordering is explicit (`PLAN`, `RENDER`, `EXPORT`), not based on enum string ordering.
- DFS roots and dependency visits are lexical; cycle errors include the cycle path.
- No tests were run, as required.

## Commit

SHA: `8c0d391262a73d40b47e23744dfd01cf5e0369f6` (implementation commit before this report update).
