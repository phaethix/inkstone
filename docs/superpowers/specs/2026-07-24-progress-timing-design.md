# Progress timing & ETA (UI) — Design

Date: 2026-07-24  
Status: approved for planning

## Goal

Show **elapsed active generation time** and **estimated remaining time** on the Web UI progress area. After checkpoint resume, elapsed **continues from the previous cumulative total** (idle time between runs does not count).

## Decisions

| Topic | Choice |
|-------|--------|
| Elapsed meaning | Cumulative **active** generation seconds only |
| ETA display | Remaining duration (e.g. `预计还剩 2h 10m`), not clock time |
| Architecture | Persist in `state.json`; server returns values; UI local 1s tick + poll calibrate |
| Supervisor deadline | Still uses **this session** wall-clock only (not cumulative UI elapsed) |

## Data model

Add to `ProjectState` (`state.json`):

```text
active_elapsed_seconds: float = 0.0
```

- Missing field on old projects → treat as `0.0` (`extra="ignore"` + default).
- Never decreases; only written on persist of a finished/paused/errored session (or mid-run checkpoint).

## Server lifecycle

1. **Job start**
   - Load `base_elapsed` from existing `state.json` if present, else `0`.
   - Store on job: `base_elapsed`, `session_started_at` (monotonic).
   - Seed progress as today (`estimate_progress`).
2. **While running**
   - `elapsed = base_elapsed + (monotonic() - session_started_at)`.
   - `remaining`: if `progress >= 0.05` and `progress < 1.0`,  
     `remaining = elapsed * (1 - p) / p`; if `progress >= 1.0`, `0`; else `null`.
   - On each progress callback, optionally persist `active_elapsed_seconds = elapsed` so crashes lose little.
3. **Pause / done / error**
   - Persist `active_elapsed_seconds = elapsed` into `state.json`.
   - Job payload keeps final `elapsed_seconds` / `remaining_seconds`.
4. **Next resume**
   - Read persisted base again; continue from there.

`PausedRun.elapsed_seconds` (and job `elapsed_seconds`) mean **cumulative active elapsed**, aligned with the UI — not “this process wall clock only”.

Supervisor wall-clock deadline remains session-local and must **not** use `active_elapsed_seconds`.

## API

`GET /api/job/{id}` includes:

- `elapsed_seconds: float | null` — cumulative active seconds while known
- `remaining_seconds: float | null` — ETA remaining, or `null` when too early / unknown

## UI

In the progress meta row (alongside status + %):

- `已用 {fmt(elapsed)} · 预计还剩 {fmt(remaining)}`
- If `remaining_seconds` is null → `预计还剩 估算中`
- Local 1s tick while `status === "running"`; each poll resets from server values
- On `done` / `paused` / `error`: stop tick; keep showing final elapsed (and remaining `0` or last value / 估算中 as appropriate)

Format helper: humanize seconds as `Xh Ym` / `Ym Zs` / `Zs` (compact Chinese-friendly labels as above).

## Out of scope

- Estimated end clock time (e.g. 18:42)
- Per-stage timing breakdown in UI
- Changing rate limits or supervisor deadline hours

## Testing

- Schema: default `0.0`; round-trip in `state.json`
- Cumulative: `base=100`, session +5s → reported ≈105; after persist, next seed base=105
- ETA: `elapsed=100`, `p=0.25` → `remaining=300`; `p < 0.05` → `null`
- Job status payload includes both timing fields

## Acceptance

1. Fresh run: elapsed ticks up from ~0; after enough progress, remaining appears and shrinks as progress rises.
2. Resume same `project_id`: elapsed starts near previous cumulative, not 0.
3. Idle overnight between pause and resume does not inflate elapsed.
4. Supervisor 24h pause still triggers on session wall-clock only.
