# Task 3 implementation report: verified finished-page restoration

## Changes

Updated `core/pipelines/creative_comic.py` to make manifest-backed reads
stage-specific and resilient at every pipeline cache boundary:

- Lettering reads use `read_manifest_entry(..., stage="letter")` for
  tombstones and `stored_output(..., stage="letter")` for verified bytes.
- Finished-page render recovery uses `read_manifest_entry(...,
  stage="render.page")`. A valid non-`ok` manifest stops the provider call;
  an `ok` manifest is restored only through hash-verifying `stored_output`.
- PDF, CBZ, webtoon, and panel-layout restoration use the exact `export` or
  `layout` stage when reading manifest entries and CAS outputs.
- Replaced finished-page projection writes after local relettering, CAS blank
  restoration, and a newly generated page with `record_page_artifact`. A
  lettering tombstone is checked before those writes, so it cannot create a
  successful projection or clear a stale marker.
- Added a narrow cleanup for a newly shortened page plan. It calls
  `drop_page_artifact` only for canonical positional keys in the current chunk
  whose page index is outside the new plan. Stale markers, tombstones, other
  chunks, and non-positional legacy keys remain untouched.

The local blank/current-lettering fast path remains before render manifest
resolution. Projects without usable manifests therefore retain their existing
local fingerprint and path-based resume behavior.

## Static review

Reviewed every `stored_output` and `stored_files` call in the pipeline:
each has the expected stage. Reviewed every manifest tombstone check: each now
uses `read_manifest_entry` with that same stage, so malformed manifests are
misses while valid non-`ok` outcomes remain non-provider hits. Confirmed page
projection writes are routed through `record_page_artifact` in the restored,
newly-lettered, and newly-rendered page paths. Ran `git diff --check` only.

## Tests

Not run (not authorized by the task brief).

## Constraints preserved

The change does not alter action-key construction, atomic state or manifest
writes, process locking, deterministic page paths, plan caches, authoritative
human state, or CLI verification behavior.
