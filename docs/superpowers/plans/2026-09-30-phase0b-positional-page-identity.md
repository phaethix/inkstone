# Phase 0b: Positional Page Identity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace model-generated `page_id` in the page state key with a positional index, so a page's identity is `c{ci:04d}-p{idx:04d}` and a replan that renames pages cannot orphan records.

**Architecture:** Page identity moves from `c{ci:04d}:{model_page_id}` to `c{ci:04d}-p{idx:04d}`, converging on the convention panel keys already use. A single shared formatter becomes the one source of truth; the pipeline and the alias-merge invalidator both call it. `page_id` stays on `ComicPagePlan`/`GeneratedPage` for prompts and auditing but no longer participates in identity. Because the key format changes, a deterministic, zero-quota rewrite on load maps any legacy `state.json` keys onto the new format so a resumed project is not repainted.

**Tech Stack:** Python 3, Pydantic v2, pytest, ruff (pinned 0.16.6, run via the project venv).

## Global Constraints

- Design §9 row **0b**, verbatim: "Mandate positional identity `c{ci:04d}-p{idx:04d}`; remove model-generated `page_id` from identity. Converges on the convention panel keys already use (L278)."
- Quota risk for 0b is **none**. No task may introduce a new billable call; the migration must never cause a repaint of an already-generated page.
- Design §4, verbatim: "Model-generated `page_id` values must never be identity, because a replan that renames pages would orphan every record."
- Panel keys and page keys live in separate state fields (`stale_panels`/`panels_done` vs `stale_pages`/`pages_done`) and `render_mode` is a single per-project value, so page and panel keys never coexist in one project. Discriminate by `render_mode`, never by guessing from the key string.
- Run tests with `.venv/bin/python -m pytest`. Format with `.venv/bin/ruff format` (venv ruff is 0.16.6; `uvx ruff` resolves 0.16.9 and can churn unrelated files — do not use it).
- Do not change the page asset path format (`page_c{ci:04d}_p{idx:04d}.png`); it is already positional and orthogonal to identity.

---

## File Structure

- `core/comic/identity.py` — gains the shared `page_state_key()` formatter (single source of truth for page identity). Owns alias-merge invalidation, which already builds page keys, so it must reuse the formatter instead of a hardcoded f-string.
- `core/schemas.py` — `ProjectState` gains `migrate_legacy_page_keys()`: a self-contained method (no new imports, no cycle with `identity.py`) that rewrites legacy keys in place.
- `core/pipelines/creative_comic.py` — `_page_state_key` removed in favour of the shared formatter; the page loop already enumerates `page_index`; `previous_page_blank`'s cross-chunk lookup stops parsing the key string and uses `GeneratedPage.unit_index`/`page_index`.
- `web/server.py` — the regen entry point discriminates page vs panel keys by `render_mode` instead of `":" not in k`.
- `core/cli.py` — the two `ProjectState.load` call sites migrate after loading.
- Tests: `tests/test_phase0b_page_identity.py` (new), `tests/test_phase0b_legacy_migration.py` (new), plus literal updates in `tests/test_finished_page_pipeline.py`, `tests/test_identity_lockdown.py`, `tests/test_schemas_finished_page.py`, `tests/test_web_server.py`, `tests/test_cli_rebuild.py`.

---

### Task 1: Shared page-key formatter

**Files:**
- Modify: `core/comic/identity.py` (add `page_state_key`)
- Test: `tests/test_phase0b_page_identity.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces: `page_state_key(chunk_index: int, page_index: int) -> str`, returning `f"c{chunk_index:04d}-p{page_index:04d}"`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_phase0b_page_identity.py`:

```python
"""Phase 0b: page identity is positional ``c{ci:04d}-p{idx:04d}`` (§4/§9)."""

from core.comic.identity import page_state_key


def test_page_state_key_matches_panel_key_shape():
    assert page_state_key(0, 0) == "c0000-p0000"
    assert page_state_key(1, 11) == "c0001-p0011"


def test_page_state_key_is_zero_padded_to_four():
    assert page_state_key(12, 300) == "c0012-p0300"
    assert page_state_key(3, 7) == "c0003-p0007"


def test_page_state_key_ignores_model_page_id():
    """Identity must be derivable from position alone (no model string)."""
    assert page_state_key(0, 0) == page_state_key(0, 0)
    assert ":" not in page_state_key(0, 0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_phase0b_page_identity.py -v`
