# Unattended Novel Generation — Spec

**Date:** 2026-07-24  
**Status:** Approved (option B wall-clock; approach 3 supervisor loop)  
**Goal:** After one upload, generation continues through timeouts/503s without user babysitting, until the comic is complete or a wall-clock deadline pauses the run with progress preserved.

## Decisions

- Supervisor loop around existing `creative_comic` (checkpoint resume), shared by Web and CLI.
- Transient failures (timeout, connection, 429/5xx exhausted, free-tier busy) → backoff sleep → re-enter pipeline on same `output_dir` / `project_id`.
- Content-policy skips remain permanent (`skipped` / `skipped_chunks`).
- Wall clock default **24 hours** from supervisor start (`INKSTONE_RUN_DEADLINE_HOURS`).
- On deadline: status `paused` (not hard `error`); same project resume continues.
- Process kill still requires restarting the server/CLI with the same project (no OS daemon this round).

## API

### `run_until_complete(...)`

Location: `core/pipelines/run_until_complete.py`

```python
async def run_until_complete(
    source_txt: str,
    *,
    output_dir: str,
    project_id: str | None = None,
    ...,
    deadline_hours: float | None = None,  # default from env, 24
) -> ComicProject | PausedRun
```

- Loop: call `creative_comic` → on success return project → on transient error: log, sleep (`INKSTONE_SUPERVISOR_BACKOFF` exponential, cap e.g. 5 min), retry.
- Non-transient (e.g. ValueError lock, missing providers): raise / `error`.
- If `time.monotonic() - start >= deadline`: return `PausedRun(reason=..., state=...)`.

### Transient classifier

Treat as recoverable: `requests.Timeout`, `requests.ConnectionError`, `RuntimeError` with max-retries/status 429/5xx, message containing queue/busy/timeout when clearly operational.

Do **not** auto-retry: content policy (already skipped inside pipeline), fingerprint/programmer bugs, project lock held by another process.

### Web

- `_run_job` uses `run_until_complete`.
- Job statuses: `running` | `paused` | `done` | `error`.
- Payload: `pause_reason`, `elapsed_seconds` optional.
- UI: show paused message; Continue = same generate with same `project_id`.

### CLI

- `generate_comic.py` uses `run_until_complete`; on pause exit non-zero with clear message that progress is saved.

## Config

| Env | Default | Meaning |
|---|---|---|
| `INKSTONE_RUN_DEADLINE_HOURS` | `24` | Wall clock for one supervisor session |
| `INKSTONE_SUPERVISOR_BACKOFF_BASE` | `30` | Seconds base backoff between pipeline attempts |
| `INKSTONE_SUPERVISOR_BACKOFF_CAP` | `300` | Max sleep between attempts |

## Tests

- Fake pipeline that fails twice then succeeds → final project.
- Fake pipeline that always fails + tiny deadline → `PausedRun`.
- Content-policy path unchanged (unit via existing tests).
- Web job maps paused status.

## Non-goals

Push notifications; systemd/launchd daemon; changing Agnes read timeout (optional follow-up).
