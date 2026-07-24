# Unattended Generation Implementation Plan

> **For agentic workers:** Execute task-by-task. Checkboxes track progress.

**Goal:** Supervisor loop so one upload runs until complete or 24h pause, without user resume clicks for transient API failures.

**Architecture:** `run_until_complete` wraps `creative_comic`; Web/CLI call the wrapper. Transient errors backoff and retry; wall-clock yields `PausedRun`.

**Tech Stack:** Existing asyncio pipeline, pytest, stdlib web server.

## Global Constraints

- Default deadline 24h via `INKSTONE_RUN_DEADLINE_HOURS`.
- Do not silently retry content-policy skips.
- No new pip dependencies.
- TDD; commit only if user asks.

---

### Task 1: `is_transient_error` + `run_until_complete`

**Files:**
- Create: `core/pipelines/run_until_complete.py`
- Create: `tests/test_run_until_complete.py`
- Modify: `core/pipelines/__init__.py` if exports exist

- [ ] Failing tests for success-after-retries and deadline pause
- [ ] Implement classifier + supervisor
- [ ] Green tests

### Task 2: Wire Web + CLI

**Files:**
- Modify: `web/server.py`
- Modify: `examples/generate_comic.py`
- Modify: `tests/test_web_server.py`
- Modify: `web/index.html` (paused UI)

- [ ] Job status `paused` + reason
- [ ] CLI prints pause message
- [ ] Tests / smoke

### Task 3: Docs + full pytest

- [ ] README one-liner on unattended runs
- [ ] `pytest` green
