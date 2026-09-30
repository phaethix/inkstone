# Phase 0g — Minimal Reclaimer (`prune`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an `inkstone prune` command that deletes unreferenced generated image artifacts older than a threshold, dry-run by default.

**Architecture:** A new `core/comic/prune.py` computes a live root set from `state.json` plus `consistency.json`, enumerates candidate files in the asset directories, and returns those that are both unreferenced and older than the threshold. The CLI wires a `prune` subcommand to it, mirroring the existing `identity`/`rebuild` pattern. The CAS and `index/` do not exist yet, so this implements the already-settled retention policy (§7, §13 resolved items 3 and 22) over today's real orphaned bytes; `collect_live_refs` is the seam Phase 4's `gc` extends.

**Tech Stack:** Python 3.10+, `pathlib`, `dataclasses`, `argparse`, `pytest`.

## Global Constraints

- Retention rule is fixed: delete only when the reference count is zero **and** the object is older than `--older-than` (§13 resolved item 3). Both conditions required.
- Dry-run is the default; deletion requires an explicit `--apply` (§9 row 0g).
- The ledger (`consistency.json`) is a mandatory root source (§8 invariant 6d).
- Zero quota: `prune` never calls an external API.
- All commands are read from the project `output_dir` (default `comic_out`), like every other subcommand.
- No new third-party dependency.
- Committed by the time a task completes; `ruff check` and `ruff format --check` clean; full `pytest` green.

## File Structure

- Create: `core/comic/prune.py` — the reclaimer: root set, candidate enumeration, plan, apply, threshold parsing.
- Create: `tests/test_phase0g_prune.py` — unit tests for the module.
- Modify: `core/cli.py` — add the `prune` subparser and dispatch handler.

### Task 1: Threshold parsing

**Files:**
- Create: `core/comic/prune.py`
- Test: `tests/test_phase0g_prune.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `parse_older_than(text: str) -> timedelta` — accepts `<N>d` (days), `<N>h` (hours), or a bare integer (days); raises `ValueError` on garbage or non-positive values.

- [ ] **Step 1: Write the failing test**

```python
"""Phase 0g — minimal reclaimer (prune)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from core.comic.prune import parse_older_than


def test_parse_older_than_days_hours_and_bare():
    assert parse_older_than("7d") == timedelta(days=7)
    assert parse_older_than("12h") == timedelta(hours=12)
    assert parse_older_than("3") == timedelta(days=3)


@pytest.mark.parametrize("bad", ["", "d", "7x", "-1d", "0d", "0", "1.5d", "d7"])
def test_parse_older_than_rejects_garbage(bad):
    with pytest.raises(ValueError):
        parse_older_than(bad)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_phase0g_prune.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.comic.prune'`

- [ ] **Step 3: Write minimal implementation**

```python
"""core.comic.prune — minimal reclaimer over generated image artifacts (§7, phase 0g).

The CAS and ``index/`` do not exist yet (Phases 2-3); today every artifact is
written at a deterministic path and overwritten in place, so the set of files a
resumable run still needs is exactly the set of paths the live state references.
This module deletes the complement, but only when it is also old enough:

    delete only when the reference count is zero AND the object is older than a
    threshold (content-addressed design §7; resolved item 3 in §13).