Expected: FAIL with `ImportError: cannot import name 'page_state_key'`

- [ ] **Step 3: Write minimal implementation**

In `core/comic/identity.py`, add this function immediately after the module-level constants (after `_HUMAN_LOCK`, around line 46) so the page and panel conventions are documented together:

```python
def page_state_key(chunk_index: int, page_index: int) -> str:
    """Return the positional identity for one finished-page position.

    Phase 0b (§4/§9): a page's identity is its position — ``c{ci:04d}-p{idx:04d}``
    — matching the convention panel keys already use.  The model-generated
    ``ComicPagePlan.page_id`` is deliberately **not** part of this key: a replan
    that renames pages would otherwise orphan every recorded page.
    """
    return f"c{chunk_index:04d}-p{page_index:04d}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_phase0b_page_identity.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Write the failing test for panel-key convergence**

Append to `tests/test_phase0b_page_identity.py`:

```python
from core.pipelines.creative_comic import _panel_state_key, _stored_panel_key


def test_page_and_panel_keys_share_one_convention():
    """0b: page keys converge on the panel convention, one formatter each."""
    assert _panel_state_key(0, 3) == "c0000-p0003"
    assert _page_state_key(0, 3) == _panel_state_key(0, 3)
```

Add this import to the same file's top:

```python
from core.pipelines.creative_comic import _page_state_key
```

- [ ] **Step 6: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_phase0b_page_identity.py::test_page_and_panel_keys_share_one_convention -v`
Expected: FAIL — `_page_state_key` still returns `c0000:3`

- [ ] **Step 7: Write minimal implementation**

Make the interim `_page_state_key` delegate to the shared formatter (Task 3 deletes it entirely):

```python
def _page_state_key(chunk_index: int, page_index: int) -> str:
    """Deprecated shim; Task 3 replaces call sites with ``page_state_key``."""
    return page_state_key(chunk_index, page_index)
```

Also collapse the duplicated panel formatter to one source of truth (lines 297-299), satisfying 0b's "converges on the convention panel keys already use":

```python
def _stored_panel_key(state: ProjectState, chunk_index: int, panel_index: int) -> str:
    """Return the pipeline-owned identity for a current-version storyboard panel."""
    return _panel_state_key(chunk_index, panel_index)
```

- [ ] **Step 8: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_phase0b_page_identity.py -v`
Expected: PASS (4 passed)

- [ ] **Step 9: Commit**

```bash
git add core/comic/identity.py core/pipelines/creative_comic.py tests/test_phase0b_page_identity.py
git commit -m "feat(identity): add positional page_state_key formatter (0b)"
```

---

### Task 2: Legacy key migration on ProjectState

**Files:**
- Modify: `core/schemas.py` (add `ProjectState.migrate_legacy_page_keys`)
- Test: `tests/test_phase0b_legacy_migration.py` (create)

**Interfaces:**
- Consumes: nothing (pure method, no `identity.py` import so no import cycle).
- Produces: `ProjectState.migrate_legacy_page_keys(self) -> bool` — rewrites legacy `c{ci:04d}:{page_id}` keys to `c{ci:04d}-p{idx:04d}` across `generated.pages`, `pages_done`, `stale_pages`, `skipped_pages`, `tombstones`; returns `True` if anything changed.

- [ ] **Step 1: Write the failing test**

Create `tests/test_phase0b_legacy_migration.py`:

```python
"""Phase 0b: pre-0b ``c{ci:04d}:{page_id}`` keys migrate to positional keys."""

from core.schemas import (
    ComicPagePlan,
    ComicPagePlanSet,
    GeneratedAssets,
    GeneratedPage,
    ProjectState,
    Tombstone,
)


def _legacy_state() -> ProjectState:
    pages = ComicPagePlanSet(
        unit_id="1",
        pages=[
            ComicPagePlan.model_validate(
                {"page_id": "u1_p0001", "panels": [{"panel_id": "1", "action": "a"}]}
            ),
            ComicPagePlan.model_validate(
                {"page_id": "u1_p0002", "panels": [{"panel_id": "2", "action": "b"}]}
            ),
        ],
    )
    return ProjectState(
        project_id="t",
        render_mode="finished_page",
        page_cache={"0": pages},
        pages_done=["c0000:u1_p0001"],
        stale_pages=["c0000:u1_p0002"],
        skipped_pages=["c0000:u1_p0002"],
        generated=GeneratedAssets(
            pages={"c0000:u1_p0001": GeneratedPage(local="/tmp/a.png", page_id="u1_p0001")}
        ),
        tombstones={
            "c0000:u1_p0002": Tombstone(
                outcome="rejected", reason="content_policy", stage="render.page"
            )
        },
    )


