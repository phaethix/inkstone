# Phase 0d: Consistency Ledger Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the pipeline an explicit, authoritative `consistency.json` ledger recording which pages each character appears on, so a character's page set is a query rather than a cache walk, and Phase 1 has only to point tier-2 references at it.

**Architecture:** A new `core/comic/ledger.py` owns a `ConsistencyLedger` Pydantic model with atomic load/save, mirroring `ProjectState`'s file conventions but living in its own `consistency.json` (§6). The ledger is maintained at zero quota from `page_cache`, which the pipeline already produces. Alias merge queries the ledger instead of walking caches. The ledger enters no key or fingerprint (§8 invariant 3, §12 invariant 10); a behavioural test proves that.

**Tech Stack:** Python 3, Pydantic v2, pytest, ruff (pinned 0.16.6, run via the project venv).

## Global Constraints

- Design §9 row **0d**, verbatim: "Positional page identity + the consistency ledger schema, and **confirm resolved item 17 within the same phase** (the ledger doubles as the L2 reference source). The ledger is a plain data structure that can land before any billable rework, and it is a prerequisite for Phase 5a."
- Quota risk for 0d is **none** (§9 row 0d). No task may introduce a new billable call. The ledger is maintained from data the pipeline already has.
- Design §6, verbatim: `consistency.json` is a sibling of `state.json` in the project directory; it "enters no `action_key`" (§8 invariant 3, §12 invariant 10).
- Design §12 invariant 9, verbatim: "The consistency ledger is not derived from artifacts. It is authoritative state at the same level as `needs_review`, and losing it silently discards product intent."
- Page identities in the ledger are the 0b positional form `c{ci:04d}-p{idx:04d}` **only** — never a model `page_id` (§4).
- Run tests with `.venv/bin/python -m pytest`. Format with `.venv/bin/ruff format` (venv ruff is 0.16.6; `uvx ruff` resolves a newer version and can churn unrelated files — do not use it).
- Do not modify `core/schemas.py`'s `ProjectState` for the ledger. Keeping the ledger out of `ProjectState` is what makes invariant 10 structurally true rather than a promise.

---

## File Structure

- `core/comic/ledger.py` — **new**. The `ConsistencyLedger` model, its entry types, atomic load/save, `rebuild_from_state`, and the query/mutation methods. Imports only the `ProjectState` type it reads; owns no pipeline logic, so it is testable in isolation and cannot cycle with `identity.py` at import time.
- `core/pipelines/creative_comic.py` — loads the ledger after state resolution, records a reference version when a portrait is generated, and rebuilds/saves the ledger once a chunk's plans and pages are known.
- `core/comic/identity.py` — `merge_character_alias` lists shared-character pages from the ledger (falling back to its current `page_cache` walk when the ledger has no entry), and renames the ledger key.
- `web/server.py` — the job snapshot gains `ledger_pending`, an aggregate of character → pending page set.
- `core/cli.py` — implements the `identity` subcommand's `--view` (currently a D3 placeholder) to print the ledger.
- Tests: `tests/test_phase0d_ledger.py` (new), `tests/test_phase0d_integration.py` (new), plus one assertion added to `tests/test_identity_lockdown.py`.

---

### Task 1: Ledger model and atomic persistence

**Files:**
- Create: `core/comic/ledger.py`
- Test: `tests/test_phase0d_ledger.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `ReferenceVersion`, `LedgerEntry`, `ConsistencyLedger` (Pydantic models); `ConsistencyLedger.load(path) -> ConsistencyLedger`; `ConsistencyLedger.save(path) -> None`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_phase0d_ledger.py`:

```python
"""Phase 0d: the consistency ledger schema and persistence (§12)."""

from pathlib import Path

from core.comic.ledger import ConsistencyLedger, LedgerEntry, ReferenceVersion


def test_empty_ledger_round_trips(tmp_path: Path):
    led = ConsistencyLedger()
    path = tmp_path / "consistency.json"
    led.save(path)
    loaded = ConsistencyLedger.load(path)
    assert loaded == led
    assert loaded.schema_version == 1


def test_entry_round_trips(tmp_path: Path):
    led = ConsistencyLedger(
        characters={
            "方鸿渐": LedgerEntry(
                pages=["c0000-p0002", "c0001-p0005"],
                reference=ReferenceVersion(
                    path="assets/portraits/方鸿渐.png", content_hash=None, version=1
                ),
                pending_pages=["c0001-p0005"],
            )
        }
    )
    path = tmp_path / "consistency.json"
    led.save(path)
    loaded = ConsistencyLedger.load(path)
    assert loaded == led
    assert loaded.characters["方鸿渐"].reference.version == 1
    assert loaded.characters["方鸿渐"].reference.content_hash is None


def test_load_missing_file_returns_empty(tmp_path: Path):
    loaded = ConsistencyLedger.load(tmp_path / "absent.json")
    assert loaded.characters == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_phase0d_ledger.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'core.comic.ledger'`