Both conditions are required. ``collect_live_refs`` is the seam Phase 4's ``gc``
extends with manifest ``outputs``, gate artifacts, and ``supersedes`` chains.
"""

from __future__ import annotations

import re
from datetime import timedelta

_OLDER_THAN_RE = re.compile(r"^(\d+)([dh]?)$")


def parse_older_than(text: str) -> timedelta:
    """Parse ``<N>d`` / ``<N>h`` / bare ``<N>`` (days) into a positive timedelta."""
    match = _OLDER_THAN_RE.match((text or "").strip())
    if match is None:
        raise ValueError(f"invalid --older-than value: {text!r}")
    amount = int(match.group(1))
    unit = match.group(2)
    if amount <= 0:
        raise ValueError(f"--older-than must be positive: {text!r}")
    return timedelta(hours=amount) if unit == "h" else timedelta(days=amount)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_phase0g_prune.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add core/comic/prune.py tests/test_phase0g_prune.py
git commit -m "feat(0g): parse --older-than threshold"
```

### Task 2: Live root set

**Files:**
- Modify: `core/comic/prune.py`
- Test: `tests/test_phase0g_prune.py`

**Interfaces:**
- Consumes: `parse_older_than` (Task 1).
- Produces: `collect_live_refs(state: ProjectState, ledger: ConsistencyLedger) -> set[Path]` — every artifact path the authoritative state points at, resolved to absolute `Path`s.

- [ ] **Step 1: Write the failing test**

```python
from pathlib import Path

from core.comic.ledger import ConsistencyLedger, LedgerEntry
from core.comic.prune import collect_live_refs
from core.schemas import (
    CharacterAsset,
    GeneratedAssets,
    GeneratedPage,
    GeneratedPanel,
    ProjectState,
)


def _state_with_assets(tmp_path: Path) -> ProjectState:
    state = ProjectState(project_id="p")
    state.generated = GeneratedAssets()
    state.generated.panels["c0000-p0000"] = GeneratedPanel(
        local=str(tmp_path / "panels" / "id-a.png"),
        chunk_index=0,
        panel_index=0,
    )
    state.generated.portraits["甲"] = str(tmp_path / "assets" / "portraits" / "id-b.png")
    state.generated.pages["c0000-p0000"] = GeneratedPage(
        local=str(tmp_path / "pages" / "page_c0000_p0000.png"),
        blank_local=str(tmp_path / "pages" / "blank" / "page_c0000_p0000.png"),
        mode="finished_lettered",
    )
    state.characters["甲"] = CharacterAsset(
        name="甲", portrait_local=str(tmp_path / "assets" / "portraits" / "id-b.png")
    )
    return state


def test_collect_live_refs_covers_every_root_source(tmp_path):
    state = _state_with_assets(tmp_path)
ledger = ConsistencyLedger()
    ref = tmp_path / "assets" / "portraits" / "id-ref.png"
    ledger.characters["甲"] = LedgerEntry()
    ledger.characters["甲"].reference.path = str(ref)

    refs = collect_live_refs(state, ledger)

    assert (tmp_path / "panels" / "id-a.png").resolve() in refs
    assert (tmp_path / "assets" / "portraits" / "id-b.png").resolve() in refs
    assert (tmp_path / "pages" / "page_c0000_p0000.png").resolve() in refs
    assert (tmp_path / "pages" / "blank" / "page_c0000_p0000.png").resolve() in refs
    assert ref.resolve() in refs


def test_collect_live_refs_ignores_empty_paths(tmp_path):
    state = ProjectState(project_id="p")
    state.characters["乙"] = CharacterAsset(name="乙", portrait_local=None)
    refs = collect_live_refs(state, ConsistencyLedger())
    assert refs == set()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_phase0g_prune.py -k collect_live_refs -v`
Expected: FAIL — `ImportError: cannot import name 'collect_live_refs'`

- [ ] **Step 3: Write minimal implementation**

Add to `core/comic/prune.py`:

```python
from pathlib import Path

from core.comic.ledger import ConsistencyLedger
from core.schemas import ProjectState


def collect_live_refs(state: ProjectState, ledger: ConsistencyLedger) -> set[Path]:
    """Every artifact path the authoritative state still points at.

    This is the reference-count side of the retention rule. The ledger is
    included because it is authoritative product intent and one of ``gc``'s
    explicit root sets (design §8 invariant 6d); omitting it would let a single
    prune delete the only copy of a character's reference image.
    """
    refs: set[Path] = set()
    for panel in state.generated.panels.values():
        if panel.local:
            refs.add(Path(panel.local).resolve())
    for portrait in state.generated.portraits.values():
        if portrait:
            refs.add(Path(portrait).resolve())
    for page in state.generated.pages.values():
        if page.local:
            refs.add(Path(page.local).resolve())
        if page.blank_local:
            refs.add(Path(page.blank_local).resolve())
    for asset in state.characters.values():
        if asset.portrait_local:
            refs.add(Path(asset.portrait_local).resolve())
    for entry in ledger.characters.values():
        if entry.reference.path:
            refs.add(Path(entry.reference.path).resolve())
    return refs
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_phase0g_prune.py -k collect_live_refs -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add core/comic/prune.py tests/test_phase0g_prune.py
git commit -m "feat(0g): collect live asset references as the prune root set"
```

### Task 3: Plan the reclaimable set

**Files:**
- Modify: `core/comic/prune.py`
- Test: `tests/test_phase0g_prune.py`

**Interfaces:**
- Consumes: `collect_live_refs` (Task 2), `parse_older_than` (Task 1).
- Produces:
  - `PruneCandidate` dataclass: `path: Path`, `size_bytes: int`, `mtime: float`.
  - `PrunePlan` dataclass: `candidates: list[PruneCandidate]`, properties `total_bytes: int`, `__bool__`, `__len__`.
  - `plan_prune(output_dir: Path, older_than: timedelta, *, now: float | None = None) -> PrunePlan` — raises `FileNotFoundError` when `state.json` is absent.

- [ ] **Step 1: Write the failing test**

```python
import os
import time

from core.comic.prune import plan_prune


def _age(path: Path, seconds: float) -> None:
    old = time.time() - seconds
    os.utime(path, (old, old))


def _write(path: Path, payload: bytes = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def test_plan_prune_selects_old_unreferenced_only(tmp_path, monkeypatch):
    (tmp_path / "panels").mkdir()
    (tmp_path / "assets" / "portraits").mkdir(parents=True)
    (tmp_path / "pages" / "blank").mkdir(parents=True)

referenced = _write(tmp_path / "panels" / "id-live.png", b"live")
    orphan_old = _write(tmp_path / "panels" / "id-orphan.png", b"orphan")
    orphan_new = _write(tmp_path / "panels" / "id-fresh.png", b"fresh")
    for path in (referenced, orphan_old):
        _age(path, 10 * 86400)

    state = _state_with_assets(tmp_path)
    state.generated.panels["c0000-p0000"].local = str(referenced)
    from core.comic.ledger import ConsistencyLedger

ledger = ConsistencyLedger()
    (tmp_path / "state.json").write_text(state.model_dump_json())
    (tmp_path / "consistency.json").write_text(ledger.model_dump_json())

    plan = plan_prune(tmp_path, parse_older_than("7d"))
    planned = {candidate.path for candidate in plan.candidates}
assert planned == {orphan_old.resolve()}
    assert plan.total_bytes == 6


def test_plan_prune_missing_state_raises(tmp_path):
    import pytest

    with pytest.raises(FileNotFoundError):
        plan_prune(tmp_path, parse_older_than("7d"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_phase0g_prune.py -k plan_prune -v`
Expected: FAIL — `ImportError: cannot import name 'plan_prune'`

- [ ] **Step 3: Write minimal implementation**

Add to `core/comic/prune.py`:

```python
import time
from dataclasses import dataclass, field

# Only these globs are ever candidates; everything else (comic.pdf, webtoon.png,
# state.json, consistency.json, logs) is structurally out of scope.
_ASSET_GLOBS = (
    "panels/*.png",
    "assets/portraits/*.png",
    "pages/*.png",
    "pages/blank/*.png",
)


@dataclass
class PruneCandidate:
    """One reclaimable file."""

    path: Path
    size_bytes: int
    mtime: float


@dataclass
class PrunePlan:
    """The outcome of planning: what would be deleted, and how much it weighs."""

    candidates: list[PruneCandidate] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.candidates)

    def __len__(self) -> int:
        return len(self.candidates)

    @property
    def total_bytes(self) -> int:
        return sum(candidate.size_bytes for candidate in self.candidates)


def _is_within(path: Path, root: Path) -> bool:
    """True when ``path`` resolves inside ``root`` (mirrors the pipeline helper)."""
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def plan_prune(output_dir: Path, older_than: timedelta, *, now: float | None = None) -> PrunePlan:
    """Compute the reclaimable set: unreferenced AND older than ``older_than``.

    An absent ``state.json`` is a hard error, never "everything is unreferenced":
    a missing state must not authorise a mass delete.
    """
    output_dir = Path(output_dir)
    state_path = output_dir / "state.json"
    if not state_path.is_file():
        raise FileNotFoundError(state_path)
    state = ProjectState.load(state_path)
    state.migrate_legacy_page_keys()
    ledger = ConsistencyLedger.load_or_rebuild(output_dir / "consistency.json", state)
    live = collect_live_refs(state, ledger)

    cutoff = (now if now is not None else time.time()) - older_than.total_seconds()
    plan = PrunePlan()
    for pattern in _ASSET_GLOBS:
        for path in sorted(output_dir.glob(pattern)):
            try:
                stat = path.stat()
            except FileNotFoundError:
                continue
            if not _is_within(path, output_dir):
                continue
            if path.resolve() in live:
                continue
            if stat.st_mtime >= cutoff:
                continue
            plan.candidates.append(
                PruneCandidate(path=path, size_bytes=stat.st_size, mtime=stat.st_mtime)
            )
    return plan
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_phase0g_prune.py -k plan_prune -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add core/comic/prune.py tests/test_phase0g_prune.py
git commit -m "feat(0g): plan reclaimable assets by refcount and age"
```

### Task 4: Apply the plan

**Files:**
- Modify: `core/comic/prune.py`
- Test: `tests/test_phase0g_prune.py`

**Interfaces:**
- Consumes: `PrunePlan` (Task 3).
- Produces:
  - `PruneResult` dataclass: `deleted: int`, `reclaimed_bytes: int`.
  - `apply_prune(plan: PrunePlan) -> PruneResult` — deletes each candidate, tolerating a file that vanished since planning.

- [ ] **Step 1: Write the failing test**

```python
from core.comic.prune import apply_prune


def test_apply_prune_deletes_and_reports(tmp_path):
    orphan = _write(tmp_path / "panels" / "id-orphan.png", b"12345")
    _age(orphan, 10 * 86400)
    plan = PrunePlan(candidates=[PruneCandidate(path=orphan, size_bytes=5, mtime=0.0)])

    result = apply_prune(plan)

    assert result.deleted == 1
    assert result.reclaimed_bytes == 5
    assert not orphan.exists()


def test_apply_prune_tolerates_vanished_file(tmp_path):
    missing = tmp_path / "panels" / "id-gone.png"
    plan = PrunePlan(candidates=[PruneCandidate(path=missing, size_bytes=5, mtime=0.0)])
    result = apply_prune(plan)
    assert result.deleted == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_phase0g_prune.py -k apply_prune -v`
Expected: FAIL — `ImportError: cannot import name 'apply_prune'`

- [ ] **Step 3: Write minimal implementation**

Add to `core/comic/prune.py`:

```python
@dataclass
class PruneResult:
    """What an apply actually reclaimed."""

    deleted: int = 0
    reclaimed_bytes: int = 0


def apply_prune(plan: PrunePlan) -> PruneResult:
    """Delete every planned candidate; idempotent when a file vanished first."""
    result = PruneResult()
    for candidate in plan.candidates:
        try:
            candidate.path.unlink()
        except FileNotFoundError:
            pass
        result.deleted += 1
        result.reclaimed_bytes += candidate.size_bytes
    return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_phase0g_prune.py -k apply_prune -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add core/comic/prune.py tests/test_phase0g_prune.py
git commit -m "feat(0g): apply prune plan and report reclaimed bytes"
```

### Task 5: Safety guards

**Files:**
- Modify: `core/comic/prune.py`
- Test: `tests/test_phase0g_prune.py`

**Interfaces:**
- Consumes: `plan_prune` (Task 3).
- Produces: no new public symbol; tightens `plan_prune` behaviour that later tasks and tests rely on.

- [ ] **Step 1: Write the failing tests**

```python
def test_plan_prune_ignores_non_asset_files(tmp_path):
    _write(tmp_path / "panels" / "id-orphan.png", b"o")
    comic_pdf = _write(tmp_path / "comic.pdf", b"%PDF")
    webtoon = _write(tmp_path / "webtoon.png", b"w")
    for path in (comic_pdf, webtoon):
        _age(path, 100 * 86400)
    state = ProjectState(project_id="p")
    (tmp_path / "state.json").write_text(state.model_dump_json())

    plan = plan_prune(tmp_path, parse_older_than("7d"))

    planned = {candidate.path for candidate in plan.candidates}
    assert comic_pdf.resolve() not in planned
    assert webtoon.resolve() not in planned


def test_plan_prune_never_plans_a_referenced_old_file(tmp_path):
    live = _write(tmp_path / "assets" / "portraits" / "id-live.png", b"keep")
    _age(live, 100 * 86400)
    state = ProjectState(project_id="p")
    state.characters["甲"] = CharacterAsset(name="甲", portrait_local=str(live))
    (tmp_path / "state.json").write_text(state.model_dump_json())

    plan = plan_prune(tmp_path, parse_older_than("7d"))

    assert live.resolve() not in {candidate.path for candidate in plan.candidates}


def test_plan_prune_corrupt_ledger_still_protects_state_roots(tmp_path):
    live = _write(tmp_path / "panels" / "id-live.png", b"keep")
    _age(live, 100 * 86400)
    state = ProjectState(project_id="p")
    state.generated.panels["c0000-p0000"] = GeneratedPanel(
        local=str(live), chunk_index=0, panel_index=0
    )
    (tmp_path / "state.json").write_text(state.model_dump_json())
    (tmp_path / "consistency.json").write_text("{ not json")

    plan = plan_prune(tmp_path, parse_older_than("7d"))

    assert live.resolve() not in {candidate.path for candidate in plan.candidates}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_phase0g_prune.py -k "ignores_non_asset or never_plans or corrupt_ledger" -v`
Expected: the first two PASS already (guaranteed by the glob list and root set); the corrupt-ledger test PASSES because `load_or_rebuild` swallows it. If all three pass, keep them as regression guards and proceed. If `corrupt_ledger` fails, the fix is to confirm `plan_prune` calls `ConsistencyLedger.load_or_rebuild` (not `.load`).

- [ ] **Step 3: Apply any fix needed**

Only if Step 2 failed: ensure `plan_prune` uses `ConsistencyLedger.load_or_rebuild(output_dir / "consistency.json", state)`. No other change.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_phase0g_prune.py -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add tests/test_phase0g_prune.py core/comic/prune.py
git commit -m "test(0g): guard prune scope, root protection, and corrupt-ledger fallback"
```

### Task 6: CLI `prune` subcommand

**Files:**
- Modify: `core/cli.py` (imports; `_build_parser`; a new `_run_prune`; `main` dispatch and the backward-compat guard tuple)
- Test: `tests/test_phase0g_prune.py` (add a CLI test) and `tests/test_cli_generate.py` (verify backward-compat guard unaffected).

**Interfaces:**
- Consumes: `parse_older_than`, `plan_prune`, `apply_prune` (Tasks 1–4).
- Produces: `_run_prune(args: argparse.Namespace) -> int` and the `prune` subcommand.

- [ ] **Step 1: Write the failing test**

```python
import subprocess
import sys


def test_cli_prune_dry_run_leaves_files(tmp_path):
    orphan = _write(tmp_path / "panels" / "id-orphan.png", b"o")
    _age(orphan, 100 * 86400)
    (tmp_path / "state.json").write_text(ProjectState(project_id="p").model_dump_json())

    completed = subprocess.run(
        [sys.executable, "-m", "core.cli", "prune", "--out", str(tmp_path), "--older-than", "7d"],
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert orphan.exists()  # dry-run deletes nothing
    assert "id-orphan.png" in completed.stdout


def test_cli_prune_apply_deletes(tmp_path):
    orphan = _write(tmp_path / "panels" / "id-orphan.png", b"o")
    _age(orphan, 100 * 86400)
    (tmp_path / "state.json").write_text(ProjectState(project_id="p").model_dump_json())

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "core.cli",
            "prune",
            "--out",
            str(tmp_path),
            "--older-than",
            "7d",
            "--apply",
        ],
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert not orphan.exists()


def test_cli_prune_rejects_bad_threshold(tmp_path):
    (tmp_path / "state.json").write_text(ProjectState(project_id="p").model_dump_json())
    completed = subprocess.run(
        [sys.executable, "-m", "core.cli", "prune", "--out", str(tmp_path), "--older-than", "soon"],
        capture_output=True,
        text=True,
    )
    assert completed.returncode != 0


def test_cli_prune_missing_state_fails(tmp_path):
    completed = subprocess.run(
        [sys.executable, "-m", "core.cli", "prune", "--out", str(tmp_path), "--older-than", "7d"],
        capture_output=True,
        text=True,
    )
    assert completed.returncode != 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_phase0g_prune.py -k cli_prune -v`
Expected: FAIL — `prune` is not a known subcommand (exit 2 / "invalid choice")

- [ ] **Step 3: Write minimal implementation**

In `core/cli.py`, add the import near the other `core.comic` imports:

```python
from core.comic.prune import apply_prune, parse_older_than, plan_prune
```

In `_build_parser`, after the `p_id` block:

```python
    p_prune = sub.add_parser(
        "prune",
        help="Delete unreferenced generated assets older than a threshold (dry-run by default)",
    )
    p_prune.add_argument(
        "--out",
        default="comic_out",
        help="Generation output directory (contains state.json; default comic_out)",
    )
    p_prune.add_argument(
        "--older-than",
        required=True,
        help="Minimum age of a reclaimable object: e.g. 7d (days), 12h (hours), or a bare integer",
    )
    p_prune.add_argument(
        "--apply",
        action="store_true",
        help="Actually delete; omit for a dry-run report",
    )
```

Add the handler after `_run_rebuild`:

```python
def _run_prune(args: argparse.Namespace) -> int:
    """prune 子命令：删除未被引用且超过存活时长的生成资产（§7 阶段 0g）。

    默认 dry-run，只有显式 --apply 才删除；删除条件是引用计数为零 **且**
    存活时长超过 --older-than（§13 已定第 3 条），两者缺一不可。
    """
    try:
        older_than = parse_older_than(args.older_than)
    except ValueError as exc:
        print(f"prune：{exc}")
        return 2

    out = Path(args.out)
    try:
        plan = plan_prune(out, older_than)
    except FileNotFoundError:
        print(f"state.json 未找到：{args.out}（请先运行 generate 或指定 --out）")
        return 1

    if not plan:
        print("没有可回收的资产：未被引用且超过存活时长的文件为零。")
        return 0

    for candidate in plan.candidates:
        print(f"  - {candidate.path}（{candidate.size_bytes} 字节）")
    if not args.apply:
        print(f"dry-run：将回收 {len(plan)} 个文件，共 {plan.total_bytes} 字节。加 --apply 才会删除。")
        return 0

    result = apply_prune(plan)
    print(f"已回收 {result.deleted} 个文件，共 {result.reclaimed_bytes} 字节。")
    return 0
```

Wire the dispatch in `main`: add `"prune"` to the backward-compat guard tuple, and add an `elif` branch:

```python
    elif args.command == "prune":
        sys.exit(_run_prune(args))
```

The guard tuple becomes:

```python
    if first not in (
        "generate",
        "plan",
        "identity",
        "coverage",
        "rebuild",
        "prune",
        "-h",
        "--help",
    ):
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_phase0g_prune.py -k cli_prune tests/test_cli_generate.py -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add core/cli.py tests/test_phase0g_prune.py
git commit -m "feat(0g): add 'prune' CLI subcommand (dry-run by default)"
```

### Task 7: Idempotence and full-suite verification

**Files:**
- Modify: `tests/test_phase0g_prune.py`
- Modify: `docs/ROADMAP.md`

**Interfaces:**
- Consumes: everything above.
- Produces: the 0g roadmap row marked landed.

- [ ] **Step 1: Write the failing test**

```python
def test_apply_prune_is_idempotent(tmp_path):
    orphan = _write(tmp_path / "panels" / "id-orphan.png", b"12345")
    _age(orphan, 100 * 86400)
    (tmp_path / "state.json").write_text(ProjectState(project_id="p").model_dump_json())

    first = apply_prune(plan_prune(tmp_path, parse_older_than("7d")))
    second = apply_prune(plan_prune(tmp_path, parse_older_than("7d")))

    assert first.deleted == 1
    assert second.deleted == 0
    assert second.reclaimed_bytes == 0
```

- [ ] **Step 2: Run test to verify it fails, then passes**

Run: `.venv/bin/python -m pytest tests/test_phase0g_prune.py -k idempotent -v`
Expected: PASS immediately (the second `plan_prune` finds no files). If it fails, the candidate enumeration is not re-stating on disk; fix by confirming `plan_prune` re-globs each call.

- [ ] **Step 3: Run the full suite and linters**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass (previously 520 passed, 5 skipped, plus the new 0g tests)

Run: `.venv/bin/ruff check . && .venv/bin/ruff format --check .`
Expected: clean

- [ ] **Step 4: Update the roadmap**

In `docs/ROADMAP.md`, under "P0 — Content-addressed pipeline migration (0a-0h)", add after the 0d row:

```markdown
- [x] **0g** Minimal reclaimer: `inkstone prune --older-than <N>d [--apply]` deletes
  generated image assets whose reference count is zero **and** whose age exceeds
  the threshold, dry-run by default (§7, resolved items 3 and 22). CAS-independent
  and zero-quota: today artifacts are written at deterministic, overwritten paths,
  so the root set is `state.json` + `consistency.json`; `collect_live_refs` is the
  seam Phase 4's `gc` extends. It ships before Phase 3, the entry point that writes
  image bytes.
```

- [ ] **Step 5: Commit**

```bash
git add tests/test_phase0g_prune.py docs/ROADMAP.md
git commit -m "docs(0g): record minimal reclaimer landing and add idempotence test"
```

## Self-Review

**1. Spec coverage:**
- Deletion condition (refcount zero AND age beyond threshold) → Task 3, guarded by Task 5.
- Root set incl. the ledger → Task 2.
- Dry-run default / `--apply` → Task 6.
- `--older-than` parsing → Task 1.
- Missing `state.json` hard error → Task 3 + Task 6 test.
- Corrupt ledger fallback → Task 5.
- Containment / non-asset exclusion → Task 5.
- Idempotence → Task 7.
- Seam for Phase 4 → documented in the module docstring (Task 1) and the roadmap row (Task 7).

**2. Placeholder scan:** no TBD/TODO; every code step carries literal code.

**3. Type consistency:** `parse_older_than(str) -> timedelta`, `collect_live_refs(ProjectState, ConsistencyLedger) -> set[Path]`, `plan_prune(Path, timedelta, *, now) -> PrunePlan`, `PrunePlan.candidates/total_bytes/__len__/__bool__`, `PruneCandidate(path,size_bytes,mtime)`, `apply_prune(PrunePlan) -> PruneResult`, `PruneResult(deleted,reclaimed_bytes)` — used consistently across tasks. `_is_within` is defined once (Task 3) and reused.