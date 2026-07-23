# Inkstone Product Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a resumable project workbench: single prompt authority, Appearance-derived L1, project-level settings, C-style alias merge/dismiss with selective redraw, unified Web/CLI `project_id` resume.

**Architecture:** Keep `creative_comic` as orchestrator and `state.json` as ledger. Add `core/comic/identity.py` for L1 derivation, settings merge, alias merge/dismiss, and force-regen. Wire Web/CLI to stable `comic_out/<project_id>/` and expose review/regen HTTP APIs + minimal UI.

**Tech Stack:** Python 3.10+, Pydantic schemas, pytest (offline mocks), stdlib `http.server` Web UI.

**Spec:** `docs/superpowers/specs/2026-07-23-inkstone-product-redesign.md`

## Global Constraints

- No silent auto-merge of character aliases; high-confidence only sets `suggested=True`.
- Panel image prompts must come only from `ConsistencyEngine.build_panel_prompt`.
- Do not add SQLite/FastAPI/new pip deps for this work.
- Prefer TDD: failing test → minimal implementation → green.
- Do not commit unless the user explicitly asks (user rule overrides “frequent commits” in the skill default).
- English identifiers/docs in code; keep existing Conventional Commit style if/when committing.

## File map

| File | Responsibility |
|---|---|
| `core/schemas.py` | `aliases`, `settings`, `stale_panels`, `CharacterAliasSuggestion.suggested`; hide `panel_prompt` from tools |
| `core/comic/identity.py` | L1 builder, settings merge, alias merge/dismiss, force regen, confidence helper |
| `core/comic/consistency.py` | Prefer Appearance-derived L1 when building prompts (via identity helper) |
| `core/comic/segmentation.py` | Alias detector marks reason; callers set `suggested` |
| `core/pipelines/creative_comic.py` | Project settings merge; chunks_done timing; stale/force regen; ensure L1 |
| `core/screenwriter.py` | Tool schemas no longer require model-written `panel_prompt` |
| `web/server.py` | `project_id` resume, review/regen routes, richer job payload |
| `web/index.html` | Review sidebar + redraw/retry controls |
| `examples/generate_comic.py` | `--project`, review summary |
| `tests/test_identity.py` | New unit tests |
| `tests/test_schemas.py`, `test_consistency_l1.py`, `test_creative_comic.py`, `test_segmentation.py`, `test_web_server.py`, `test_screenwriter.py` | Update / extend |

---

### Task 1: Schema + L1 derivation + identity helpers

**Files:**
- Create: `core/comic/identity.py`
- Modify: `core/schemas.py`
- Create: `tests/test_identity.py`
- Modify: `tests/test_schemas.py`

**Interfaces:**
- Produces:
  - `build_l1_from_appearance(name: str, appearance: Appearance, role: str = "") -> str`
  - `ensure_character_l1(char: CharacterAsset) -> CharacterAsset` (mutates/returns with filled `l1_prompt`)
  - `merge_settings(existing: dict[str, Setting], new: Iterable[Setting]) -> dict[str, Setting]`
  - `is_high_confidence_alias(reason: str) -> bool`
  - Schema fields: `CharacterAsset.aliases`, `ProjectState.settings`, `ProjectState.stale_panels`, `CharacterAliasSuggestion.suggested`
  - `Panel.panel_prompt: SkipJsonSchema[str] = ""` (or equivalent hide-from-tool)

- [ ] **Step 1: Write failing tests** in `tests/test_identity.py` and extend `tests/test_schemas.py`

```python
# tests/test_identity.py
from core.comic.identity import build_l1_from_appearance, merge_settings, is_high_confidence_alias
from core.schemas import Appearance, Setting

def test_build_l1_includes_appearance_fields():
    app = Appearance(hair="black short hair", eyewear="round glasses", outfit_top="white shirt")
    text = build_l1_from_appearance("Fang", app, role="protagonist")
    assert "Fang" in text
    assert "black short hair" in text
    assert "round glasses" in text

def test_merge_settings_keeps_first_nonempty():
    existing = {"Cafe": Setting(name="Cafe", scene_prompt="warm cafe")}
    merged = merge_settings(existing, [Setting(name="Cafe", description="later"), Setting(name="Street", scene_prompt="rainy street")])
    assert merged["Cafe"].scene_prompt == "warm cafe"
    assert "Street" in merged

def test_high_confidence_substring_reason():
    assert is_high_confidence_alias("name variant (normalized/substring match)") is True
    assert is_high_confidence_alias("similar name (difflib match)") is False
```

