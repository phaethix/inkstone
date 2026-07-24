# Progress Timing & ETA Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show cumulative active generation elapsed time and estimated remaining duration on the Web UI, continuing across checkpoint resumes via `state.json`.

**Architecture:** Persist `active_elapsed_seconds` on `ProjectState`. Pure helpers compute remaining ETA from `(elapsed, progress)`. The Web job tracks `base_elapsed + session wall`, exposes both fields on `/api/job`, persists on progress ticks and terminal states. The UI formats and 1s-ticks locally, recalibrating from poll. Supervisor deadline stays session-local and does not use the cumulative counter.

**Tech Stack:** Python 3.10+, Pydantic `ProjectState`, stdlib `web/server.py`, vanilla `web/index.html`, pytest.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-24-progress-timing-design.md`
- Elapsed = cumulative **active** seconds only (idle between runs excluded)
- ETA display = remaining duration only (not clock end time)
- ETA formula: `remaining = elapsed * (1 - p) / p` when `p >= 0.05` and `p < 1`; `0` when `p >= 1`; else `null`
- Supervisor wall-clock deadline uses **this session** monotonic time only — never `active_elapsed_seconds`
- No new pip dependencies
- TDD; commit only when the user explicitly asks

## File map

| File | Responsibility |
|------|----------------|
| `core/schemas.py` | `ProjectState.active_elapsed_seconds` field |
| `core/pipelines/timing.py` | Pure `estimate_remaining`, `format` not required (UI formats) |
| `web/server.py` | Job seed/tick/persist; job JSON fields |
| `web/index.html` | Progress meta timing row + local tick |
| `tests/test_timing.py` | ETA + cumulative helpers |
| `tests/test_schemas.py` | Field default / round-trip |
| `tests/test_web_server.py` | Job payload timing seed/persist |

---

### Task 1: Schema field + ETA helper

**Files:**
- Modify: `core/schemas.py` (`ProjectState`)
- Create: `core/pipelines/timing.py`
- Create: `tests/test_timing.py`
- Modify: `tests/test_schemas.py` (or add cases there)

**Interfaces:**
- Produces: `ProjectState.active_elapsed_seconds: float` (default `0.0`)
- Produces: `estimate_remaining(elapsed_seconds: float, progress: float) -> float | None`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_timing.py
from core.pipelines.timing import estimate_remaining


def test_estimate_remaining_linear():
    assert estimate_remaining(100.0, 0.25) == 300.0


def test_estimate_remaining_too_early():
    assert estimate_remaining(100.0, 0.04) is None


def test_estimate_remaining_done():
    assert estimate_remaining(100.0, 1.0) == 0.0
```

```python
# tests/test_schemas.py (add)
def test_active_elapsed_seconds_default_and_roundtrip(tmp_path):
    from core.schemas import ProjectState

    state = ProjectState(project_id="t1")
    assert state.active_elapsed_seconds == 0.0
    path = tmp_path / "state.json"
    state.active_elapsed_seconds = 125.5
    state.save(path)
    loaded = ProjectState.load(path)
    assert loaded.active_elapsed_seconds == 125.5
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `pytest tests/test_timing.py tests/test_schemas.py::test_active_elapsed_seconds_default_and_roundtrip -v`  
Expected: FAIL (import / attribute missing)

- [ ] **Step 3: Implement minimal code**

In `core/schemas.py` on `ProjectState`, add:

```python
active_elapsed_seconds: float = 0.0
```

Create `core/pipelines/timing.py`:

```python
"""Progress timing helpers (elapsed / ETA)."""

from __future__ import annotations

MIN_PROGRESS_FOR_ETA = 0.05


def estimate_remaining(elapsed_seconds: float, progress: float) -> float | None:
    """Linear ETA remaining from cumulative elapsed and progress fraction."""
    if progress >= 1.0:
        return 0.0
    if progress < MIN_PROGRESS_FOR_ETA:
        return None
    if elapsed_seconds < 0:
        elapsed_seconds = 0.0
    return float(elapsed_seconds) * (1.0 - float(progress)) / float(progress)
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `pytest tests/test_timing.py tests/test_schemas.py::test_active_elapsed_seconds_default_and_roundtrip -v`  
Expected: PASS

- [ ] **Step 5: Commit** (only if user asked)

```bash
git add core/schemas.py core/pipelines/timing.py tests/test_timing.py tests/test_schemas.py
git commit -m "feat: add active_elapsed_seconds and ETA helper"
```

---

### Task 2: Web job cumulative elapsed + persist

**Files:**
- Modify: `web/server.py`
- Modify: `tests/test_web_server.py`

**Interfaces:**
- Consumes: `ProjectState.active_elapsed_seconds`, `estimate_remaining`
- Produces: job fields `base_elapsed`, `session_started_at` (internal); API `elapsed_seconds`, `remaining_seconds`
- Produces: `_seed_job_timing(project_id) -> float`, `_job_elapsed(job) -> float`, `_persist_active_elapsed(project_id, elapsed)`, `_refresh_job_timing(job)`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_web_server.py — add