- [ ] **Step 3: Write minimal implementation**

Create `core/comic/ledger.py`:

```python
"""core.comic.ledger — the explicit consistency ledger (§12).

Records *which pages must be visually consistent with each other*. It is
authoritative state, not derived from artifacts (§12 invariant 9): it is loaded
and saved like ``state.json``, and it enters no ``action_key`` or fingerprint
(§8 invariant 3, §12 invariant 10). Page identities are the 0b positional form
``c{ci:04d}-p{idx:04d}`` only — never a model ``page_id`` (§4).

Phase 0d lands the schema, maintenance, and queries with zero quota and no CAS.
Phase 1 points the §4 tier-2 reference at this ledger's authoritative version;
``content_hash`` is reserved for that and stays ``None`` until the CAS exists.
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ReferenceVersion(BaseModel):
    """A character's authoritative reference image and its version."""

    model_config = ConfigDict(extra="ignore")

    path: str = ""
    # Reserved for Phase 1: the CAS content hash. ``None`` until the CAS lands.
    content_hash: str | None = None
    version: int = 0


class LedgerEntry(BaseModel):
    """What the ledger knows about one character."""

    model_config = ConfigDict(extra="ignore")

    # Positional page identities the character appears on (§4).
    pages: list[str] = Field(default_factory=list)
    reference: ReferenceVersion = Field(default_factory=ReferenceVersion)
    # Pages awaiting review because the reference version changed (§12).
    pending_pages: list[str] = Field(default_factory=list)
    # The reference version a human has already reviewed; pending is the set of
    # pages for versions strictly above this (Phase 5b raises it on acceptance).
    reviewed_version: int = 0
    updated_at: str = Field(default_factory=_now_iso)


class ConsistencyLedger(BaseModel):
    """The consistency ledger persisted to ``consistency.json`` (§6)."""

    model_config = ConfigDict(extra="ignore")

    schema_version: int = 1
    characters: dict[str, LedgerEntry] = Field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path) -> "ConsistencyLedger":
        """Load the ledger; a missing file yields an empty ledger.

        The ledger is reconstructible from ``page_cache`` (§12 invariant 9), so
        a missing file is not an error.
        """
        p = Path(path)
        if not p.is_file():
            return cls()
        return cls.model_validate_json(p.read_text(encoding="utf-8"))

    def save(self, path: str | Path) -> None:
        """Persist atomically, so interruption cannot truncate the ledger."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f".{p.name}.", dir=p.parent, text=True)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as temp:
                temp.write(self.model_dump_json(indent=2))
                temp.flush()
                os.fsync(temp.fileno())
            os.replace(temp_name, p)
        except Exception:
            Path(temp_name).unlink(missing_ok=True)
            raise
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_phase0d_ledger.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add core/comic/ledger.py tests/test_phase0d_ledger.py
git commit -m "feat(ledger): add the consistency ledger schema and atomic IO (0d)"
```

---

### Task 2: Rebuild page sets and pending review from `page_cache`

**Files:**
- Modify: `core/comic/ledger.py`
- Test: `tests/test_phase0d_ledger.py`

**Interfaces:**
- Consumes: `ConsistencyLedger` from Task 1; `ProjectState.page_cache` (`core.schemas`).
- Produces: `ConsistencyLedger.rebuild_from_state(state: ProjectState) -> bool` — `True` when anything changed. It reads `state.page_cache` only; it never issues a call.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_phase0d_ledger.py`:

```python
from core.schemas import ComicPagePlan, ComicPagePlanSet, PagePanelSpec, ProjectState


def _plan(names: list[str], page_id: str = "p") -> ComicPagePlan:
    return ComicPagePlan(
        page_id=page_id,
        reference_characters=list(names),
        panels=[PagePanelSpec(panel_id="pan1", characters=list(names))],
    )


def _state_with(chunks: dict[str, list[list[str]]]) -> ProjectState:
    state = ProjectState(project_id="t")
    for chunk_key, pages in chunks.items():
        state.page_cache[chunk_key] = ComicPagePlanSet(
            unit_id=chunk_key,
            pages=[_plan(names, page_id=f"pg{i}") for i, names in enumerate(pages)],
        )
    return state