Also assert `to_tool_schema(Storyboard, ...)` properties do **not** include `panel_prompt`, and `ProjectState` round-trips `settings` / `stale_panels` / `aliases` / `suggested`.

- [ ] **Step 2: Run tests — expect FAIL**

```bash
pytest tests/test_identity.py tests/test_schemas.py -v
```

- [ ] **Step 3: Implement schemas + `core/comic/identity.py`**

- [ ] **Step 4: Run tests — expect PASS**

```bash
pytest tests/test_identity.py tests/test_schemas.py -v
```

---

### Task 2: Alias merge / dismiss / force_regen

**Files:**
- Modify: `core/comic/identity.py`
- Modify: `tests/test_identity.py`

**Interfaces:**
- Produces:
  - `merge_character_alias(state: ProjectState, new_name: str, keep_name: str) -> list[str]`
  - `dismiss_character_alias(state: ProjectState, new_name: str, candidate: str) -> None`
  - `force_regen_panels(state: ProjectState, keys: list[str]) -> None`

**Merge behavior (required):**
1. Require both names present OR `new_name` only in `needs_review` / characters; `keep_name` must exist in `state.characters`.
2. Add `new_name` to `keep.aliases` if missing; if `new_name` had a character row, drop it after copying any richer empty fields onto keep (optional fill-empty).
3. Rewrite `characters_present` / `reference_characters` in all cached storyboard panels: `new_name` → `keep_name`.
4. Remove matching `needs_review` entries.
5. Collect panel state keys from `generated.panels` / storyboard indices that referenced `new_name` (pre-rewrite) or keep’s panels if keep portrait path changed; append unique keys to `stale_panels`; remove those keys from `panels_done`.
6. Return the stale key list.

**Dismiss:** remove matching `needs_review` row(s) only.

**force_regen_panels:** for each key, remove from `panels_done` and `skipped`; ensure key in `stale_panels`.

- [ ] **Step 1: Failing tests**

```python
def test_merge_alias_rewrites_storyboard_and_marks_stale(tmp_path):
    # Build ProjectState with characters 方鸿渐 + 鸿渐, one panel referencing 鸿渐, panels_done containing that key
    # merge_character_alias(state, "鸿渐", "方鸿渐")
    # assert "鸿渐" not in state.characters
    # assert "鸿渐" in state.characters["方鸿渐"].aliases
    # assert panel names rewritten
    # assert state key in stale_panels and not in panels_done

def test_dismiss_alias_only_clears_review():
    ...

def test_force_regen_panels_clears_done_and_skipped():
    ...
```

- [ ] **Step 2: Implement until PASS**

```bash
pytest tests/test_identity.py -v
```

---

### Task 3: ConsistencyEngine uses Appearance-derived L1

**Files:**
- Modify: `core/comic/consistency.py`
- Modify: `tests/test_consistency_l1.py`

**Interfaces:**
- Consumes: `ensure_character_l1` / `build_l1_from_appearance`
- Change: `build_panel_prompt` uses `ensure_character_l1(c).l1_prompt` (or inline call) so empty `l1_prompt` + filled Appearance still hardens identity

- [ ] **Step 1: Failing test** — character with Appearance filled and `l1_prompt=""` still appears in prompt via hair/outfit text

- [ ] **Step 2: Implement + PASS**

```bash
pytest tests/test_consistency_l1.py -v
```

---

### Task 4: Pipeline — settings registry, L1 ensure, chunks_done, stale regen

**Files:**
- Modify: `core/pipelines/creative_comic.py`
- Modify: `core/screenwriter` tool usage only if schema hide breaks tests
- Modify: `tests/test_creative_comic.py`
- Modify: `tests/test_segmentation.py` (suggested flag wiring if tested via pipeline)

**Required pipeline changes:**
1. After extract: `state.settings = merge_settings(state.settings, elements.settings)`; when resolving setting for a panel, prefer `state.settings`.
2. When merging new characters: `ensure_character_l1` on create/update; when recording alias suggestions, set `suggested=is_high_confidence_alias(reason)`.
3. Move `chunks_done.append` to after panels for that chunk are fully accounted for (or on `skipped_chunks`).
4. `_chunk_complete` / resume: treat keys in `stale_panels` as not done.
5. After successfully regenerating a stale panel, remove its key from `stale_panels`.
6. Add optional `creative_comic(..., panel_keys: list[str] | None = None)` — if provided, only generate those keys (and still run layout/export). If `None`, normal run but honor stale.

