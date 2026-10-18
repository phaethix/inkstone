# Two-Phase Scheduler Foundation Implementation Plan

> **For agentic workers:** Use the `executing-plans` workflow to implement and review one task at a time. Keep the reviewed feature branch in this isolated worktree.

**Goal:** Replace implicit outer pipeline loops with an explicit ephemeral two-phase work graph while preserving current cache, provider, state, quota, gate, progress, and error behavior.

**Architecture:** Add a small validated DAG model and deterministic scheduler. The plan graph schedules one composite plan node per chunk and enforces a phase barrier. Once plans exist, the render graph expands to positional page nodes, positional continuity-dependent panel nodes, or existing bounded-concurrency panel batches; export work is scheduled after render completion. Existing `creative_comic` executors retain stage-specific behavior and keys.

**Tech Stack:** Python 3.10+, `dataclasses`, `enum`, existing asyncio pipeline and provider adapters.

## Global Constraints

- Preserve the public `creative_comic` signature and CLI/Web progress labels.
- Preserve action-key derivation, legacy fingerprint fallback, manifest semantics, output paths, positional page/panel identities, and `state.json` schema.
- Preserve single-writer behavior, current cache lookup order, cancellation checkpoints, tombstone behavior, per-item durability, and error propagation.
- Preserve the hard plan/render barrier and current render concurrency: finished pages and continuity-dependent panels execute in order; panel batches without continuity keep the existing bounded concurrency.
- Budget, gate, consistency ledger, human decisions, and audit state remain outside scheduler nodes and action keys.
- Plan-side caches remain in `state.json` for this slice. Removing legacy fingerprints and changing action-key semantics remain later work subject to the parent design's phase gates.
- The graph is ephemeral and is rebuilt on each run. It is not a second persistence or recovery source.
- Do not add or run tests; the user has not asked for test verification. Use static review only and make no runtime/test claims.

---

## File map

- Create `core/pipelines/stage_scheduler.py` for immutable node definitions, graph validation, dynamic phase expansion, and deterministic ready-node selection. It must not import providers, schemas, or project state.
- Modify `core/pipelines/creative_comic.py` to build plan work, expand render nodes from current plan output, dispatch existing execution behavior through the scheduler, and schedule export finalization.
- Modify `docs/ROADMAP.md` to describe this as scheduler foundation only; retain plan cache and fingerprint migration as outstanding work.

## Task 1: Define the ephemeral graph model

**Files:**
- Create: `core/pipelines/stage_scheduler.py`

**Interfaces:**
- `PipelinePhase(str, Enum)`: `PLAN`, `RENDER`, `EXPORT`.
- `WorkKind(str, Enum)`: `PLAN_CHUNK`, `RENDER_PAGE`, `RENDER_PANEL`, `RENDER_PANEL_BATCH`, `EXPORT`.
- Frozen `WorkNode`: `node_id: str`, `phase: PipelinePhase`, `kind: WorkKind`, `unit_index: int | None`, `item_index: int | None`, `dependencies: tuple[str, ...]`.
- `StageGraph.add(node) -> None`: reject duplicate ids immediately.
- `StageGraph.validate() -> None`: reject missing dependencies, phase-inverted edges, and cycles before execution.
- `StageGraph.ready(completed: set[str], phase: PipelinePhase) -> tuple[WorkNode, ...]`: return eligible nodes in stable `(unit_index, item_index, node_id)` order, and return no later-phase nodes while any earlier-phase node remains incomplete.
- `StageGraph.add(node) -> None` remains available after the plan phase so the pipeline can dynamically add render and export nodes, then validate them before executing those phases.

- [ ] **Step 1: Create immutable phase, kind, and node declarations.** Keep this module independent of `creative_comic` to prevent import cycles.
- [ ] **Step 2: Add graph insertion and validation.** Use a deterministic DFS/topological validation that visits node ids in lexical order and reports the involved ids for a cycle.
- [ ] **Step 3: Add ready-node selection.** Return only incomplete nodes whose dependencies are complete and whose phase matches the requested phase; never reorder ties nondeterministically.
- [ ] **Step 4: Review the module statically.** Confirm no provider, filesystem, state model, or action-key dependency exists.
- [ ] **Step 5: Commit** as `feat(pipeline): add deterministic stage graph model`.

## Task 2: Build and execute the plan phase graph

**Files:**
- Modify: `core/pipelines/creative_comic.py`
- Consume: `StageGraph`, `WorkNode`, `PipelinePhase`, and `WorkKind` from Task 1.

