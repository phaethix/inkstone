# Lettering + Showcase scaffold + Streaming PDF

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship reader-visible caption/dialogue/sfx lettering, a Journey-West showcase scaffold (source+scripts only), and memory-bounded PDF export.

**Architecture:** Schema fields flow storyboard → GeneratedPanel → PanelImage → LayoutEngine drawers; ExportEngine prefers img2pdf, else batched PIL + optional merge.

**Tech Stack:** Python 3.10+, Pillow, optional img2pdf/pypdf.

## Global Constraints

- Showcase option C: no generated panels/PDF in git.
- No new hard dependencies; optional imports only.
- Backward compatible with dialogue-only state.json.
- TDD; English code/comments; keep Chinese only where CLI plan already does.

---

### Task 1: Schema lettering fields

**Files:** `core/schemas.py`, `tests/test_schemas_lettering.py` (new)

- [ ] Add `caption` / `sfx` to `Panel` and `GeneratedPanel` (optional str, coerce like dialogue)
- [ ] Tests: validate round-trip; blank → None

### Task 2: Layout lettering drawers

**Files:** `core/comic/layout.py`, `tests/test_layout.py`

- [ ] Extend `PanelImage` with caption/sfx
- [ ] Draw caption top bar, dialogue bubble, sfx outline
- [ ] Tests: caption-only and sfx-only change pixels vs plain panel

### Task 3: Pipeline + screenwriter wiring

**Files:** `core/pipelines/creative_comic.py`, `core/screenwriter.py`

- [ ] Copy caption/sfx through `_ordered_generated_panels` / `_render_panel` / layout
- [ ] Prompt reminder to split caption / dialogue / sfx

### Task 4: Streaming PDF export

**Files:** `core/comic/export.py`, `tests/test_export.py`

- [ ] img2pdf path when available
- [ ] Batched PIL (≤8 resident); merge via pypdf or pdfunite/gs
- [ ] Test: many pages export without manga2pdf; mock concurrent Image.open count optional

### Task 5: Showcase scaffold

**Files:** `examples/showcase/journey-west-ch1/source.txt`, `README.md`, `scripts/run_showcase.sh`, `docs/ROADMAP.md`

- [ ] Public-domain excerpt + run instructions
- [ ] Script runs `inkstone plan` offline
- [ ] ROADMAP checkboxes notes for lettering MVP + showcase scaffold

### Task 6: Verify

- [ ] `ruff check` + `pytest`
- [ ] Commit on current branch
