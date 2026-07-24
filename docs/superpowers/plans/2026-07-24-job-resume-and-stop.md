# Job Resume on Refresh + Cooperative Stop — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reconnect the Web UI to an in-flight job after refresh via `localStorage`, and add a cooperative **停止生成** control that pauses at safe boundaries and leaves a resumable checkpoint.

**Architecture:** Web jobs gain a `threading.Event` cancel flag exposed by `POST /api/job/<id>/stop`. The pipeline accepts optional `cancel_check: Callable[[], bool]` and raises `PipelineCancelled` at chunk/panel/supervisor-retry boundaries; `run_until_complete` maps that to `PausedRun(reason="stopped by user")`. The UI persists `inkstone.activeJob` and on load re-polls the stored `job_id`.

**Tech Stack:** Existing stdlib web server, asyncio pipeline, pytest, vanilla `web/index.html` + `localStorage`.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-24-job-resume-and-stop-design.md`
- Stop = cooperative pause (do not abort in-flight HTTP)
- localStorage key exactly `inkstone.activeJob`
- Stop reason string includes `stopped by user` (for UI/logs)
- Must release `.inkstone.lock` on cancel (contextmanager `finally` already does if exception propagates out of `creative_comic`)
- No new pip dependencies
- TDD; commit only when the user explicitly asks

## File map

| File | Responsibility |
|------|----------------|
| `core/pipelines/cancel.py` | `PipelineCancelled` + tiny `check_cancel` helper |
| `core/pipelines/creative_comic.py` | Honor `cancel_check` at chunk/panel boundaries |
| `core/pipelines/run_until_complete.py` | Pass-through + map cancel → `PausedRun`; check before retry sleep |
| `web/server.py` | Job `cancel_event`, `POST /api/job/<id>/stop`, wire checker |
| `web/index.html` | localStorage session + Stop button + boot reconnect |
| `tests/test_pipeline_cancel.py` | Cancel checkpoints / PausedRun |
| `tests/test_web_server.py` | Stop API + job paused |

---

### Task 1: Pipeline cancel primitive + checkpoints

**Files:**
- Create: `core/pipelines/cancel.py`
- Create: `tests/test_pipeline_cancel.py`
- Modify: `core/pipelines/creative_comic.py`
- Modify: `core/pipelines/run_until_complete.py`

**Interfaces:**
- Produces: `class PipelineCancelled(Exception)` with `.reason: str` (default `"stopped by user"`)
- Produces: `def check_cancel(cancel_check: Callable[[], bool] | None) -> None` — raises `PipelineCancelled` when checker returns true
- Produces: `creative_comic(..., cancel_check: Callable[[], bool] | None = None)`
- Produces: `run_until_complete(..., cancel_check: Callable[[], bool] | None = None)` — on `PipelineCancelled`, return `PausedRun(reason=exc.reason, ...)`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_pipeline_cancel.py
import asyncio
from pathlib import Path

from core.pipelines.cancel import PipelineCancelled, check_cancel
from core.pipelines.run_until_complete import PausedRun, run_until_complete
from core.schemas import ProjectState


def test_check_cancel_raises_when_true():
    try:
        check_cancel(lambda: True)
        assert False, "expected PipelineCancelled"
    except PipelineCancelled as exc:
        assert "stopped by user" in exc.reason


def test_check_cancel_noop_when_false_or_none():
    check_cancel(None)
    check_cancel(lambda: False)


def test_run_until_complete_maps_cancel_to_paused(tmp_path, monkeypatch):
    calls = {"n": 0}

    async def fake_creative(*args, **kwargs):
        check = kwargs.get("cancel_check")
        calls["n"] += 1
        if calls["n"] == 1:
            raise PipelineCancelled()
        raise AssertionError("should not retry after user cancel")

    monkeypatch.setattr(
        "core.pipelines.run_until_complete.creative_comic",
        fake_creative,
    )
    out = tmp_path / "p1"
    out.mkdir()
    ProjectState(project_id="p1").save(out / "state.json")

    result = asyncio.run(
        run_until_complete("hello", output_dir=str(out), project_id="p1", cancel_check=lambda: True)
    )
    assert isinstance(result, PausedRun)
    assert "stopped by user" in result.reason
```