- [ ] **Step 1: Add a plan graph builder.** For `len(chunks)` chunks, create `plan.chunk:{ci:04d}` nodes in ascending `ci`. Each chunk node depends on the preceding chunk node because identity merges, visual-bible reconciliation, and historical planning state are shared. The first plan node has no predecessor.
- [ ] **Step 2: Validate the full plan graph before running the first chunk.** Graph construction and validation must not mutate `ProjectState` or call providers.
- [ ] **Step 3: Replace plan-loop index selection with scheduler dispatch.** Keep the existing plan-stage executor body, cache/key decisions, state saves, audit binding, and cancellation points. Resolve `ci` from the selected node and call the same path currently used when `phase == "plan"`.
- [ ] **Step 4: Mark a plan node complete only after its current chunk executor returns.** A content-policy skip retains its current saved skip behavior and counts as an executor completion; an operational exception leaves the node incomplete and aborts the run as today.
- [ ] **Step 5: Review ordering statically.** Confirm every chunk plan completes before render graph construction and that chunk order is unchanged.
- [ ] **Step 6: Commit** as `refactor(pipeline): schedule chunk planning through stage graph`.

## Task 3: Expand and execute finished-page render nodes

**Files:**
- Modify: `core/pipelines/creative_comic.py`

- [ ] **Step 1: Build the dynamic page graph after the plan barrier.** Read each chunk's current `page_cache` result in `ci` order and create one `render.page:{ci:04d}:{page_index:04d}` node per planned page. Each node uses `page_state_key(ci, page_index)` only when dispatched; scheduler ids never become persisted keys.
- [ ] **Step 2: Declare continuity order without changing reference selection.** Finished-page nodes follow current `(ci, page_index)` traversal order. Where the existing executor needs a page rendered earlier in this run, add a dependency to the immediately preceding positional page node; do not add previous-page bytes to any action key.
- [ ] **Step 3: Extract the existing one-page execution block into a focused async handler.** It must preserve manifest lookup and verified restore, tombstone hit behavior, stale-page checks, gate admission, fallback-size retry, intent/failure logging, lettering, projection updates, `state.save`, and progress callbacks exactly as currently ordered.
- [ ] **Step 4: Dispatch each page node through that handler.** A rejected/tombstoned or cache-hit page completes according to its existing branch; exceptions abort without releasing dependent nodes.
- [ ] **Step 5: Review static diffs.** Compare the moved handler line-by-line with the existing page loop; ensure keys, paths, save boundaries, and quota charges remain unchanged.
- [ ] **Step 6: Commit** as `refactor(pipeline): schedule finished pages as graph nodes`.

## Task 4: Expand panel-compose work without changing concurrency

**Files:**
- Modify: `core/pipelines/creative_comic.py`

- [ ] **Step 1: Build render work from each planned storyboard.** When `panel_continuity` is enabled, create one ordered `RENDER_PANEL` node per positional panel. When disabled, create one deterministic `RENDER_PANEL_BATCH` node per chunk; include panel state keys in positional order as batch metadata.
- [ ] **Step 2: Extract the existing per-panel executor without changing request construction or mutation order.** Keep prompt/reference assembly, key checks, gate admission, budget charge, `image_intent`, provider call, L3 processing, policy skip recording, state projection, and save behavior in the pipeline layer.
- [ ] **Step 3: Dispatch continuity-enabled panel nodes sequentially.** Preserve `prev_panel_local` handoff and current skip behavior.
- [ ] **Step 4: Dispatch no-continuity panel batches through the existing bounded semaphore and `asyncio.gather` path.** Keep deterministic result-to-panel pairing and current single batch state save.
- [ ] **Step 5: Verify the phase barrier and panel batch graph statically.** All panel work must depend on completed plan nodes; batch member ids must not be persisted.
- [ ] **Step 6: Commit** as `refactor(pipeline): schedule panel work by continuity mode`.

## Task 5: Schedule export finalization and record the boundary

**Files:**
- Modify: `core/pipelines/creative_comic.py`
- Modify: `docs/ROADMAP.md`

- [ ] **Step 1: Add requested export nodes after all render work.** Add only the output-format nodes that the current run will execute. Preserve the existing deterministic ordering and exact export helper calls.
- [ ] **Step 2: Route existing finalization through the export nodes.** Keep page/layout/Webtoon/PDF/CBZ selection, manifest keys, restore behavior, filenames, and return values unchanged.
- [ ] **Step 3: Update the roadmap.** Record the two-phase ephemeral scheduler foundation as local branch work. Explicitly say plan nodes remain composite and that plan-cache relocation, per-stage runners, legacy-fingerprint removal, and action-key changes remain incomplete.
- [ ] **Step 4: Perform final static review.** Inspect the complete diff for public API changes, changed stage keys, reordered save/charge/gate boundaries, new persistent state, or user-visible progress-label changes. Do not run tests.
- [ ] **Step 5: Commit** as `docs(pipeline): record scheduler foundation boundary` (code changes in this task may be committed separately as `refactor(pipeline): schedule exports after render work`).

## Final status

The work is complete when all five tasks are committed, the scheduler drives both render modes and export finalization, the plan/render barrier remains intact, and static review finds no change to key semantics, provider ordering, state persistence, or current concurrency. No runtime or test verification is authorized by this plan.
