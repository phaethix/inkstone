# Inkstone Product Redesign — Spec

**Date:** 2026-07-23  
**Status:** Approved for implementation  
**Goal:** Turn Inkstone from a runnable pipeline into a resumable comic **project workbench** with a single prompt authority, a governable character-identity ledger, and unified Web/CLI project semantics.

## Product decisions (locked)

- **Alias policy:** Option C — generation continues without blocking; humans merge/dismiss anytime from a review surface. High-confidence matches are **suggested**, never silently merged.
- **After merge:** mark affected panels stale; redraw only those panels (+ re-layout/export), not the whole novel.
- **Prompt authority:** pipeline-built prompts only; LLM does not own `panel_prompt`.
- **L1 source:** derive from structured `Appearance` (+ name); do not treat free-form LLM `l1_prompt` as sole truth.
- **Settings:** project-level registry (cross-chunk reuse), not chunk-local only.
- **Persistence:** keep `state.json` + stdlib web server this round (no SQLite/FastAPI migration).
- **Out of scope:** InsightFace/L4, auto-silent merge, speech-bubble speaker targeting polish.

## Success criteria

1. Same `project_id` directory is resumable from Web and CLI.
2. One code path builds every panel image prompt.
3. Alias forks are detectable, mergeable/dismissable, and merge triggers selective redraw.
4. Job/API surfaces expose `needs_review`, `skipped`, `skipped_chunks`, and stale counts.
5. `chunks_done` means “chunk panels finished or chunk skipped”, not “portraits done”.
6. Existing offline pytest suite stays green; new behaviors covered by tests.

## Domain model changes

### Project

- On-disk root: `comic_out/<project_id>/`
- Contains: `state.json`, `portraits/`, `panels/`, layout outputs, optional copied `source.txt`
- CLI: `--project <id>` or derive stable id; `--out` may still point at the project root
- Web: client may pass `project_id`; omitted → create new id; reuse directory + `state.json` for resume

### CharacterAsset

- Add `aliases: list[str]` (names merged into this identity)
- `l1_prompt` remains stored but is **refreshed** from `Appearance` via `build_l1_from_appearance` when appearance is non-empty
- Portrait ownership stays on the kept character after merge

### Setting (project-level)

- `ProjectState.settings: dict[str, Setting]`
- Merge by exact name across chunks (first wins unless empty fields can be filled)
- Panel `setting_ref` resolves: project settings → current chunk elements → empty

### CharacterAliasSuggestion

- Add `suggested: bool` — true for high-confidence (normalized equality / strict substring)
- `dismiss` removes from `needs_review` only
- `merge(new_name → keep_name)`:
  - move/merge asset into keep; record `new_name` in `keep.aliases`
  - rewrite panel character name lists in `chunk_cache` storyboards where needed
  - drop duplicate portrait for `new_name` from table (file may remain orphan; optional delete)
  - remove matching `needs_review` entries
  - mark panels that referenced `new_name` (or keep, if portrait changed) as **stale**

### Stale / force regen

- `ProjectState.stale_panels: list[str]` — panel state keys (`c0000-p0000`)
- Pipeline skips regen for keys in `panels_done` unless also in `stale_panels` or caller passes force set
- `force_regen(state, keys)` clears keys from `panels_done` / `skipped` / adds to `stale_panels` as needed
- `regen_stale=True` on `creative_comic` clears stale after successful redraw

### Checkpoint semantics

- Append to `chunks_done` only when every panel of the chunk is in `panels_done` or `skipped`, or the whole chunk is in `skipped_chunks`
- `stage` remains a progress label only; resume continues to use fingerprint + cache + files

### Panel.panel_prompt

- Hide from tool schema (`SkipJsonSchema`) or stop requesting it from the model
- Runtime ignores any stored value; always `ConsistencyEngine.build_panel_prompt`

## API / UX

### Library API (`core/comic/identity.py` or extend `segmentation.py`)

- `build_l1_from_appearance(name, appearance, role="") -> str`
- `merge_settings(existing, new) -> dict`
- `merge_character_alias(state, new_name, keep_name) -> list[str]` (returns stale keys)
- `dismiss_character_alias(state, new_name, candidate) -> None`
- `force_regen_panels(state, keys: list[str]) -> None`
- `is_high_confidence_alias(reason: str) -> bool`

### Web

- `POST /api/generate` body: `{text, format?, style_guide?, project_id?}` → `{job_id, project_id}`
- `GET /api/job/<id>` includes: `project_id`, `skipped`, `skipped_chunks`, `needs_review`, `stale_panels`, panels/webtoon/pdf
- `POST /api/project/<project_id>/review` `{action: merge|dismiss, new_name, candidate}`
- `POST /api/project/<project_id>/regen` `{keys?: string[], stale?: true}` — starts a job that only regenerates requested/stale panels
- UI: review side panel with suggested merge highlight; skipped list + retry; “Redraw affected” after merge

### CLI

- `examples/generate_comic.py --project <id>` (output dir = `comic_out/<id>` unless `--out` overrides)
- Print `needs_review` summary at end

## Compatibility

- Old `state.json` without new fields: pydantic defaults (empty lists/dicts)
- Legacy `panel_prompt` in cached storyboards: ignored
- No mandatory migration tool

## Non-goals (restate)

Silent auto-merge; GPU identity stack; replacing persistence with SQLite; FastAPI rewrite; bubble geometry redesign.