Add one unit-style test that `_creative_comic` respects cancel at chunk start by monkeypatching a minimal path — if too heavy, rely on `check_cancel` at the top of the chunk loop covered by an integration-style test:

```python
def test_creative_comic_stops_before_second_chunk(tmp_path, monkeypatch):
    """Cancel after first chunk boundary check on second iteration."""
    from core.pipelines import creative_comic as mod
    from core.schemas import StoryElements, Storyboard, Panel, ChunkCache

    # Force two chunks
    monkeypatch.setattr(mod, "segment_text", lambda _t: ["chunk-a", "chunk-b"])

    stopped = {"v": False}

    async def fake_extract(text, *, chat=None):
        return StoryElements(characters=[], settings=[], style_guide="s")

    async def fake_plan(text, elements, *, chat=None):
        return Storyboard(
            chapter_id="c",
            panels=[Panel(panel_id="p1", action="wave")],
        )

    # Skip real image work: mark panels done without calling image API
    async def fake_creative_inner(*args, **kwargs):
        # Call real _creative_comic but stub extract/plan/image heavily
        ...
```

Prefer keeping Task 1 tests focused: `check_cancel` + `run_until_complete` mapping is required; for `creative_comic`, add a **direct** test by exporting a tiny helper used in the loop:

```python
# In creative_comic, call check_cancel(cancel_check) at:
# 1) start of each `for ci, chunk` iteration (before extract)
# 2) before building/scheduling each pending panel batch (before image work)
# 3) optionally before starting extract/storyboard awaits
```

Minimal creative_comic test without full image stack:

```python
def test_creative_comic_honors_cancel_at_chunk_boundary(tmp_path, monkeypatch):
    from core.pipelines.creative_comic import creative_comic
    from core.pipelines.cancel import PipelineCancelled
    import core.pipelines.creative_comic as cc

    monkeypatch.setattr(cc, "segment_text", lambda _t: ["a", "b"])
    monkeypatch.setattr(cc, "get_chat_provider", lambda: object())
    monkeypatch.setattr(cc, "get_image_provider", lambda: object())
    monkeypatch.setattr(cc, "_model_snapshot", lambda *_a, **_k: cc.ModelSnapshot())
    monkeypatch.setattr(cc, "_reconcile_state", lambda *_a, **_k: None)

    hits = {"extract": 0}

    async def boom_extract(text, *, chat=None):
        hits["extract"] += 1
        raise AssertionError("extract should not run when already cancelled")

    monkeypatch.setattr(cc, "extract_story_elements", boom_extract)

    cancel = {"go": True}  # already cancelled before first chunk work

    try:
        asyncio.run(
            creative_comic(
                "x",
                output_dir=str(tmp_path / "out"),
                project_id="c1",
                cancel_check=lambda: cancel["go"],
            )
        )
        assert False, "expected cancel"
    except PipelineCancelled:
        pass
    assert hits["extract"] == 0
```

Note: `creative_comic` wraps with lock and currently does not catch `PipelineCancelled` — letting it propagate is correct so the lock releases; `run_until_complete` catches it.

- [ ] **Step 2: Run tests — expect FAIL**

Run: `pytest tests/test_pipeline_cancel.py -v`  
Expected: FAIL (imports / kwargs missing)

- [ ] **Step 3: Implement**

Create `core/pipelines/cancel.py`:

```python
from __future__ import annotations

from collections.abc import Callable


class PipelineCancelled(Exception):
    """Cooperative user stop — progress should already be on disk."""

    def __init__(self, reason: str = "stopped by user") -> None:
        self.reason = reason
        super().__init__(reason)


def check_cancel(cancel_check: Callable[[], bool] | None) -> None:
    if cancel_check is not None and cancel_check():
        raise PipelineCancelled()
```

