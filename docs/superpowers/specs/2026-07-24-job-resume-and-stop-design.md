# Job resume on refresh + cooperative stop — Design

Date: 2026-07-24  
Status: approved for planning

## Goal

1. **Refresh restore:** If generation is in progress and the user reloads the page, the UI reconnects to the same job and looks like it never left (progress, log, timing, project id).
2. **Stop generation:** A visible control cooperatively pauses the run at safe boundaries, persists checkpoint, and allows later resume with the same `project_id`.

## Decisions

| Topic | Choice |
|-------|--------|
| Stop semantics | Cooperative pause (finish current in-flight HTTP, then stop) |
| Refresh reconnect | `localStorage` key with `job_id` + `project_id` (+ form fields) |
| Job storage | Keep in-memory `JOBS` (server restart → reconnect fails gracefully) |
| Cancel plumbing | Cancel flag / event checked at panel, chunk, and supervisor-retry boundaries |

## §1 Refresh restore (`localStorage`)

**Key:** `inkstone.activeJob`

```json
{
  "job_id": "<12-hex>",
  "project_id": "<id>",
  "format": "webtoon",
  "text": "...",
  "style_guide": "..."
}
```

**Write:** When `/api/generate` or regen returns `{job_id, project_id}`, persist the key and fill the project id input.

**On live-mode page load:**

1. Read `inkstone.activeJob`.
2. `GET /api/job/<job_id>`.
3. If `running`: restore progress UI, start poll + timing tick, show Stop.
4. If `paused`: restore progress UI / partial results, show pause reason, enable Generate, keep `project_id`.
5. If `done`: render results if payload has panels; then clear the key (or clear after render).
6. If `error` / 404: clear the key; leave a normal blank form. On 404 after server restart, optional one-line hint: resume with the same project id.

**Clear key when:** new successful terminal `done`/`error` handled, or 404, or user starts a new generate that replaces the key.

**Limits:** Same browser + origin only. Restarting `web/server.py` drops `JOBS`; reconnect gets 404 and clears the key — disk checkpoint under `comic_out/<project_id>/` remains for a fresh Generate with that id.

## §2 Cooperative stop

**API:** `POST /api/job/<job_id>/stop`  
- Sets `cancel_requested=true` on the job (and signals a shared `threading.Event` / callback).  
- Returns `{ok: true}` or 404 if unknown job.  
- Does **not** kill the worker thread immediately.

**Pipeline:**

- Web passes a cancel checker into `run_until_complete` / `creative_comic`.
- Check **before each panel**, **at each chunk boundary**, and **before supervisor retry sleep**.
- On cancel: persist `state.json` (including `active_elapsed_seconds`), release `.inkstone.lock`, return `PausedRun` (or equivalent) with reason `stopped by user`.
- Do **not** abort an in-flight single image/chat HTTP call; wait for it to finish or time out.

**Job status after stop:** `paused` with `pause_reason` describing user stop. UI matches existing paused handling (message + Generate enabled).

**UI:**

- Button **停止生成** next to Generate, visible only while `status === "running"`.
- On click: `POST .../stop`, disable button to「正在停止…」until poll sees `paused`/`error`/`done`.
- Demo mode: Stop only cancels the client-side demo animation (no API).

**Lock:** Stop path **must** release `.inkstone.lock` so the same `project_id` can resume.

## §3 Boundaries & tests

**Boundaries**

- One `activeJob` per browser origin; a new Generate overwrites the key.
- Server restart → 404 → clear key; user resumes via project id.
- Stop does not interrupt the current HTTP; next unit of work is skipped.

**Tests**

- Stop API sets cancel flag; fake pipeline exits at boundary → job `paused`.
- Cancel honored at panel / chunk checkpoints.
- After stop, same `project_id` can start another job (lock released).
- localStorage helpers: write/read; clear on 404/done as specified.

## Out of scope

- Persisting jobs to disk / surviving server restart without re-Generate
- Hard abort of in-flight HTTP
- Cross-browser / multi-device session sync
- ETA accuracy redesign (separate track)

## Acceptance

1. Start generate → refresh → progress UI returns and continues updating for the same job.
2. Click Stop → within one panel/chunk boundary, status becomes paused; panels already done remain; same project id can continue.
3. Restart web server → refresh clears stale job key without crashing; Generate with saved project id resumes from checkpoint.