def test_rebuild_derives_positional_page_sets():
    state = _state_with({"0": [["A", "B"], ["A"]], "1": [["B"]]})
    led = ConsistencyLedger()
    assert led.rebuild_from_state(state) is True
    assert led.characters["A"].pages == ["c0000-p0000", "c0000-p0001"]
    assert led.characters["B"].pages == ["c0000-p0000", "c0001-p0000"]


def test_rebuild_is_idempotent_and_returns_false_on_no_change():
    state = _state_with({"0": [["A"]]})
    led = ConsistencyLedger()
    assert led.rebuild_from_state(state) is True
    assert led.rebuild_from_state(state) is False


def test_rebuild_strips_stage_suffix_from_names():
    state = _state_with({"0": [["方鸿渐@adult"]]})
    led = ConsistencyLedger()
    led.rebuild_from_state(state)
    assert "方鸿渐" in led.characters
    assert "方鸿渐@adult" not in led.characters


def test_rebuild_refreshes_pending_only_above_reviewed_version():
    state = _state_with({"0": [["A"]]})
    led = ConsistencyLedger(characters={"A": LedgerEntry(pages=["c0000-p0000"])})
    led.characters["A"].reference = ReferenceVersion(path="p.png", version=2)
    led.characters["A"].reviewed_version = 1
    led.rebuild_from_state(state)
    assert led.characters["A"].pending_pages == ["c0000-p0000"]
    # A human reviewed version 2 -> pending clears without touching pages.
    led.characters["A"].reviewed_version = 2
    led.rebuild_from_state(state)
    assert led.characters["A"].pending_pages == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_phase0d_ledger.py -k rebuild -v`
Expected: FAIL with `AttributeError: 'ConsistencyLedger' object has no attribute 'rebuild_from_state'`

- [ ] **Step 3: Write minimal implementation**

Add `from core.schemas import ProjectState` to the imports at the top of `core/comic/ledger.py` (after the pydantic import), then append the module-level helper and the method:

```python
def _base_name(name: str) -> str:
    """Strip a ``Name@stage`` suffix so a stage portrait maps to its character."""
    return (name or "").split("@", 1)[0].strip()


def _pages_by_character(state: ProjectState) -> dict[str, list[str]]:
    """Derive, per character, the positional page ids it appears on (§12).

    Reads ``page_cache`` — the plans the pipeline already produced — so the
    derivation is zero-quota. Each character's list is de-duplicated.
    """
    from core.comic.identity import page_state_key  # local: avoid an import cycle

    out: dict[str, list[str]] = {}
    for cache_key, pageset in state.page_cache.items():
        try:
            chunk_index = int(cache_key)
        except (TypeError, ValueError):
            continue
        for page_index, plan in enumerate(pageset.pages):
            key = page_state_key(chunk_index, page_index)
            names: list[str] = list(plan.reference_characters)
            for panel in plan.panels:
                names.extend(panel.characters)
            for raw in names:
                name = _base_name(raw)
                if not name:
                    continue
                bucket = out.setdefault(name, [])
                if key not in bucket:
                    bucket.append(key)
    return out
```

Then add this method inside `ConsistencyLedger` (after `save`):

```python
def rebuild_from_state(self, state: ProjectState) -> bool:
    """Refresh every character's ``pages`` and pending set from ``page_cache``.

    Idempotent and zero-quota. ``pages`` is derived; ``pending_pages`` is
    recomputed as "pages for a reference version above the reviewed one", so
    it is independent of whether a portrait was regenerated before or after
    the plans were known. A character's ``reference`` is never cleared here:
    it records a product decision (§12 invariant 9), not a derivation.
    Returns ``True`` when anything changed.
    """
    derived = _pages_by_character(state)
    changed = False
    for name, pages in derived.items():
        entry = self.characters.get(name)
        if entry is None:
            entry = LedgerEntry(pages=list(pages))
            self.characters[name] = entry
            changed = True
        elif entry.pages != pages:
            entry.pages = list(pages)
            entry.updated_at = _now_iso()
            changed = True
        pending = list(entry.pages) if entry.reference.version > entry.reviewed_version else []
        if entry.pending_pages != pending:
            entry.pending_pages = pending
            entry.updated_at = _now_iso()
            changed = True
    return changed
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_phase0d_ledger.py -k rebuild -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add core/comic/ledger.py tests/test_phase0d_ledger.py
git commit -m "feat(ledger): derive page sets and pending review from page_cache (0d)"
```

---

### Task 3: Tolerate a corrupt ledger and reconstruct it

**Files:**
- Modify: `core/comic/ledger.py`
- Test: `tests/test_phase0d_ledger.py`

**Interfaces:**
- Consumes: `ConsistencyLedger.load` / `rebuild_from_state` from Tasks 1-2.
- Produces: `ConsistencyLedger.load_or_rebuild(path: str | Path, state: ProjectState) -> ConsistencyLedger`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_phase0d_ledger.py`:

```python
def test_load_or_rebuild_recovers_from_corruption(tmp_path: Path):
    path = tmp_path / "consistency.json"
    path.write_text("{ this is not json", encoding="utf-8")
    state = _state_with({"0": [["A"]]})
    led = ConsistencyLedger.load_or_rebuild(path, state)
    assert led.characters["A"].pages == ["c0000-p0000"]


def test_load_or_rebuild_prefers_a_valid_file(tmp_path: Path):
    path = tmp_path / "consistency.json"
    ConsistencyLedger(characters={"A": LedgerEntry(pages=["c9999-p9999"])}).save(path)
    state = _state_with({"0": [["A"]]})
    led = ConsistencyLedger.load_or_rebuild(path, state)
    # A valid file is authoritative: it is not overwritten by a rebuild.
    assert led.characters["A"].pages == ["c9999-p9999"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_phase0d_ledger.py -k load_or_rebuild -v`
Expected: FAIL with `AttributeError: 'ConsistencyLedger' object has no attribute 'load_or_rebuild'`

- [ ] **Step 3: Write minimal implementation**

Add this classmethod to `ConsistencyLedger` in `core/comic/ledger.py`:

```python
    @classmethod
    def load_or_rebuild(
        cls,
        path: str | Path,
        state: ProjectState,
    ) -> "ConsistencyLedger":
        """Load the ledger; reconstruct it when the file is absent or corrupt.

        A corrupt ledger must not block a resumable run (§12 invariant 9 is about
        authority, not fragility): fall back to an empty ledger rebuilt from
        ``page_cache``. A *valid* file wins — it may hold human decisions a
        rebuild cannot reproduce.
        """
        p = Path(path)
        if p.is_file():
            try:
                return cls.load(p)
            except Exception:  # noqa: BLE001 - a corrupt ledger is recoverable
                pass
        led = cls()
        led.rebuild_from_state(state)
        return led
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_phase0d_ledger.py -k load_or_rebuild -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add core/comic/ledger.py tests/test_phase0d_ledger.py
git commit -m "feat(ledger): recover from a corrupt or absent ledger (0d)"
```

---

### Task 4: Reference versions, pending query, and rename

**Files:**
- Modify: `core/comic/ledger.py`
- Test: `tests/test_phase0d_ledger.py`

**Interfaces:**
- Consumes: `ConsistencyLedger` from Tasks 1-3.
- Produces:
  - `record_reference(name: str, path: str, content_hash: str | None = None) -> None` — bumps `version`, sets `path`/`content_hash`. Does not touch `pending_pages`; that is `rebuild_from_state`'s job.
  - `pending_pages() -> dict[str, list[str]]` — only characters with a non-empty pending set.
  - `rename_character(old: str, new: str) -> None` — merges an alias's entry into the kept character's.
  - `pages_for(name: str) -> list[str]`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_phase0d_ledger.py`:

```python
def test_record_reference_bumps_version_without_touching_pending():
    led = ConsistencyLedger(characters={"A": LedgerEntry(pages=["c0000-p0000"])})
    led.record_reference("A", "assets/portraits/A.png")
    assert led.characters["A"].reference.version == 1
    assert led.characters["A"].reference.path == "assets/portraits/A.png"
    # Pending is reconciled by rebuild/pipeline, not by the write itself.
    assert led.characters["A"].pending_pages == []
    led.record_reference("A", "assets/portraits/A.png")
    assert led.characters["A"].reference.version == 2


def test_pending_pages_lists_only_non_empty():
    led = ConsistencyLedger(
        characters={
            "A": LedgerEntry(pages=["c0000-p0000"], pending_pages=["c0000-p0000"]),
            "B": LedgerEntry(pages=["c0000-p0001"]),
        }
    )
    assert led.pending_pages() == {"A": ["c0000-p0000"]}


def test_pages_for_missing_character_is_empty():
    assert ConsistencyLedger().pages_for("nobody") == []


