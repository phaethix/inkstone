# Narrative Fidelity Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the four pipeline gaps in `.issue/2026-08-03-15_04-review.md` — identity stage lock, layout anti-template, voice/timeline layers, and must-draw beats — as sequential, mergeable phases.

**Architecture:** Extend Visual Bible + finished-page planner/prompt/fingerprint without novel-specific hardcoding. Each phase adds schema and/or helpers, wires `creative_comic` / `screenwriter` / `page_prompt`, bumps a fingerprint token, and ships unit tests.

**Tech Stack:** Python 3.10+, pydantic schemas, pytest, existing `core/comic/*` + `core/pipelines/creative_comic.py`.

**Spec:** `docs/superpowers/specs/2026-08-03-narrative-fidelity-pipeline-design.md`

## Global Constraints

- No Zweig / novel-specific constants in code (QA checklists may live under `.issue/`).
- No in-image lettering regression; keep deferred lettering.
- No VLM QA loops in these phases.
- One phase per PR; fingerprint tokens as named in the spec.
- Prefer TDD: failing test → implement → pass → commit.

---

## File map

| File | A | B | C | D |
|------|---|---|---|---|
| `core/schemas.py` | age_look | — | speaker, timeline | beats / covers_beats |
| `core/comic/visual_bible.py` | stage resolve, refs | — | — | — |
| `core/comic/page_prompt.py` | hair/age lines | anti-standee | timeline grammar | beat action lines |
| `core/comic/layout_diversity.py` | — | **new** | — | — |
| `core/comic/voice.py` | — | — | **new** (sanitize) | — |
| `core/screenwriter.py` | age_look reconcile | prior layouts | speaker/timeline plan rules | extract_key_beats + retry |
| `core/pipelines/creative_comic.py` | wire resolve + fp | pass prior layouts | wire sanitize + fp | wire beats + fp |
| `tests/test_stage_lock.py` | **new** | — | — | — |
| `tests/test_layout_diversity.py` | — | **new** | — | — |
| `tests/test_voice_timeline.py` | — | — | **new** | — |
| `tests/test_key_beats.py` | — | — | — | **new** |

---

## Phase A — Identity lock hardening

### Task A1: Schema `age_look` + stage resolve helper

**Files:** `core/schemas.py`, `core/comic/visual_bible.py`, `tests/test_stage_lock.py`

- [ ] Add failing tests for `resolve_panel_stage_refs`: action containing 少女 / child cue rewrites bare name → `Name@child` when stage exists; no rewrite when stage missing.
- [ ] Add `CharacterStage.age_look: str = ""` with coerce.
- [ ] Implement `AGE_CUE_TO_STAGE` heuristics (generic CN/EN markers → child|teen|adult|elder).
- [ ] Implement `resolve_panel_stage_refs(plan, bible) -> ComicPagePlan`.
- [ ] Extend `ensure_stage_locks` / sanitize to fill blank `age_look` from stage literal defaults.
- [ ] Run `pytest tests/test_stage_lock.py -q`.
- [ ] Commit: `feat: resolve panel character stages from age cues`

### Task A2: Prompt + refs + fingerprint

**Files:** `core/comic/page_prompt.py`, `core/pipelines/creative_comic.py`, `core/screenwriter.py`, tests

- [ ] Failing test: finished-page prompt includes hair_lock and age_look when present; includes “do not change hair color/length” lock line.
- [ ] Inject hair/age lines in `_character_desc_for_prompt` / identity block.
- [ ] Wire `resolve_panel_stage_refs` after bible rewrite / before render in finished_page path.
- [ ] Reconcile instructions: fill `age_look` per stage.
- [ ] Fingerprint: add `"identity": "stage_lock_v1"` (replace or alongside metaphor_v2 — prefer replace metaphor token only if tests allow; else add `stage_lock: "v1"` key). Update fingerprint tests.
- [ ] Run related pytest; commit: `feat: force stage identity lines and stage_lock fingerprint`

### Task A3: Phase A verification

- [ ] `pytest tests/test_stage_lock.py tests/test_visual_bible_v3.py tests/test_finished_page_pipeline.py tests/test_page_prompt.py -q`
- [ ] Open PR for Phase A only.

---

## Phase B — Layout anti-template

### Task B1: Diversity helper + catalog

**Files:** `core/comic/layout_diversity.py` (new), `tests/test_layout_diversity.py`

- [ ] Define `LAYOUT_CATALOG` frozenset of intent tokens.
- [ ] `summarize_recent_layouts(pages) -> str` for planner context.
- [ ] `consecutive_layout_streak(intents) -> int` helper.
- [ ] Tests for summarize + streak.
- [ ] Commit: `feat: add layout diversity helpers`

### Task B2: Planner + prompt wire

**Files:** `core/screenwriter.py`, `core/comic/page_prompt.py`, `core/pipelines/creative_comic.py`

- [ ] `plan_comic_pages(..., recent_layouts: list[str] | None = None)` inject anti-repeat instructions + catalog.
- [ ] `ANTI_CENTER_STANDEE_LINE` in page_prompt when bible or always for finished_page.
- [ ] Pipeline: collect last K `layout_intent` from state / prior pages in chunk; pass into planner.
- [ ] Fingerprint `layout: "anti_template_v1"`.
- [ ] Tests; commit; PR Phase B.

---

## Phase C — Voice + timeline

### Task C1: Schema + voice sanitize

**Files:** `core/schemas.py`, `core/comic/voice.py`, `tests/test_voice_timeline.py`

- [ ] Add `speaker`, page/panel `timeline` fields.
- [ ] `sanitize_panel_voice(panel, bible) -> panel`: letter-writer I-voice in reader-only dialogue → caption.
- [ ] Tests; commit.

### Task C2: Planner + prompt + fingerprint

**Files:** `core/screenwriter.py`, `core/comic/page_prompt.py`, `core/pipelines/creative_comic.py`

- [ ] Plan instructions for speaker + timeline; inject letter_reader/writer names from bible.
- [ ] Timeline visual grammar lines in `render_finished_page_prompt`.
- [ ] Wire sanitize before lettering / storage.
- [ ] Fingerprint `voice_timeline: "v1"`.
- [ ] Tests; PR Phase C.

---

## Phase D — Key beats

### Task D1: Beat schema + extract tool

**Files:** `core/schemas.py`, `core/screenwriter.py`, `tests/test_key_beats.py`

- [ ] Models: `KeyBeat`, list on state or chunk cache; `covers_beats` on `ComicPagePlan`.
- [ ] `extract_key_beats` tool + fake chat test.
- [ ] Commit.

### Task D2: Coverage retry + prompt + fingerprint

**Files:** `core/pipelines/creative_comic.py`, `core/comic/page_prompt.py`

- [ ] After `plan_comic_pages`, if must_draw uncovered → one retry with beat list.
- [ ] Prompt: when covers_beats, require physical staging.
- [ ] Fingerprint `beats: "v1"`.
- [ ] Tests; PR Phase D.

---

## Manual QA (after A–D)

Use `.issue/2026-08-03-15_04-review.md` §八.5 as a **human checklist** for 《来信》 re-runs — not asserted in CI:

1. 厚信开封 2. 死婴四烛 3. 初见一瞥 4. 门铃与空屋 5. 初夜白玫瑰 6. 产院 7. 舞厅一小时 8. 钞票/约翰/空蓝花瓶

---

## Progress

| Phase | Status |
|-------|--------|
| Spec | written |
| A Identity | in progress (code done; PR next) |
| B Layout | pending |
| C Voice/timeline | pending |
| D Beats | pending |
