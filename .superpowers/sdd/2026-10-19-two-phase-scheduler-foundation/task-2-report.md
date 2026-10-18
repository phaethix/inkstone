# Task 2 report: plan phase graph

## Change

`creative_comic` now builds an ephemeral plan graph with one
`plan.chunk:{ci:04d}` node per segmented chunk. Each node depends on the
preceding node, so the existing serial identity merge, visual-bible
reconciliation, and planning-history behavior stays ordered. The graph is
validated before plan dispatch and does not read or mutate `ProjectState`, call
providers, alter action keys, or persist scheduler state.

The plan loop selects each chunk through `StageGraph.ready`. Its completion is
recorded only when the existing chunk body returns control to the dispatcher:
the pre-existing cache and content-policy `continue` paths complete the current
node, while an operational exception exits before completion and preserves the
current abort behavior.

## Behavior boundaries

- The public `creative_comic` signature, progress labels, action-key and legacy
  fingerprint behavior, manifest and `state.json` semantics, state save points,
  cancellation checks, audit binding, output paths, and error propagation are
  unchanged.
- The plan graph is rebuilt in memory for every run. It is not persistence or a
  recovery source.
- Render dispatch remains the existing `enumerate(chunks)` loop. Finished-page
  ordering, continuity-panel ordering, and no-continuity panel batching are
  unchanged. The plan generator must exhaust before this render loop begins,
  preserving the plan/render barrier.
- Budget, gate, consistency ledger, human decisions, and audit state remain
  outside graph nodes and scheduler identifiers.

## Static review

- `git diff --check` passed before the implementation commit.
- The graph builder creates ascending chunk ids and a single predecessor edge
  for every chunk after the first. `StageGraph.ready` can expose only one plan
  node at a time because of those edges.
- No tests were added or run, as required.

## Commit

SHA: `722c62de842d42e70c6894ba49a04e859b405094`
