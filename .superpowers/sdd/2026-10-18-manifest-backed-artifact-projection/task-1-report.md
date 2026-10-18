# Task 1 implementation report

## Scope

Implemented the shared manifest and CAS read-boundary hardening in `core/comic/cas.py` only. No tests were added or run, per task instructions.

## Changes

- Added `read_manifest_entry(root, key, *, stage=None)`, which safely parses a manifest and returns `None` for unreadable/malformed entries, payload-key mismatches, index filename mismatches, or an optional stage mismatch.
- Added `verified_manifest_files(root, key, *, stage=None)`, which only resolves `ok` manifests with at least one output and validates every referenced object as canonical SHA-256 content whose bytes hash to the declared content ID.
- Changed `stored_output` and `stored_files` to accept the optional stage and delegate to the shared resolver. `stored_output` returns the first verified output; `stored_files` returns outputs only if every output verifies.
- Refactored `verify` to reuse the shared manifest-identity predicate and CAS-object byte verifier. Malformed entries and invalid content IDs remain diagnostics; missing objects and hash mismatches are reported. It only reads index/CAS state and does not repair or reconcile it.
- Kept non-`ok` outcomes unusable as byte hits and left existing manifest-writing behavior unchanged.

## Static review

- `git diff --check`: passed.
- Python source compilation via in-memory `compile(...)`: passed.
- `py_compile` could not create its default cache directory because the environment denied writes under the user's Library cache path; no repository state was affected.
- Tests: not run (not authorized).

## Concerns

- `load_manifest` remains available with its prior parsing semantics for non-cache callers. The hardened cache read path uses `read_manifest_entry`.
- Verification output for malformed JSON is now a concise `malformed manifest` diagnostic, while existing key, tombstone, missing-object, and hash-mismatch diagnostics are retained.