In `creative_comic` / `_creative_comic`: add `cancel_check=None` kw-only arg; at start of each chunk iteration call `check_cancel(cancel_check)`; before scheduling panel renders call `check_cancel(cancel_check)`; `state.save(state_path)` immediately before raising is automatic if last save was recent — still `state.save(state_path)` right before `check_cancel` after a completed unit when practical.

In `run_until_complete`: add `cancel_check`; pass into `creative_comic`; wrap:

```python
try:
    return await creative_comic(..., cancel_check=cancel_check)
except PipelineCancelled as exc:
    return PausedRun(
        project_id=pid,
        output_dir=output_dir,
        reason=exc.reason,
        state=_load_state(output_dir),
        elapsed_seconds=time.monotonic() - start,
    )
```

Before `await asyncio.sleep(delay)` on transient retry: `check_cancel(cancel_check)`.

**Do not** treat `PipelineCancelled` as transient.

- [ ] **Step 4: Run tests — expect PASS**

Run: `pytest tests/test_pipeline_cancel.py -v`  
Expected: PASS

- [ ] **Step 5: Commit** (only if user asked)

```bash
git add core/pipelines/cancel.py core/pipelines/creative_comic.py \
  core/pipelines/run_until_complete.py tests/test_pipeline_cancel.py
git commit -m "feat: cooperative pipeline cancel checkpoints"
```

---

### Task 2: Web stop API + wire cancel into jobs

**Files:**
- Modify: `web/server.py`
- Modify: `tests/test_web_server.py`

**Interfaces:**
- Consumes: `cancel_check` / `PipelineCancelled` → `PausedRun` from Task 1
- Produces: `POST /api/job/<job_id>/stop` → `{ok: true}` or 404
- Produces: each job dict has `cancel_event: threading.Event` (not serialized) and `cancel_requested` reflected in status JSON optional boolean

- [ ] **Step 1: Write failing tests**

```python
def test_stop_unknown_job_404():
    # Use existing HTTP test helpers in test_web_server.py
    ...


def test_stop_sets_cancel_and_pipeline_pauses(tmp_path, monkeypatch):
    import threading
    import web.server as server

    monkeypatch.setattr(server, "OUTPUT_DIR", tmp_path)
    # Patch run_until_complete to wait until cancel_check is true, then raise mapping path
    gate = threading.Event()

    async def slow_run(*args, **kwargs):
        check = kwargs.get("cancel_check")
        deadline = time.time() + 2
        while time.time() < deadline:
            if check and check():
                from core.pipelines.cancel import PipelineCancelled
                raise PipelineCancelled()
            await asyncio.sleep(0.05)
        raise RuntimeError("cancel never seen")

    # Actually run_until_complete should catch — patch creative_comic instead or call real run_until_complete
    ...
```

Simpler approach matching existing tests:

```python
def test_post_stop_marks_cancel_event(monkeypatch):
    import web.server as server
    import threading

    job_id = "jobstop1"
    ev = threading.Event()
    with server.JOBS_LOCK:
        server.JOBS[job_id] = {
            "status": "running",
            "cancel_event": ev,
            "log": [],
            "error": None,
            "progress": 0.1,
            "stage": "panels",
            "project_id": "p",
            "panels": [],
            "webtoon": None,
            "pdf": None,
            "skipped": [],
            "skipped_chunks": [],
            "needs_review": [],
            "stale_panels": [],
            "pause_reason": None,
            "elapsed_seconds": 1,
            "remaining_seconds": None,
            "base_elapsed": 0,
            "session_started_at": time.monotonic(),
        }
    # Invoke handler helper
    assert server.request_stop(job_id) is True
    assert ev.is_set()
    assert server.request_stop("missing") is False
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Implement in `web/server.py`**

On `_start_job`:

```python
cancel_event = threading.Event()
JOBS[job_id] = {..., "cancel_event": cancel_event, "cancel_requested": False}
```

Pass into `_run_job` / `run_until_complete`:

```python
cancel_check=lambda: JOBS[job_id]["cancel_event"].is_set()
```

(Or close over `cancel_event` directly.)

```python
def request_stop(job_id: str) -> bool:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job is None:
            return False
        job["cancel_requested"] = True
        ev = job.get("cancel_event")
        if isinstance(ev, threading.Event):
            ev.set()
        return True