def test_migration_rewrites_all_five_key_maps():
    state = _legacy_state()
    changed = state.migrate_legacy_page_keys()

    assert changed is True
    assert state.pages_done == ["c0000-p0000"]
    assert state.stale_pages == ["c0000-p0001"]
    assert state.skipped_pages == ["c0000-p0001"]
    assert set(state.generated.pages) == {"c0000-p0000"}
    assert state.generated.pages["c0000-p0000"].page_id == "u1_p0001"
    assert set(state.tombstones) == {"c0000-p0001"}


def test_migration_is_idempotent():
    state = _legacy_state()
    assert state.migrate_legacy_page_keys() is True
    assert state.migrate_legacy_page_keys() is False
    assert state.pages_done == ["c0000-p0000"]


def test_migration_leaves_new_format_and_unknown_keys_untouched():
    state = ProjectState(
        project_id="t",
        pages_done=["c0000-p0000"],
        stale_pages=["c0009:not_in_cache"],
    )

    assert state.migrate_legacy_page_keys() is False
    assert state.pages_done == ["c0000-p0000"]
    assert state.stale_pages == ["c0009:not_in_cache"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_phase0b_legacy_migration.py -v`
Expected: FAIL with `AttributeError: 'ProjectState' object has no attribute 'migrate_legacy_page_keys'`

- [ ] **Step 3: Write minimal implementation**

In `core/schemas.py`, add this method to `ProjectState` immediately after the `load` classmethod (around line 1496):

```python
    def migrate_legacy_page_keys(self) -> bool:
        """Rewrite pre-0b ``c{ci:04d}:{page_id}`` page keys to positional keys.

        Phase 0b made page identity positional (§4/§9).  ``state.json`` files
        written before that change still carry ``c{ci:04d}:{page_id}`` keys; left
        alone, every recorded page would look un-generated and the next run would
        repaint the whole book — a billable regression, which 0b's "quota risk:
        none" forbids.  Page order comes from ``page_cache`` (the same plan order
        the pipeline enumerated), so the rewrite is deterministic and idempotent.

        Keys whose chunk or ``page_id`` is absent from ``page_cache`` are left
        untouched rather than guessed at.

        Returns ``True`` when any key was rewritten.
        """
        # chunk index -> {model page_id: positional index}
        index_by_chunk: dict[int, dict[str, int]] = {}
        for cache_key, pageset in self.page_cache.items():
            try:
                chunk_index = int(cache_key)
            except (TypeError, ValueError):
                continue
            index_by_chunk[chunk_index] = {
                plan.page_id: position for position, plan in enumerate(pageset.pages)
            }

        def _parse(legacy_key: str) -> tuple[int, str] | None:
            if ":" not in legacy_key:
                return None
            prefix, page_id = legacy_key.split(":", 1)
            if not prefix.startswith("c"):
                return None
            try:
                chunk_index = int(prefix[1:])
            except ValueError:
                return None
            return chunk_index, page_id

        def _rewrite(legacy_key: str) -> str:
            parsed = _parse(legacy_key)
            if parsed is None:
                return legacy_key
            chunk_index, page_id = parsed
            position = index_by_chunk.get(chunk_index, {}).get(page_id)
            if position is None:
                return legacy_key
            return f"c{chunk_index:04d}-p{position:04d}"

        changed = False

        rewritten_pages: dict[str, GeneratedPage] = {}
        for key, record in self.generated.pages.items():
            new_key = _rewrite(key)
            if new_key != key:
                changed = True
            rewritten_pages[new_key] = record
        self.generated.pages = rewritten_pages

        for field_name in ("pages_done", "stale_pages", "skipped_pages"):
            current = getattr(self, field_name)
            updated = [_rewrite(k) for k in current]
            if updated != current:
                changed = True
            setattr(self, field_name, updated)

        rewritten_tombstones: dict[str, Tombstone] = {}
        for key, record in self.tombstones.items():
            new_key = _rewrite(key)
            if new_key != key:
                changed = True
            rewritten_tombstones[new_key] = record
        self.tombstones = rewritten_tombstones

        return changed
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_phase0b_legacy_migration.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add core/schemas.py tests/test_phase0b_legacy_migration.py
git commit -m "feat(schemas): migrate legacy page keys on load (0b)"
```

---

### Task 3: Pipeline uses positional page identity end to end

**Files:**
- Modify: `core/pipelines/creative_comic.py` (import, delete `_page_state_key`, 4 call sites)
- Modify: `tests/test_finished_page_pipeline.py`, `tests/test_identity_lockdown.py`, `tests/test_schemas_finished_page.py`, `tests/test_web_server.py`, `tests/test_cli_rebuild.py` (literal updates)
- Modify: `CHANGELOG.md`

**Interfaces:**
- Consumes: `core.comic.identity.page_state_key(chunk_index, page_index)` from Task 1.
- Produces: page state keys are `c{ci:04d}-p{idx:04d}` everywhere; `_page_state_key` no longer exists.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_phase0b_page_identity.py`:

```python
import asyncio
from unittest.mock import patch

from core.pipelines.creative_comic import creative_comic
from tests.test_finished_page_pipeline import FakeImage, FakeChat, _fake_export_pdf


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_finished_page_keys_are_positional(tmp_path, monkeypatch):
    """The state key is position, not the model's ``page_id`` (0b)."""
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "finished_page")
    src = "第一章\n福贵在村口。\n第二章\n福贵在读书。"
    proj = asyncio.run(
        creative_comic(src, output_dir=str(tmp_path), chat=FakeChat(), image=FakeImage())
    )

    assert "c0000-p0000" in proj.state.generated.pages
    assert "c0001-p0000" in proj.state.generated.pages
    assert all(":" not in key for key in proj.state.generated.pages)


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_key_is_unchanged_when_model_page_id_changes(tmp_path, monkeypatch):
    """A replan that renames ``page_id`` must not move the recorded key (0b)."""
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "finished_page")
    src = "第一章\n福贵在村口。"
    proj = asyncio.run(
        creative_comic(src, output_dir=str(tmp_path), chat=FakeChat(), image=FakeImage())
    )
    recorded = proj.state.generated.pages["c0000-p0000"]
    assert recorded.page_id == "u1_p0001"  # audited, but not identity

    rerun = asyncio.run(
        creative_comic(src, output_dir=str(tmp_path), chat=FakeChat(), image=FakeImage())
    )
    assert "c0000-p0000" in rerun.state.generated.pages
    assert rerun.state.pages_done == ["c0000-p0000"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_phase0b_page_identity.py -v`
Expected: FAIL — keys still contain `:` (assertion `"c0000-p0000" in proj.state.generated.pages` fails)

- [ ] **Step 3: Write minimal implementation**

In `core/pipelines/creative_comic.py`, extend the identity import block:

```python
from core.comic.identity import (
    ensure_character_l1,
    harden_human_identity_prompt,
    merge_settings,
    page_state_key,
    suggestion_from_alias,
)
```

Delete the now-superseded helper (lines 302-304), so no local page-key formatter can drift from the shared one:

```python
def _page_state_key(chunk_index: int, page_id: str) -> str:
    """Return the pipeline-owned identity for one (chunk, page_id) position."""
    return f"c{chunk_index:04d}:{page_id}"
```

Update the four call sites to use position:

In `previous_page_blank` (line 453):

```python
        prev_key = page_state_key(chunk_index, page_index - 1)
```

In `_page_chunk_complete` (line ~513):

```python
    for page_index, plan in enumerate(pageset.pages):
        state_key = page_state_key(chunk_index, page_index)
```

In `_mark_page_chunk_done_if_complete` (line ~537):

```python
    for page_index, plan in enumerate(pageset.pages):
        state_key = page_state_key(chunk_index, page_index)
```

In the page generation loop (lines 1287-1288):

```python
                page_id = plan.page_id
                state_key = page_state_key(ci, page_index)
```

Then update the test literals. Apply these exact swaps (the literal `c0000:does_not_exist` in `tests/test_cli_rebuild.py` is an intentionally-unknown key and must stay):

`tests/test_finished_page_pipeline.py` — replace every occurrence of `"c0000:u1_p0001"` with `"c0000-p0000"`, every `"c0001:u2_p0001"` with `"c0001-p0000"`, and the two sets at lines 666-667 from `{"c0000:p0001", "c0001:p0001"}` to `{"c0000-p0000", "c0001-p0000"}`.

`tests/test_identity_lockdown.py` line 273 — replace `"c0000:u1_p0002"` with `"c0000-p0001"`.

`tests/test_schemas_finished_page.py` — replace all three occurrences of `"c0000:p0001"` with `"c0000-p0000"` (lines 105, 107, 108).

`tests/test_web_server.py` — line 139 `pages_done=["c0000:u1_p0001"]` becomes `pages_done=["c0000-p0000"]`; line 152 `assert "c0000:u1_p0001" in snapshot["stale_pages"]` becomes `assert "c0000-p0000" in snapshot["stale_pages"]`.

`tests/test_cli_rebuild.py` — replace `"c0000:u1_p0001"` with `"c0000-p0000"` (lines 20, 46, 53, 69, 78, 81, 85) and `"c0001:u2_p0001"` with `"c0001-p0000"` (lines 79, 86).

Update `CHANGELOG.md` under the unreleased section:

```markdown
- Phase 0b: page identity is now positional (`c{ci:04d}-p{idx:04d}`), matching
  panel keys; the model-generated `page_id` is retained for prompts/audit but no
  longer participates in identity, so a replan that renames pages cannot orphan
  recorded pages. Legacy `c{ci:04d}:{page_id}` keys are migrated on load.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_phase0b_page_identity.py tests/test_finished_page_pipeline.py tests/test_identity_lockdown.py tests/test_schemas_finished_page.py tests/test_web_server.py tests/test_cli_rebuild.py -v`
Expected: PASS — including the two new positional tests

- [ ] **Step 5: Commit**

```bash
git add core/pipelines/creative_comic.py tests/test_finished_page_pipeline.py tests/test_identity_lockdown.py tests/test_schemas_finished_page.py tests/test_web_server.py tests/test_cli_rebuild.py CHANGELOG.md
git commit -m "refactor(pipeline): page identity is positional (0b)"
```

---

### Task 4: Wire legacy migration into every load site

**Files:**
- Modify: `core/pipelines/creative_comic.py` (after `ProjectState.load`, line ~936)
- Modify: `web/server.py` (`_load_project_state`, line ~485)
- Modify: `core/cli.py` (lines 251, 304)
- Test: `tests/test_phase0b_legacy_migration.py` (append)

**Interfaces:**
- Consumes: `ProjectState.migrate_legacy_page_keys()` from Task 2.
- Produces: no public interface change; legacy states are rewritten on read.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_phase0b_legacy_migration.py`:

```python
import json
from unittest.mock import patch

import web.server as server


def test_web_load_project_state_migrates_legacy_keys(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "OUTPUT_DIR", tmp_path)
    out = tmp_path / "legacy1"
    out.mkdir()
    (out / "state.json").write_text(_legacy_state().model_dump_json(), encoding="utf-8")

    _out_dir, loaded = server._load_project_state("legacy1")

    assert loaded.pages_done == ["c0000-p0000"]
    assert all(":" not in k for k in loaded.generated.pages)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_phase0b_legacy_migration.py::test_web_load_project_state_migrates_legacy_keys -v`
Expected: FAIL — `loaded.pages_done == ["c0000:u1_p0001"]`

- [ ] **Step 3: Write minimal implementation**

In `web/server.py`, replace the `_load_project_state` body:

```python
def _load_project_state(project_id: str) -> tuple[Path, ProjectState]:
    out_dir = _project_dir(project_id)
    state_path = out_dir / "state.json"
    if not state_path.is_file():
        raise FileNotFoundError(f"no state.json for project {project_id}")
    state = ProjectState.load(state_path)
    # Phase 0b: bring a pre-0b checkpoint onto positional page keys before any
    # reader (regen bookkeeping, snapshots) interprets them.
    if state.migrate_legacy_page_keys():
        state.save(state_path)
    return out_dir, state
```

In `core/pipelines/creative_comic.py`, right after `persisted = ProjectState.load(state_path)` (line ~936), add:

```python
        persisted = ProjectState.load(state_path)
        # Phase 0b: migrate pre-0b page keys before fingerprints/keys are read,
        # so a resumed project keeps its recorded pages and is not repainted.
        persisted.migrate_legacy_page_keys()
```

In `core/cli.py`, after each `state = ProjectState.load(state_path)` (lines 251 and 304), add:

```python
    state.migrate_legacy_page_keys()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_phase0b_legacy_migration.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add core/pipelines/creative_comic.py web/server.py core/cli.py tests/test_phase0b_legacy_migration.py
git commit -m "feat: migrate legacy page keys at every load site (0b)"
```

---

### Task 5: Cross-chunk blank lookup by recorded position, not key parsing

**Files:**
- Modify: `core/pipelines/creative_comic.py` (`previous_page_blank`, lines 443-473)
- Modify: `tests/test_identity_lockdown.py` (`test_previous_page_blank_crosses_chunks`, line ~255)

**Interfaces:**
- Consumes: `GeneratedPage.unit_index` / `GeneratedPage.page_index` (existing fields).
- Produces: `previous_page_blank(state, pageset, *, chunk_index, page_index) -> str | None` (signature unchanged).

- [ ] **Step 1: Write the failing test**

Replace `test_previous_page_blank_crosses_chunks` in `tests/test_identity_lockdown.py` with a case that would only pass if the lookup reads the recorded position (the legacy key here has a chunk prefix that does not match `unit_index`):

```python
def test_previous_page_blank_crosses_chunks():
    pageset = ComicPagePlanSet(
        unit_id="2",
        pages=[
            ComicPagePlan.model_validate(
                {
                    "page_id": "u2_p0001",
                    "purpose": "x",
                    "layout_intent": "y",
                    "panels": [{"panel_id": "1", "action": "walks"}],
                }
            )
        ],
    )
    state = ProjectState(
        project_id="p",
        generated=GeneratedAssets(
            pages={
                "c0000-p0001": GeneratedPage(
                    local="/tmp/lettered.png",
                    blank_local="/tmp/prev-chunk.png",
                    page_id="u1_p0002",
                    unit_index=0,
                    page_index=1,
                )
            }
        ),
    )
    assert previous_page_blank(state, pageset, chunk_index=1, page_index=0) == "/tmp/prev-chunk.png"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_identity_lockdown.py::test_previous_page_blank_crosses_chunks -v`
Expected: FAIL — the loop's `key.split(":", 1)` raises/`continue`s on a positional key, so it returns `None`

- [ ] **Step 3: Write minimal implementation**

Replace the cross-chunk candidate loop in `previous_page_blank` (lines 458-473) so it reads the recorded position instead of parsing the key string:

```python
    candidates: list[tuple[int, str, str]] = []
    for gen in state.generated.pages.values():
        if not gen.blank_local:
            continue
        if gen.unit_index < chunk_index:
            candidates.append((gen.unit_index, gen.page_index, gen.blank_local))
    if not candidates:
        return None
    candidates.sort()
    return candidates[-1][2]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_identity_lockdown.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add core/pipelines/creative_comic.py tests/test_identity_lockdown.py
git commit -m "refactor(pipeline): resolve previous-page blank by recorded position (0b)"
```

---

### Task 6: Alias-merge invalidation uses the shared formatter

**Files:**
- Modify: `core/comic/identity.py` (line 334)
- Test: `tests/test_phase0b_page_identity.py` (append)

**Interfaces:**
- Consumes: `page_state_key(chunk_index, page_index)` from Task 1 (same module).
- Produces: no interface change; alias merge now marks the correct positional keys.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_phase0b_page_identity.py`:

```python
from core.comic.identity import merge_character_alias
from core.schemas import (
    CharacterAsset,
    ComicPagePlan,
    ComicPagePlanSet,
    ProjectState,
)


def test_alias_merge_marks_positional_page_keys():
    pages = ComicPagePlanSet(
        unit_id="1",
        pages=[
            ComicPagePlan.model_validate(
                {"page_id": "u1_p0001", "panels": [{"panel_id": "1", "action": "a"}]}
            ),
            ComicPagePlan.model_validate(
                {
                    "page_id": "u1_p0002",
                    "reference_characters": ["福贵"],
                    "panels": [{"panel_id": "2", "action": "b", "characters": ["福贵"]}],
                }
            ),
        ],
    )
    state = ProjectState(
        project_id="p",
        characters={
            "徐福贵": CharacterAsset(name="徐福贵"),
            "福贵": CharacterAsset(name="福贵"),
        },
        page_cache={"0": pages},
        pages_done=["c0000-p0000", "c0000-p0001"],
    )

    merge_character_alias(state, "福贵", "徐福贵")

    assert state.stale_pages == ["c0000-p0001"]
    assert state.pages_done == ["c0000-p0000"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_phase0b_page_identity.py::test_alias_merge_marks_positional_page_keys -v`
Expected: FAIL — `stale_pages == ["c0000:u1_p0002"]`

- [ ] **Step 3: Write minimal implementation**

In `core/comic/identity.py`, replace the stale-page collection loop (lines 323-334) so the key comes from position:

```python
stale_pages: list[str] = []
for cache_key, pageset in state.page_cache.items():
    try:
        chunk_index = int(cache_key)
    except ValueError:
        continue
    for page_index, plan in enumerate(pageset.pages):
        names = set(plan.reference_characters)
        for panel in plan.panels:
            names.update(panel.characters)
        if new_name in names:
            stale_pages.append(page_state_key(chunk_index, page_index))
    for plan in pageset.pages:
        plan.reference_characters = _rewrite_names(plan.reference_characters, new_name, keep_name)
        for panel in plan.panels:
            panel.characters = _rewrite_names(panel.characters, new_name, keep_name)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_phase0b_page_identity.py tests/test_web_server.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add core/comic/identity.py tests/test_phase0b_page_identity.py
git commit -m "refactor(identity): alias merge marks positional page keys (0b)"
```

---

### Task 7: Regen entry point discriminates by render_mode

**Files:**
- Modify: `web/server.py` (`start_regen_job`, lines 522-531)
- Test: `tests/test_phase0b_page_identity.py` (append)

**Interfaces:**
- Consumes: `state.render_mode` (existing).
- Produces: no interface change.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_phase0b_page_identity.py`:

```python
import web.server as server
from core.schemas import ComicPagePlan, ComicPagePlanSet


def test_start_regen_job_treats_positional_page_keys_as_pages(tmp_path, monkeypatch):
    """A positional page key must reach stale_pages, never stale_panels (0b)."""
    monkeypatch.setattr(server, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(server, "_start_job", lambda *a, **k: ("job1", "proj1"))
    project_id = "proj1"
    out = tmp_path / project_id
    out.mkdir()
    (out / "source.txt").write_text("第一章\n福贵。", encoding="utf-8")
    page = ComicPagePlan(
        page_id="u1_p0001",
        reference_characters=["福贵"],
        panels=[PagePanelSpec(panel_id="p1", characters=["福贵"])],
    )
    state = ProjectState(
        project_id=project_id,
        render_mode="finished_page",
        page_cache={"0": ComicPagePlanSet(unit_id="u1", pages=[page])},
        stale_pages=["c0000-p0000"],
    )
    state.save(out / "state.json")

    server.start_regen_job(project_id, stale=True)

    reloaded = ProjectState.load(out / "state.json")
    assert reloaded.stale_pages == ["c0000-p0000"]
    assert reloaded.stale_panels == []
```

Add `PagePanelSpec` to the existing `core.schemas` import at the top of the file.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_phase0b_page_identity.py::test_start_regen_job_treats_positional_page_keys_as_pages -v`
Expected: FAIL — `reloaded.stale_panels == ["c0000-p0000"]` (the `":" not in k` test now passes positional page keys through)

- [ ] **Step 3: Write minimal implementation**

In `web/server.py`, replace the panel/page split in `start_regen_job`:

```python
    # Panel keys and page keys live in disjoint state fields and never coexist in
    # one project: ``render_mode`` selects exactly one render path (§0b). Route by
    # the mode, not by guessing from the key string — the positional page key
    # ``c0000-p0000`` is shape-identical to a panel key, so a delimiter heuristic
    # would misroute it into ``stale_panels`` and the repaint would be a no-op.
    if state.render_mode == "finished_page":
        state.stale_pages = sorted(set(state.stale_pages) | set(target_keys))
        state.pages_done = [k for k in state.pages_done if k not in set(target_keys)]
    else:
        force_regen_panels(state, target_keys)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_phase0b_page_identity.py tests/test_web_server.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add web/server.py tests/test_phase0b_page_identity.py
git commit -m "fix(web): route regen keys by render_mode, not key shape (0b)"
```

---

### Task 8: End-to-end proof that a legacy project is not repainted

**Files:**
- Test: `tests/test_phase0b_legacy_migration.py` (append)

**Interfaces:**
- Consumes: Tasks 1-4.
- Produces: nothing; this is the quota-safety regression net for 0b's "risk: none".

- [ ] **Step 1: Write the failing test**

Append to `tests/test_phase0b_legacy_migration.py`:

```python
import asyncio
from pathlib import Path
from unittest.mock import patch

from core.pipelines.creative_comic import creative_comic
from tests.test_finished_page_pipeline import FakeImage, FakeChat, _fake_export_pdf


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_resume_of_legacy_state_does_not_repaint(tmp_path, monkeypatch):
    """0b quota risk is none: a pre-0b checkpoint must not trigger a repaint."""
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "finished_page")
    src = "第一章\n福贵在村口。"

    first = FakeImage()
    proj = asyncio.run(creative_comic(src, output_dir=str(tmp_path), chat=FakeChat(), image=first))
    assert first.calls == 2  # 1 portrait + 1 page

    # Rewrite the checkpoint to the pre-0b colon format with a renamed page_id,
    # simulating a project created before 0b and replanned since.
    raw = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    raw["pages_done"] = ["c0000:u1_p0001"]
    raw["generated"]["pages"] = {"c0000:u1_p0001": raw["generated"]["pages"]["c0000-p0000"]}
    (tmp_path / "state.json").write_text(json.dumps(raw), encoding="utf-8")

    second = FakeImage()
    resumed = asyncio.run(
        creative_comic(src, output_dir=str(tmp_path), chat=FakeChat(), image=second)
    )

    assert second.calls == 0  # page kept, no repaint
    assert resumed.state.pages_done == ["c0000-p0000"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_phase0b_legacy_migration.py::test_resume_of_legacy_state_does_not_repaint -v`
Expected: FAIL — `second.calls == 1` (the legacy key is not recognised, so the page is repainted)

- [ ] **Step 3: Confirm the implementation is already in place**

No production code change is expected here: Task 2 provides `migrate_legacy_page_keys` and Task 4 calls it after `ProjectState.load`. If the test still fails, the defect is in Task 4's wiring (the migration is not running on the pipeline's load path) — fix that, do not weaken the assertion.

Run: `.venv/bin/python -m pytest tests/test_phase0b_legacy_migration.py::test_resume_of_legacy_state_does_not_repaint -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add tests/test_phase0b_legacy_migration.py
git commit -m "test: prove legacy checkpoint resume is not repainted (0b)"
```

---

### Task 9: Full suite, formatting, and design-doc status

**Files:**
- Modify: `docs/architecture/2026-09-28-content-addressed-pipeline-design.md` (0b row, local only — `docs/architecture/` is gitignored)

**Interfaces:**
- Consumes: everything above.
- Produces: a green suite and a truthful status marker.

- [ ] **Step 1: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (no failures, no errors). If a page-key literal was missed, a test will fail with a `c...:` key in the message — fix the literal, not the assertion.

- [ ] **Step 2: Format with the pinned ruff**

Run: `.venv/bin/ruff format core tests scripts`
Expected: `NNN files already formatted`, or a small diff scoped to the files touched here.

- [ ] **Step 3: Mark the 0b row done in the design doc**

In `docs/architecture/2026-09-28-content-addressed-pipeline-design.md`, append ` — **done** (positional keys, legacy migration on load)` inside the 0b row of the §9 phase table.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "chore: format and record 0b status (0b)"
```

---

## Self-Review

**Spec coverage:** §9 0b (positional identity, `page_id` out of identity, converge on panel convention) is covered by Tasks 1, 3, 6. §4's "a replan that renames pages must not orphan records" is covered by Task 3's `test_key_is_unchanged_when_model_page_id_changes` and Task 8. 0b's "quota risk: none" is covered by Tasks 2, 4, 8. The 0b note "confirm resolved item 17 within the same phase" belongs to 0d, not 0b, and is out of scope here.

**Placeholder scan:** No TBD/TODO; every code step shows full content; every test step shows runnable code and an exact command with a concrete expected result.

**Type consistency:** `page_state_key(chunk_index: int, page_index: int) -> str` is defined once in Task 1 and used with that signature in Tasks 3, 5, 6. `ProjectState.migrate_legacy_page_keys() -> bool` is defined in Task 2 and used in Task 4. The exact literal swaps are enumerated per file so the rename cannot half-land.