def test_seed_job_timing_from_checkpoint(tmp_path, monkeypatch):
    import web.server as server
    from core.schemas import ProjectState

    monkeypatch.setattr(server, "OUTPUT_DIR", tmp_path)
    project_id = "timing01"
    (tmp_path / project_id).mkdir()
    state = ProjectState(project_id=project_id, active_elapsed_seconds=3600.0)
    state.save(tmp_path / project_id / "state.json")
    assert server._seed_job_timing(project_id) == 3600.0


def test_job_elapsed_adds_session(monkeypatch):
    import web.server as server
    import time

    fixed = {"t": 1000.0}
    monkeypatch.setattr(server.time, "monotonic", lambda: fixed["t"])
    job = {"base_elapsed": 100.0, "session_started_at": 1000.0, "status": "running"}
    assert server._job_elapsed(job) == 100.0
    fixed["t"] = 1005.0
    assert server._job_elapsed(job) == 105.0


def test_refresh_job_timing_sets_remaining(monkeypatch):
    import web.server as server

    monkeypatch.setattr(server.time, "monotonic", lambda: 1100.0)
    job = {
        "base_elapsed": 100.0,
        "session_started_at": 1000.0,
        "progress": 0.25,
        "status": "running",
    }
    server._refresh_job_timing(job)
    assert job["elapsed_seconds"] == 200.0
    assert job["remaining_seconds"] == 600.0


def test_persist_active_elapsed(tmp_path, monkeypatch):
    import web.server as server
    from core.schemas import ProjectState

    monkeypatch.setattr(server, "OUTPUT_DIR", tmp_path)
    project_id = "timing02"
    (tmp_path / project_id).mkdir()
    ProjectState(project_id=project_id).save(tmp_path / project_id / "state.json")
    server._persist_active_elapsed(project_id, 42.0)
    loaded = ProjectState.load(tmp_path / project_id / "state.json")
    assert loaded.active_elapsed_seconds == 42.0
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `pytest tests/test_web_server.py::test_seed_job_timing_from_checkpoint tests/test_web_server.py::test_job_elapsed_adds_session tests/test_web_server.py::test_refresh_job_timing_sets_remaining tests/test_web_server.py::test_persist_active_elapsed -v`  
Expected: FAIL (helpers missing)

- [ ] **Step 3: Implement helpers + wire job lifecycle**

Add near progress helpers in `web/server.py`:

```python
import time
from core.pipelines.timing import estimate_remaining

def _seed_job_timing(project_id: str) -> float:
    state_path = OUTPUT_DIR / project_id / "state.json"
    if not state_path.is_file():
        return 0.0
    try:
        return float(ProjectState.load(state_path).active_elapsed_seconds or 0.0)
    except Exception:  # noqa: BLE001
        return 0.0


def _job_elapsed(job: dict) -> float:
    base = float(job.get("base_elapsed") or 0.0)
    started = job.get("session_started_at")
    if started is None:
        return base
    return base + max(0.0, time.monotonic() - float(started))


def _refresh_job_timing(job: dict) -> None:
    elapsed = _job_elapsed(job)
    job["elapsed_seconds"] = elapsed
    progress = float(job.get("progress") or 0.0)
    job["remaining_seconds"] = estimate_remaining(elapsed, progress)


def _persist_active_elapsed(project_id: str, elapsed: float) -> None:
    state_path = OUTPUT_DIR / project_id / "state.json"
    if not state_path.is_file():
        return
    try:
        state = ProjectState.load(state_path)
        # Never decrease if an older write races a newer checkpoint.
        state.active_elapsed_seconds = max(float(state.active_elapsed_seconds or 0.0), float(elapsed))
        state.save(state_path)
    except Exception:  # noqa: BLE001
        logger.warning("could not persist active_elapsed_seconds for %s", project_id)
```

Wire `_start_job`:

```python
base_elapsed = _seed_job_timing(pid)
JOBS[job_id] = {
    # ...existing fields...
    "base_elapsed": base_elapsed,
    "session_started_at": time.monotonic(),
    "elapsed_seconds": base_elapsed,
    "remaining_seconds": estimate_remaining(base_elapsed, seeded_progress),
    "progress": seeded_progress,
    # ...
}
```

In `_on_progress`, after updating progress:

```python
_refresh_job_timing(job)
_persist_active_elapsed(project_id, float(job["elapsed_seconds"]))
```

In `_run_job` `finally` (and before setting terminal status paths): call `_refresh_job_timing(job)` then `_persist_active_elapsed(project_id, ...)`.

In `_fill_job_from_paused`: **do not** overwrite cumulative `elapsed_seconds` with session-only `paused.elapsed_seconds`. Instead keep job timing via `_refresh_job_timing` / persist. Optionally append session wall into logs only.

In `GET /api/job/...`: if `status == "running"`, call `_refresh_job_timing(job)` under lock before serialize; include `remaining_seconds` in JSON.

**Do not** change `run_until_complete` deadline math to use cumulative elapsed.