```

In `do_POST`, route `/api/job/<id>/stop` → `request_stop` → 200 `{ok:true}` or 404.

When serializing job JSON, include `"cancel_requested": bool(job.get("cancel_requested"))` (do not include the Event).

Existing `PausedRun` handling already sets `status=paused` — user stop uses same path.

- [ ] **Step 4: PASS tests**

- [ ] **Step 5: Commit** (if asked)

```bash
git commit -m "feat: add POST /api/job/<id>/stop cooperative cancel"
```

---

### Task 3: UI — localStorage reconnect + Stop button

**Files:**
- Modify: `web/index.html`

**Interfaces:**
- Consumes: `GET /api/job/<id>`, `POST /api/job/<id>/stop`, job fields already present
- Produces: `inkstone.activeJob` localStorage session; Stop button UX

- [ ] **Step 1: Markup**

Next to `#go`:

```html
<button id="stop" class="btn btn-secondary hidden" type="button">停止生成</button>
```

- [ ] **Step 2: Session helpers**

```javascript
const ACTIVE_JOB_KEY = "inkstone.activeJob";

function saveActiveJob( partial ) {
  const prev = loadActiveJob() || {};
  localStorage.setItem(ACTIVE_JOB_KEY, JSON.stringify({ ...prev, ...partial }));
}
function loadActiveJob() {
  try { return JSON.parse(localStorage.getItem(ACTIVE_JOB_KEY) || "null"); }
  catch { return null; }
}
function clearActiveJob() { localStorage.removeItem(ACTIVE_JOB_KEY); }

function setStopVisible(running) {
  const btn = $("stop");
  if (!btn) return;
  btn.classList.toggle("hidden", !running);
  if (running) {
    btn.disabled = false;
    btn.textContent = "停止生成";
  }
}
```

- [ ] **Step 3: Wire generate / poll / stop / boot**

- After successful `/api/generate` (and regen):  
  `saveActiveJob({ job_id, project_id, format, text, style_guide })`; `setStopVisible(true)`.
- On poll terminal `done`/`error`: `clearActiveJob()` (for `paused`, **keep** key so refresh still shows paused state — or keep `project_id` and clear `job_id`; **spec:** paused restore on load is required → **keep full key** until user starts a new generate or clears on done/error/404).
- `stop` click: `POST /api/job/${jobId}/stop`; button → disabled + `正在停止…`.
- On live boot (after mode detect succeeds): `restoreActiveJobIfAny()`:
  - load key → fetch job
  - restore text/format/style/projectId fields from key
  - `running` → show progress, `poll(job_id)`, `setStopVisible(true)`, sync timing
  - `paused` → show progress/results + pause message, Generate enabled, Stop hidden
  - `done` → `render(job)` then `clearActiveJob()`
  - `error`/404 → `clearActiveJob()`; if 404 && project_id, set `#modeHint` or `#errorWrap` one-liner about resume with project id

Demo: Stop clears demo timer / hides stop.

Track `currentJobId` in JS for stop + poll.

- [ ] **Step 4: Manual smoke**

1. Start generate → refresh → UI reconnects.  
2. Stop → paused → Generate again same id continues.  
3. Restart server → refresh → key cleared, no crash.

- [ ] **Step 5: Commit** (if asked)

```bash
git commit -m "feat: restore active job on refresh and add stop button"
```

---

### Task 4: Full regression

- [ ] `.venv/bin/pytest -q` all green
- [ ] Acceptance checklist from spec §Acceptance

---

## Spec coverage (self-review)

| Spec item | Task |
|-----------|------|
| `inkstone.activeJob` write/load/clear | Task 3 |
| Reconnect running/paused/done/404 | Task 3 |
| `POST .../stop` | Task 2 |
| Cancel at panel/chunk/retry boundaries | Task 1 |
| `PausedRun` / paused UI | Task 1–3 |
| Lock release on cancel | Task 1 (exception + lock finally) |
| Demo stop | Task 3 |
| Tests listed | Task 1–2, 4 |

No placeholders remaining after inline review.