def test_rename_character_unions_pages_and_pending():
    led = ConsistencyLedger(
        characters={
            "别名": LedgerEntry(pages=["c0000-p0000"], pending_pages=["c0000-p0000"]),
            "A": LedgerEntry(pages=["c0001-p0000"]),
        }
    )
    led.rename_character("别名", "A")
    assert "别名" not in led.characters
    assert led.characters["A"].pages == ["c0001-p0000", "c0000-p0000"]
    assert led.characters["A"].pending_pages == ["c0000-p0000"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_phase0d_ledger.py -k "record_reference or pending or rename or pages_for" -v`
Expected: FAIL with `AttributeError: 'ConsistencyLedger' object has no attribute 'record_reference'`

- [ ] **Step 3: Write minimal implementation**

Add these methods to `ConsistencyLedger` in `core/comic/ledger.py`:

```python
def pages_for(self, name: str) -> list[str]:
    entry = self.characters.get(name)
    return list(entry.pages) if entry is not None else []


def record_reference(
    self,
    name: str,
    path: str,
    content_hash: str | None = None,
) -> None:
    """Record that ``name``'s authoritative reference was (re)generated.

    Bumps the reference version; ``rebuild_from_state`` then marks the
    character's pages pending for review. Called on a real generation event,
    not on a mere path comparison, because a regeneration overwrites the same
    path and only the event distinguishes it.
    """
    entry = self.characters.get(name)
    if entry is None:
        entry = LedgerEntry()
        self.characters[name] = entry
    entry.reference = ReferenceVersion(
        path=path,
        content_hash=content_hash,
        version=entry.reference.version + 1,
    )
    entry.updated_at = _now_iso()


def pending_pages(self) -> dict[str, list[str]]:
    """Characters with pending pages, for the aggregated UI panel (§4)."""
    return {
        name: list(entry.pending_pages)
        for name, entry in self.characters.items()
        if entry.pending_pages
    }


def rename_character(self, old: str, new: str) -> None:
    """Merge ``old``'s entry into ``new`` (alias merge, §12 rule 3)."""
    if old == new:
        return
    incoming = self.characters.pop(old, None)
    if incoming is None:
        return
    target = self.characters.get(new)
    if target is None:
        self.characters[new] = incoming
        return
    for key in incoming.pages:
        if key not in target.pages:
            target.pages.append(key)
    for key in incoming.pending_pages:
        if key not in target.pending_pages:
            target.pending_pages.append(key)
    target.reviewed_version = max(target.reviewed_version, incoming.reviewed_version)
    if incoming.reference.version > target.reference.version:
        target.reference = incoming.reference
    target.updated_at = _now_iso()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_phase0d_ledger.py -v`
Expected: PASS (all ledger tests)

- [ ] **Step 5: Commit**

```bash
git add core/comic/ledger.py tests/test_phase0d_ledger.py
git commit -m "feat(ledger): reference versions, pending query, and rename (0d)"
```

---

### Task 5: Alias merge lists shared pages from the ledger

**Files:**
- Modify: `core/comic/identity.py`
- Test: `tests/test_phase0d_integration.py` (new)

**Interfaces:**
- Consumes: `ConsistencyLedger.pages_for` / `rename_character` from Task 4.
- Produces: `merge_character_alias(state, new_name, keep_name, ledger: ConsistencyLedger | None = None) -> list[str]`. When `ledger` is given, the page set comes from the ledger and the ledger key is renamed; the existing `page_cache` walk remains the fallback when the ledger has no entry for `new_name`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_phase0d_integration.py`:

```python
"""Phase 0d: ledger integration with alias merge, pipeline, and the web snapshot."""

from core.comic.identity import merge_character_alias
from core.comic.ledger import ConsistencyLedger, LedgerEntry
from core.schemas import CharacterAsset, ProjectState


def test_alias_merge_lists_shared_pages_from_ledger():
    state = ProjectState(project_id="t")
    state.characters["鸿渐"] = CharacterAsset(name="鸿渐")
    state.characters["方鸿渐"] = CharacterAsset(name="方鸿渐")
    led = ConsistencyLedger(
        characters={
            "鸿渐": LedgerEntry(pages=["c0000-p0003"]),
            "方鸿渐": LedgerEntry(pages=["c0001-p0000"]),
        }
    )
    stale = merge_character_alias(state, "鸿渐", "方鸿渐", ledger=led)
    assert stale == []
    assert "c0000-p0003" in state.stale_pages
    assert "鸿渐" not in led.characters
    assert led.characters["方鸿渐"].pages == ["c0001-p0000", "c0000-p0003"]


def test_alias_merge_without_ledger_keeps_cache_walk():
    state = ProjectState(project_id="t")
    state.characters["a"] = CharacterAsset(name="a")
    state.characters["b"] = CharacterAsset(name="b")
    # No page_cache entry -> nothing to invalidate; must not raise.
    assert merge_character_alias(state, "a", "b") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_phase0d_integration.py -v`
Expected: FAIL with `TypeError: merge_character_alias() got an unexpected keyword argument 'ledger'`

- [ ] **Step 3: Write minimal implementation**

In `core/comic/identity.py`, add the import near the other `core.schemas` import:

```python
from core.comic.ledger import ConsistencyLedger
```

Change the `merge_character_alias` signature and the page-key block. The current block begins at `stale_pages: list[str] = []` and the loop over `state.page_cache.items()`; replace it with a ledger-first, cache-fallback version:

```python
def merge_character_alias(
    state: ProjectState,
    new_name: str,
    keep_name: str,
    ledger: ConsistencyLedger | None = None,
) -> list[str]:
```

and, inside, replace:

```python
stale = _panel_keys_referencing(state, new_name)
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

with:

```python
stale = _panel_keys_referencing(state, new_name)
# §12 consequence 3: prefer the ledger's precise page set for the alias;
# fall back to walking page_cache when the ledger has no entry for it, so a
# project that has not yet rebuilt its ledger still invalidates correctly.
ledger_pages = ledger.pages_for(new_name) if ledger is not None else []
stale_pages: list[str] = list(ledger_pages)
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
            key = page_state_key(chunk_index, page_index)
            if key not in stale_pages:
                stale_pages.append(key)
    for plan in pageset.pages:
        plan.reference_characters = _rewrite_names(plan.reference_characters, new_name, keep_name)
        for panel in plan.panels:
            panel.characters = _rewrite_names(panel.characters, new_name, keep_name)
if ledger is not None:
    ledger.rename_character(new_name, keep_name)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_phase0d_integration.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add core/comic/identity.py tests/test_phase0d_integration.py
git commit -m "feat(identity): alias merge lists shared pages from the ledger (0d)"
```

---

### Task 6: Maintain the ledger in the pipeline

**Files:**
- Modify: `core/pipelines/creative_comic.py`
- Test: `tests/test_phase0d_integration.py`

**Interfaces:**
- Consumes: `ConsistencyLedger.load_or_rebuild` / `record_reference` / `rebuild_from_state` / `save` from Tasks 1-4.
- Produces: side effects only. After a run, `output_dir/consistency.json` exists and maps each character to its positional page set; a character whose portrait was generated for the first time has reference `version == 1` and **empty** `pending_pages` (nothing to re-review yet — §12 invariant 11 rewards quiet); a character whose portrait was regenerated has `version == 2` and `pending_pages ==` its page set.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_phase0d_integration.py`:

```python
import asyncio

from core.comic.ledger import ConsistencyLedger
from core.pipelines.creative_comic import creative_comic


def test_pipeline_writes_ledger_with_positional_pages(tmp_path):
    from tests.test_finished_page_pipeline import FakeChat, FakeImage

    src = "第一章\n福贵在村口。"
    asyncio.run(creative_comic(src, output_dir=str(tmp_path), chat=FakeChat(), image=FakeImage()))
    led = ConsistencyLedger.load(tmp_path / "consistency.json")
    assert "福贵" in led.characters
    pages = led.characters["福贵"].pages
    assert pages and all(p.startswith("c") and "-p" in p for p in pages)
    # First generation records version 1 and does NOT flag every page pending.
    assert led.characters["福贵"].reference.version == 1
    assert led.characters["福贵"].pending_pages == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_phase0d_integration.py::test_pipeline_writes_ledger_with_positional_pages -v`
Expected: FAIL with `FileNotFoundError` (no `consistency.json` written)

- [ ] **Step 3: Write minimal implementation**

At the top of `core/pipelines/creative_comic.py`, add the import beside the other `core.comic` imports:

```python
from core.comic.ledger import ConsistencyLedger
```

Right after `state_path = output_dir / "state.json"` (near the top of `_creative_comic`), define the ledger path and load it once, after `state` is resolved. Because `state` is assigned later (the `if state_path.exists()` block), add the load immediately after `state.render_mode = mode`:

```python
    ledger_path = output_dir / "consistency.json"
    ledger = ConsistencyLedger.load_or_rebuild(ledger_path, state)
```

In the portrait success loop, immediately after `state.generated.portraits[name] = path`, record the reference with first-generation-aware semantics:

```python
if ledger.characters.get(name) is None or ledger.pages_for(name) == []:
    # First-ever portrait, or no pages known yet: establish the
    # reference without flagging pages we cannot yet enumerate.
    ledger.record_reference(name, path)
    ledger.characters[name].reviewed_version = ledger.characters[name].reference.version
else:
    # A regeneration of an already-referenced character: bump the
    # version so rebuild marks that character's pages pending.
    ledger.record_reference(name, path)
```

After each page completes (immediately after `state.generated.pages[state_key] = GeneratedPage(...)` and `_mark_page_done(state, state_key)`), and once more after `_mark_page_chunk_done_if_complete(...)`, rebuild and save the ledger:

```python
                if ledger.rebuild_from_state(state):
                    ledger.save(ledger_path)
```

and

```python
            _mark_page_chunk_done_if_complete(state, key, pageset, ci)
            ledger.rebuild_from_state(state)
            ledger.save(ledger_path)
            state.save(state_path)
            _report("pages", _pct())
            continue
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_phase0d_integration.py -v`
Expected: PASS

- [ ] **Step 5: Run the full suite to confirm no regression**

Run: `.venv/bin/python -m pytest -q`
Expected: all previously passing tests still pass.

- [ ] **Step 6: Commit**

```bash
git add core/pipelines/creative_comic.py tests/test_phase0d_integration.py
git commit -m "feat(pipeline): maintain consistency.json during a run (0d)"
```

---

### Task 7: Expose ledger pending pages in the web snapshot

**Files:**
- Modify: `web/server.py`
- Test: `tests/test_phase0d_integration.py`

**Interfaces:**
- Consumes: `ConsistencyLedger.load_or_rebuild` from Task 3.
- Produces: `_state_snapshot(state)` gains a `"ledger_pending"` key, a `dict[str, list[str]]` (character → pending page set), sourced from the ledger reconstructed against `state`. It is empty when the ledger has nothing pending.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_phase0d_integration.py`:

```python
def test_state_snapshot_includes_ledger_pending(monkeypatch, tmp_path):
    from web import server

    monkeypatch.setattr(server, "OUTPUT_DIR", tmp_path)
    state = ProjectState(project_id="p1")
    snap = server._state_snapshot(state)
    assert snap["ledger_pending"] == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_phase0d_integration.py::test_state_snapshot_includes_ledger_pending -v`
Expected: FAIL with `KeyError: 'ledger_pending'`

- [ ] **Step 3: Write minimal implementation**

In `web/server.py`, add the import near the other `core` imports:

```python
from core.comic.ledger import ConsistencyLedger
```

Change `_state_snapshot` to accept the project directory so it can locate the ledger:

```python
def _state_snapshot(state: ProjectState, out_dir: Path | None = None) -> dict:
    if out_dir is None:
        out_dir = OUTPUT_DIR / state.project_id
    ledger = ConsistencyLedger.load_or_rebuild(out_dir / "consistency.json", state)
    return {
        "skipped": list(state.skipped),
        "skipped_chunks": list(state.skipped_chunks),
        "needs_review": [s.model_dump() for s in state.needs_review],
        "stale_panels": list(state.stale_panels),
        "stale_pages": list(state.stale_pages),
        "render_mode": state.render_mode,
        "pages_done": list(state.pages_done),
        "skipped_pages": list(state.skipped_pages),
        "ledger_pending": ledger.pending_pages(),
    }
```

The one existing internal caller, `_load_project_state`, already has `out_dir`; pass it through:

```python
    state = ProjectState.load(state_path)
    if state.migrate_legacy_page_keys():
        state.save(state_path)
    return out_dir, state
```

and in `apply_review`, replace `_state_snapshot(state)` with `_state_snapshot(state, out_dir)`. Leave the two `job.update(_state_snapshot(proj.state))` call sites unchanged: the default `out_dir` resolves from `OUTPUT_DIR / project_id`, which is correct there.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_phase0d_integration.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add web/server.py tests/test_phase0d_integration.py
git commit -m "feat(web): surface ledger pending pages in the snapshot (0d)"
```

---

### Task 8: `identity --view` prints the ledger

**Files:**
- Modify: `core/cli.py`
- Test: `tests/test_phase0d_integration.py`

**Interfaces:**
- Consumes: `ConsistencyLedger.load_or_rebuild` from Task 3.
- Produces: `_run_identity(args) -> int`, printing one line per character with its page count and pending count; returns 0 on success, 1 when `state.json` is missing.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_phase0d_integration.py`:

```python
def test_identity_view_prints_ledger(tmp_path, capsys):
    from core import cli

    out = tmp_path / "proj"
    out.mkdir()
    ProjectState(project_id="p1").save(out / "state.json")
    ConsistencyLedger(characters={"福贵": LedgerEntry(pages=["c0000-p0000"])}).save(
        out / "consistency.json"
    )
    code = cli._run_identity(type("A", (), {"out": str(out), "view": True})())
    assert code == 0
    assert "福贵" in capsys.readouterr().out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_phase0d_integration.py::test_identity_view_prints_ledger -v`
Expected: FAIL with `AttributeError: module 'core.cli' has no attribute '_run_identity'`

- [ ] **Step 3: Write minimal implementation**

In `core/cli.py`, add the import near the other `core` imports:

```python
from core.comic.ledger import ConsistencyLedger
```

Add the `--out` option to the `identity` parser (it currently only has `--view` and `--merge`):

```python
    p_id = sub.add_parser("identity", help="Identity ledger: view characters and pending pages")
    p_id.add_argument("--view", action="store_true", help="Print the consistency ledger")
    p_id.add_argument("--merge", default=None, help="Merge alias: 'new:keep'")
    p_id.add_argument(
        "--out",
        default="comic_out",
        help="Generation output directory (contains consistency.json; default comic_out)",
    )
```

Add the handler near `_run_coverage`:

```python
def _run_identity(args: argparse.Namespace) -> int:
    """identity 子命令：打印一致性账本（§12）。"""
    out = Path(args.out)
    state_path = out / "state.json"
    if not state_path.exists():
        print(f"state.json 未找到：{args.out}（请先运行 generate 或指定 --out）")
        return 1
    state = ProjectState.load(state_path)
    state.migrate_legacy_page_keys()
    ledger = ConsistencyLedger.load_or_rebuild(out / "consistency.json", state)
    if not ledger.characters:
        print("账本为空：尚无可显示的角色页集。")
        return 0
    print(f"一致性账本：{len(ledger.characters)} 个角色")
    for name in sorted(ledger.characters):
        entry = ledger.characters[name]
        pending = len(entry.pending_pages)
        ref = entry.reference.version
        print(f"  - {name}: {len(entry.pages)} 页，参考版本 v{ref}，待复核 {pending} 页")
    return 0
```

Wire it in `main`, replacing the `_not_implemented("identity", "D3")` branch:

```python
    elif args.command == "identity":
        sys.exit(_run_identity(args))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_phase0d_integration.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add core/cli.py tests/test_phase0d_integration.py
git commit -m "feat(cli): identity --view prints the consistency ledger (0d)"
```

---

### Task 9: Prove the ledger enters no key or fingerprint

**Files:**
- Modify: `core/comic/identity.py` (only if the guard fails)
- Test: `tests/test_phase0d_integration.py`

**Interfaces:**
- Consumes: `_render_fingerprint` from `core.pipelines.creative_comic`; `ConsistencyLedger`.
- Produces: no production change expected. This task is a **regression guard** for §8 invariant 3 / §12 invariant 10: mutating the ledger must not change any fingerprint the pipeline computes.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_phase0d_integration.py`:

```python
def test_ledger_contents_do_not_enter_the_render_fingerprint():
    from core.pipelines.creative_comic import _render_fingerprint
    from core.schemas import ModelSnapshot

    snapshot = ModelSnapshot()
    before = _render_fingerprint(
        "manhua", snapshot=snapshot, panel_continuity=True, l3_enabled=False
    )
    # A ledger full of content must not perturb the fingerprint inputs at all.
    ConsistencyLedger(
        characters={"A": LedgerEntry(pages=["c0000-p0000"], pending_pages=["c0000-p0000"])}
    )
    after = _render_fingerprint(
        "manhua", snapshot=snapshot, panel_continuity=True, l3_enabled=False
    )
    assert before == after
```

- [ ] **Step 2: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_phase0d_integration.py::test_ledger_contents_do_not_enter_the_render_fingerprint -v`
Expected: PASS. `_render_fingerprint` takes only declared inputs and the ledger is not among them; the guard fails only if a future change wires the ledger into the fingerprint, which is exactly what it must catch.

- [ ] **Step 3: Run the full suite and the linters**

Run: `.venv/bin/python -m pytest -q && .venv/bin/ruff check . && .venv/bin/ruff format --check .`
Expected: all tests pass; `ruff` reports no issues. If `ruff format --check` lists files, run `.venv/bin/ruff format` on only the files this plan touched and re-check.

- [ ] **Step 4: Commit**

```bash
git add tests/test_phase0d_integration.py
git commit -m "test(ledger): guard that the ledger enters no fingerprint (0d)"
```

---

### Task 10: Update the roadmap

**Files:**
- Modify: `docs/ROADMAP.md`

**Interfaces:**
- Consumes: nothing.
- Produces: documentation only.

- [ ] **Step 1: Record 0d's landing**

In `docs/ROADMAP.md`, under "P0 — Make the current state honest and deterministic", add a line recording that the consistency ledger schema, its maintenance from `page_cache`, and the `identity --view` surface landed as Phase 0d, with `consistency.json` noted as authoritative and CAS-independent.

- [ ] **Step 2: Commit**

```bash
git add docs/ROADMAP.md docs/superpowers/plans/2026-09-30-phase0d-consistency-ledger.md docs/superpowers/specs/2026-09-30-consistency-ledger-design.md
git commit -m "docs: record the 0d consistency ledger landing"
```