- [ ] **Step 4: Run tests — expect PASS**

Run: `pytest tests/test_web_server.py::test_seed_job_timing_from_checkpoint tests/test_web_server.py::test_job_elapsed_adds_session tests/test_web_server.py::test_refresh_job_timing_sets_remaining tests/test_web_server.py::test_persist_active_elapsed -v`  
Expected: PASS

Also: `pytest tests/test_run_until_complete.py -v` — still PASS (deadline unchanged)

- [ ] **Step 5: Commit** (only if user asked)

```bash
git add web/server.py tests/test_web_server.py
git commit -m "feat: persist and expose cumulative job timing"
```

---

### Task 3: UI elapsed + remaining display

**Files:**
- Modify: `web/index.html` (progress meta + JS)

**Interfaces:**
- Consumes: job `elapsed_seconds`, `remaining_seconds`, `status`

- [ ] **Step 1: Extend progress markup**

In `#progressWrap` `.progress-meta`, add a timing span:

```html
<div class="progress-meta">
  <span id="status">Preparing…</span>
  <span id="timing" class="muted">已用 — · 预计还剩 估算中</span>
  <span id="pct">0%</span>
</div>
```

Adjust CSS so meta is three columns or wraps cleanly (`justify-content: space-between` already — ensure `#timing` sits between status and pct or below on narrow widths).

- [ ] **Step 2: Add format + tick helpers in script**

```javascript
let timingTimer = null;
let timingElapsed = 0;
let timingRemaining = null; // number | null
let timingRunning = false;

function formatDuration(sec) {
  sec = Math.max(0, Math.floor(Number(sec) || 0));
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  if (h > 0) return h + "h " + m + "m";
  if (m > 0) return m + "m " + s + "s";
  return s + "s";
}

function renderTiming() {
  const el = $("timing");
  if (!el) return;
  const left = (timingRemaining == null)
    ? "估算中"
    : formatDuration(timingRemaining);
  el.textContent = "已用 " + formatDuration(timingElapsed) + " · 预计还剩 " + left;
}

function startTimingTick() {
  stopTimingTick();
  timingRunning = true;
  timingTimer = setInterval(() => {
    if (!timingRunning) return;
    timingElapsed += 1;
    if (timingRemaining != null && timingRemaining > 0) {
      timingRemaining = Math.max(0, timingRemaining - 1);
    }
    renderTiming();
  }, 1000);
}

function stopTimingTick() {
  timingRunning = false;
  if (timingTimer) clearInterval(timingTimer);
  timingTimer = null;
}

function syncTimingFromJob(job) {
  if (job.elapsed_seconds != null) timingElapsed = Number(job.elapsed_seconds);
  timingRemaining = (job.remaining_seconds == null) ? null : Number(job.remaining_seconds);
  renderTiming();
}
```

- [ ] **Step 3: Wire into `poll` / `generate` / terminal states**

- On `generate()` start: `timingElapsed = 0; timingRemaining = null; renderTiming();` (resume will recalibrate on first poll)
- On each running poll: `syncTimingFromJob(job);` if tick not running, `startTimingTick()`
- On done/paused/error: `syncTimingFromJob(job); stopTimingTick();` keep `#progressWrap` visible long enough on pause to show timing, or keep timing line in error/pause banner — prefer keep progress wrap visible on pause with final timing (today pause hides progress wrap — change pause path to leave progress wrap visible with final % + timing, or show timing in `#errorWrap` prefix). **Preferred:** on pause, do **not** hide `progressWrap`; show final status + timing; enable Generate again.

- [ ] **Step 4: Manual smoke**

Run: `python web/server.py` → open UI → start a short generate → confirm seconds tick; kill/resume same project id → elapsed continues (requires a project with `active_elapsed_seconds` already set, or complete one pause cycle).

- [ ] **Step 5: Commit** (only if user asked)

```bash
git add web/index.html
git commit -m "feat: show elapsed and remaining time on progress UI"
```

---

### Task 4: Full regression

- [ ] **Step 1: Run full test suite**

Run: `pytest -q`  
Expected: all PASS

- [ ] **Step 2: Spec acceptance checklist**

1. Fresh run ticks from ~0; after `progress >= 5%`, remaining appears  
2. Resume same `project_id` continues elapsed  
3. Idle between runs does not inflate elapsed  
4. `tests/test_run_until_complete.py` still enforces session deadline only  

---

## Spec coverage (self-review)

| Spec requirement | Task |
|------------------|------|
| `active_elapsed_seconds` on state | Task 1 |
| Cumulative active elapsed | Task 2 |
| Remaining duration ETA formula + 5% gate | Task 1–2 |
| API `elapsed_seconds` / `remaining_seconds` | Task 2 |
| UI display + 1s tick + poll calibrate | Task 3 |
| Resume continuity | Task 2–3 |
| Supervisor deadline session-local | Task 2 (explicit non-change) + Task 4 |
| Tests listed in spec | Task 1–2, 4 |

No placeholders remaining after inline review.