- [ ] **Step 1: Tests**
  - Alias suggestion has `suggested=True` for substring case (extend existing alias test)
  - Resume regenerates stale panel
  - `chunks_done` absent until panels finished (assert mid-state via monkeypatch or inspect after partial mock failure — simplest: after full success chunk key is in `chunks_done`; after portrait-only crash simulation if feasible)
  - Settings reused across chunks by name (two-chunk mock extract)

- [ ] **Step 2: Implement +**

```bash
pytest tests/test_creative_comic.py tests/test_hardening_regressions.py tests/test_safety.py -v
```

---

### Task 5: Web — project_id resume, review/regen APIs, job payload

**Files:**
- Modify: `web/server.py`
- Modify: `tests/test_web_server.py`

**Routes / payload:**
- `POST /api/generate`: accept `project_id`; output `comic_out/<project_id>/`; return `{job_id, project_id}`
- `GET /api/job/<job_id>`: add `project_id`, `skipped`, `skipped_chunks`, `needs_review`, `stale_panels`
- `POST /api/project/<project_id>/review`: `{action, new_name, candidate}` → load `state.json`, merge/dismiss, save, return `{stale_panels, needs_review}`
- `POST /api/project/<project_id>/regen`: `{stale: true}` or `{keys: [...]}` → start job calling `creative_comic` with `panel_keys` / stale handling; return `{job_id, project_id}`

Path safety: project_id must be safe slug (`^[a-zA-Z0-9_-]{1,64}$`); reject `..`.

- [ ] **Step 1: Failing API unit tests** (call handler helpers directly or use `server` functions with tmp `OUTPUT_DIR`)

- [ ] **Step 2: Implement + PASS**

```bash
pytest tests/test_web_server.py -v
```

---

### Task 6: Web UI — review sidebar + redraw

**Files:**
- Modify: `web/index.html`

**UI:**
- Persist/show `project_id` from generate response; poll job includes review lists
- Side panel: needs_review rows with Merge / Dismiss; badge when `suggested`
- Skipped list + “Retry” → regen API
- After merge, if `stale_panels` non-empty, show “Redraw N panels” → regen `{stale: true}`

Keep demo-mode (no backend) behavior intact.

- [ ] **Step 1: Manual sanity** — load `index.html` structure; ensure JS calls new endpoints (no full browser automation required if careful). Add a tiny pure-JS-free check only if project already tests HTML (optional). Prefer keep server tests as contract.

- [ ] **Step 2: Implement UI wiring**

---

### Task 7: CLI `--project` + review summary

**Files:**
- Modify: `examples/generate_comic.py`

- [ ] **Step 1:** Add `--project`; default `out` to `comic_out/<project>` when set
- [ ] **Step 2:** Print `needs_review` / `stale_panels` / skipped counts at end

---

### Task 8: Full suite + doc touch-up

- [ ] **Step 1:**

```bash
pytest -v
```

Expected: all previously passing tests still pass (update any schema/tool-schema assertions that mentioned `panel_prompt`).

- [ ] **Step 2:** Update `README.md` “How it works” / Web UI bullets briefly for project resume + review (short, no new markdown sprawl).

- [ ] **Step 3:** Mark tasks complete in this plan file checkboxes.

---

## Spec coverage checklist

| Spec item | Task |
|---|---|
| Hide/ignore `panel_prompt`; single build path | 1, 3, 4 |
| Appearance → L1 | 1, 3, 4 |
| Project-level settings | 1, 4 |
| C-style review; suggested not silent | 2, 4, 5, 6 |
| Merge → stale → selective redraw | 2, 4, 5, 6 |
| `chunks_done` timing | 4 |
| Web/CLI same project_id resume | 5, 7 |
| Job exposes review/skip/stale | 5, 6 |
| No SQLite/FastAPI/silent merge | Global |

## Self-review notes

- No TBD placeholders in task steps.
- Types aligned: stale keys are `cXXXX-pYYYY` strings matching `_panel_state_key`.
- Commits deferred to user request.
