"""Inkstone local Web UI server (zero extra dependencies).

A single-file browser UI (``index.html``) talks to this tiny backend, which
runs the real ``creative_comic`` pipeline and streams progress + results.

The backend uses only the Python standard library (``http.server`` +
``threading`` + ``asyncio``) so Inkstone needs no new pip dependency for the
UI. Start it with your API key in the environment (or ``.env``):

    AGNES_API_KEY=sk-xxx python web/server.py
    # then open http://127.0.0.1:8000

Routes:
    GET  /                        -> serves index.html
    POST /api/generate            -> {text, format?, style_guide?, project_id?}
                                    -> {job_id, project_id}
    GET  /api/job/<job_id>        -> job status + panels + review fields
    GET  /api/projects            -> list projects from comic_out/*/state.json
    GET  /api/experiments         -> list planning-only A/B experiments
    GET  /api/sequences           -> list sequential-recursion runs
    GET  /api/sequence/<id>       -> rounds, current candidates, checkpoints
    POST /api/sequence/<id>/pick  -> {choice, why} -> current round selection
    POST /api/sequence/<id>/advance -> compose + plan the next round
    GET  /api/experiment/<id>     -> one experiment (labels hidden until picked)
    POST /api/experiment/<id>/pick -> {choice, why} -> selection.json
    POST /api/experiment/<id>/compose -> {merge_from?, components?} -> adopted/
    POST /api/job/<job_id>/stop   -> cooperative cancel -> {ok: true} or 404
    POST /api/project/<id>/review -> {action: merge|dismiss, new_name, candidate}
    POST /api/project/<id>/regen  -> {stale?: true, keys?: [...]} -> {job_id, project_id}
    GET  /files/<path>            -> serves generated artifacts (path-safe)

Generation runs in a background thread (it makes real upstream API calls and
can take minutes), so the UI polls ``/api/job/<id>`` until status is "done".
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# Make the repo root importable so `core` resolves regardless of CWD.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pydantic import ValidationError  # noqa: E402

from core.api import get_chat_provider, get_image_provider  # noqa: E402
from core.comic.identity import (  # noqa: E402
    dismiss_character_alias,
    force_regen_panels,
    merge_character_alias,
    remember_merged_alias,
)
from core.comic.ledger import LEDGER_ESCALATION_THRESHOLD, ConsistencyLedger  # noqa: E402
from core.comic.locks import exclusive_lock  # noqa: E402
from core.comic.reader import chapter_pages, write_reader  # noqa: E402
from core.config import (  # noqa: E402
    data_dir,
    experiments_dir,
    job_ttl_seconds,
    ui_host,
    ui_max_body_bytes,
    ui_port,
)
from core.pipelines.creative_comic import estimate_progress  # noqa: E402
from core.pipelines.run_until_complete import PausedRun, run_until_complete  # noqa: E402
from core.pipelines.timing import estimate_remaining  # noqa: E402
from core.schemas import ProjectState  # noqa: E402

logger = logging.getLogger(__name__)

OUTPUT_DIR = ROOT / "comic_out"
EXPERIMENTS_DIR = experiments_dir()
# Every knob below is resolved by ``core.config``, so it joins ``ALL_ENV_VARS``
# and the h_env partition test. A bare ``os.environ`` read here would be
# invisible to both.
HOST = ui_host()
PORT = ui_port()
# Cap novel-paste / JSON POSTs (three-body-scale text is ~0.5–1 MB; 8 MB is ample).
MAX_JSON_BODY_BYTES = ui_max_body_bytes()

_PROJECT_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
_EXPERIMENT_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,80}$")

# In-memory job registry. Each job writes under comic_out/<project_id>/.
JOBS: dict[str, dict] = {}
# Purged job ids → wall-clock time purged; lookups return 410 until tombstone TTL.
JOB_TOMBSTONES: dict[str, float] = {}
JOBS_LOCK = threading.Lock()

_TERMINAL_JOB_STATUSES = frozenset({"done", "error", "paused"})
_JOB_LOG_MAX_LINES = 500


class _BodyTooLarge(ValueError):
    """Raised when a POST body exceeds ``MAX_JSON_BODY_BYTES``."""


def _job_ttl_seconds() -> int:
    return job_ttl_seconds()


def _append_job_log(job: dict, message: str) -> None:
    """Append one log line under ``JOBS_LOCK`` (pipeline thread + GET race-safe)."""
    with JOBS_LOCK:
        log = job["log"]
        log.append(message)
        overflow = len(log) - _JOB_LOG_MAX_LINES
        if overflow > 0:
            del log[:overflow]


def _mark_job_finished(job: dict) -> None:
    if job.get("status") in _TERMINAL_JOB_STATUSES:
        job["finished_at"] = time.time()


def _purge_expired_jobs_locked() -> None:
    """Drop finished jobs past TTL and expire old tombstones. Caller must hold JOBS_LOCK."""
    now = time.time()
    ttl = _job_ttl_seconds()
    for job_id, job in list(JOBS.items()):
        finished = job.get("finished_at")
        if finished is not None and now - float(finished) > ttl:
            del JOBS[job_id]
            JOB_TOMBSTONES[job_id] = now
    for job_id, expired_at in list(JOB_TOMBSTONES.items()):
        if now - float(expired_at) > ttl:
            del JOB_TOMBSTONES[job_id]


def _resolve_job(job_id: str) -> tuple[dict | None, str | None]:
    """Return (job, error) where error is 'expired', 'unknown', or None."""
    with JOBS_LOCK:
        _purge_expired_jobs_locked()
        job = JOBS.get(job_id)
        if job is not None:
            finished = job.get("finished_at")
            if finished is not None and time.time() - float(finished) > _job_ttl_seconds():
                del JOBS[job_id]
                JOB_TOMBSTONES[job_id] = time.time()
                _purge_expired_jobs_locked()
                return None, "expired"
            return job, None
        if job_id in JOB_TOMBSTONES:
            return None, "expired"
        return None, "unknown"


def _load_dotenv() -> None:
    """Best-effort .env loader (no python-dotenv dependency).

    Never overrides keys already present in the environment. Having
    ``AGNES_API_KEY`` set must not skip loading the rest of ``.env``.
    """
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


class _JobLogHandler(logging.Handler):
    """Append pipeline log records into the job's log buffer."""

    def __init__(self, job: dict) -> None:
        super().__init__()
        self._job = job

    def emit(self, record: logging.LogRecord) -> None:
        _append_job_log(self._job, record.getMessage())


def _reader_url(project_id: str | None) -> str | None:
    """URL of the browser reader when the project already has page images."""
    if not project_id:
        return None
    try:
        project = _project_dir(project_id)
    except ValueError:
        return None
    if not chapter_pages(project / "pages"):
        return None
    return f"/files/{project.name}/reader.html"


def _file_url(local_path: str) -> str:
    """Turn an on-disk path under OUTPUT_DIR into a /files/<rel> URL."""
    target = Path(local_path).resolve()
    rel = target.relative_to(OUTPUT_DIR.resolve())
    return f"/files/{rel.as_posix()}"


def _providers_configured() -> bool:
    """Validate the selected provider pair without making an upstream request."""
    try:
        get_chat_provider()
        get_image_provider()
    except (RuntimeError, ValueError):
        return False
    return True


def _is_within(path: str | Path, root: Path) -> bool:
    try:
        Path(path).resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _is_output_file(path: str) -> bool:
    target = Path(path).resolve()
    return target.is_file() and _is_within(target, OUTPUT_DIR)


def validate_project_id(project_id: str) -> str:
    """Return a safe project id or raise ValueError."""
    if not _PROJECT_ID_RE.match(project_id or ""):
        raise ValueError("invalid project_id (use 1-64 chars: letters, digits, _-)")
    return project_id


def _project_dir(project_id: str) -> Path:
    return OUTPUT_DIR / validate_project_id(project_id)


def _state_snapshot(state: ProjectState, out_dir: Path | None = None) -> dict:
    """Snapshot state for the UI; `ledger_pending` aggregates breaks per character."""
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
        "ledger_escalation_threshold": LEDGER_ESCALATION_THRESHOLD,
    }


def _fill_job_from_project(job: dict, proj) -> None:
    ordered_panels = sorted(
        proj.state.generated.panels.items(),
        key=lambda item: (item[1].chunk_index, item[1].panel_index),
    )
    job["panels"] = [
        {"id": generated.source_panel_id or panel_key, "url": _file_url(generated.local)}
        for panel_key, generated in ordered_panels
        if _is_output_file(generated.local)
    ]
    job["webtoon"] = _file_url(proj.webtoon) if proj.webtoon else None
    job["pdf"] = _file_url(proj.pdf) if proj.pdf else None
    job["reader"] = _reader_url(proj.project_id)
    job.update(_state_snapshot(proj.state))
    job["project_id"] = proj.project_id


def _fill_job_from_paused(job: dict, paused: PausedRun) -> None:
    job["project_id"] = paused.project_id
    job["pause_reason"] = paused.reason
    # `paused.elapsed_seconds` is this run's session-only wall time, not the
    # job's cumulative elapsed_seconds — leave that to _refresh_job_timing.
    _append_job_log(job, f"session wall time: {paused.elapsed_seconds:.1f}s")
    if paused.state is not None:
        job.update(_state_snapshot(paused.state))
        ordered_panels = sorted(
            paused.state.generated.panels.items(),
            key=lambda item: (item[1].chunk_index, item[1].panel_index),
        )
        job["panels"] = [
            {"id": generated.source_panel_id or panel_key, "url": _file_url(generated.local)}
            for panel_key, generated in ordered_panels
            if _is_output_file(generated.local)
        ]
        job["reader"] = _reader_url(paused.project_id)


def _load_checkpoint_state(project_id: str) -> ProjectState | None:
    """Load project state from disk, or None if missing or unreadable.

    Serves progress seeding, which reads the heavy plan caches to keep the
    resumed estimate honest. A corrupt ``cache.json`` must not block that, so
    the full load falls back to the projection-only read; a corrupt
    ``state.json`` still returns ``None``.
    """
    state_path = OUTPUT_DIR / project_id / "state.json"
    if not state_path.is_file():
        return None
    try:
        return ProjectState.load(state_path)
    except ValidationError:
        try:
            return ProjectState.load(state_path, caches=False)
        except Exception:  # noqa: BLE001
            return None
    except Exception:  # noqa: BLE001
        return None


def _seed_job_progress(project_id: str) -> tuple[float, str]:
    """Seed UI progress from an existing checkpoint so resume does not flash 0%."""
    state = _load_checkpoint_state(project_id)
    if state is None:
        return 0.0, "init"
    return estimate_progress(state), "resume"


def _seed_job_resume_fields(project_id: str) -> dict:
    """Seed render_mode/pages_done/skipped_pages from checkpoint when resuming."""
    state = _load_checkpoint_state(project_id)
    if state is None:
        return {
            "render_mode": "finished_page",
            "pages_done": [],
            "skipped_pages": [],
        }
    snapshot = _state_snapshot(state)
    return {
        "render_mode": snapshot["render_mode"],
        "pages_done": snapshot["pages_done"],
        "skipped_pages": snapshot["skipped_pages"],
    }


def _timing_path(project_id: str) -> Path:
    """Web-layer-owned timing file for a project (kept out of state.json)."""
    return OUTPUT_DIR / project_id / "timing.json"


def _load_timing_elapsed(project_id: str) -> float:
    """Cumulative elapsed seconds: timing.json first, legacy checkpoint field as fallback."""
    path = _timing_path(project_id)
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return float(data.get("active_elapsed_seconds") or 0.0)
        except Exception:  # noqa: BLE001
            return 0.0
    # Backward compat: checkpoints written before the timing split.
    state_path = OUTPUT_DIR / project_id / "state.json"
    if state_path.is_file():
        try:
            # Projection-only: timing is a light field, and a corrupt
            # cache.json must not zero the elapsed clock.
            return float(ProjectState.load(state_path, caches=False).active_elapsed_seconds or 0.0)
        except Exception:  # noqa: BLE001
            return 0.0
    return 0.0


def _seed_job_timing(project_id: str) -> float:
    """Seed a job's cumulative elapsed time from prior runs."""
    return _load_timing_elapsed(project_id)


def _job_elapsed(job: dict) -> float:
    """Cumulative elapsed seconds: checkpointed base plus the current session."""
    base = float(job.get("base_elapsed") or 0.0)
    started = job.get("session_started_at")
    if started is None:
        return base
    return base + max(0.0, time.monotonic() - float(started))


def _refresh_job_timing(job: dict) -> None:
    """Recompute a job's cumulative elapsed/remaining fields in place."""
    elapsed = _job_elapsed(job)
    job["elapsed_seconds"] = elapsed
    progress = float(job.get("progress") or 0.0)
    job["remaining_seconds"] = estimate_remaining(elapsed, progress)


def _persist_active_elapsed(project_id: str, elapsed: float) -> None:
    """Persist cumulative elapsed time to timing.json (single-writer, atomic).

    Deliberately not state.json: the pipeline owns that file and full-dumps it,
    so a web-layer load-modify-save there raced and lost updates, at O(state
    size) IO per panel. timing.json is tiny and owned solely by this layer.
    """
    path = _timing_path(project_id)
    if not path.parent.is_dir():
        return
    try:
        # Never decrease if an older write races a newer one.
        prev = _load_timing_elapsed(project_id)
        value = max(prev, float(elapsed))
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".timing-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"active_elapsed_seconds": value}, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    except Exception:  # noqa: BLE001
        logger.warning("could not persist timing.json for %s", project_id)


def _run_job(
    job_id: str,
    text: str,
    fmt: str,
    style_guide: str | None,
    project_id: str,
    panel_keys: list[str] | None = None,
) -> None:
    job = JOBS[job_id]
    handler = _JobLogHandler(job)
    handler.setLevel(logging.INFO)
    root = logging.getLogger()
    root.addHandler(handler)

    def _on_progress(stage: str, percent: float | None) -> None:
        with JOBS_LOCK:
            job["stage"] = stage
            if percent is not None:
                # Never let a resume tick go backwards on the UI bar.
                prev = float(job.get("progress") or 0.0)
                job["progress"] = max(prev, float(percent))
            _refresh_job_timing(job)
        _persist_active_elapsed(project_id, float(job["elapsed_seconds"]))

    try:
        out_dir = _project_dir(project_id)
        cancel_event: threading.Event = job["cancel_event"]
        result = asyncio.run(
            run_until_complete(
                text,
                output_dir=str(out_dir),
                project_id=project_id,
                output_format=fmt,
                style_guide=style_guide,
                panel_keys=panel_keys,
                progress_callback=_on_progress,
                cancel_check=cancel_event.is_set,
                data_dir=str(data_dir()),
            )
        )
        if isinstance(result, PausedRun):
            _fill_job_from_paused(job, result)
            job["status"] = "paused"
            _append_job_log(job, f"PAUSED: {result.reason}")
        else:
            _fill_job_from_project(job, result)
            job["status"] = "done"
            job["progress"] = 1.0
            job["stage"] = "done"
    except Exception as exc:  # noqa: BLE001 — surface any failure to the UI
        job["status"] = "error"
        job["error"] = str(exc)
        _append_job_log(job, f"ERROR: {exc}")
    finally:
        _refresh_job_timing(job)
        _persist_active_elapsed(project_id, float(job["elapsed_seconds"]))
        _mark_job_finished(job)
        root.removeHandler(handler)


def _start_job(
    text: str,
    fmt: str,
    style_guide: str | None,
    project_id: str | None = None,
    panel_keys: list[str] | None = None,
) -> tuple[str, str]:
    pid = validate_project_id(project_id) if project_id else uuid.uuid4().hex[:12]
    job_id = uuid.uuid4().hex[:12]
    seeded_progress, seeded_stage = _seed_job_progress(pid)
    seeded_resume = _seed_job_resume_fields(pid)
    base_elapsed = _seed_job_timing(pid)
    with JOBS_LOCK:
        _purge_expired_jobs_locked()
        JOBS[job_id] = {
            "status": "running",
            "log": [],
            "panels": [],
            "webtoon": None,
            "pdf": None,
            "reader": None,
            "skipped": [],
            "skipped_chunks": [],
            "needs_review": [],
            "stale_panels": [],
            **seeded_resume,
            "project_id": pid,
            "pause_reason": None,
            "base_elapsed": base_elapsed,
            "session_started_at": time.monotonic(),
            "elapsed_seconds": base_elapsed,
            "remaining_seconds": estimate_remaining(base_elapsed, seeded_progress),
            "progress": seeded_progress,
            "stage": seeded_stage,
            "error": None,
            "cancel_event": threading.Event(),
            "cancel_requested": False,
        }
    threading.Thread(
        target=_run_job,
        args=(job_id, text, fmt, style_guide, pid, panel_keys),
        daemon=True,
    ).start()
    return job_id, pid


def request_stop(job_id: str) -> tuple[bool, str | None]:
    """Signal a running job's cancel_event.

    Returns (ok, error) where error is ``expired``, ``unknown``, or None.
    """
    job, error = _resolve_job(job_id)
    if error is not None:
        return False, error
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job is None:
            return False, "unknown"
        job["cancel_requested"] = True
        ev = job.get("cancel_event")
        if isinstance(ev, threading.Event):
            ev.set()
        return True, None


@contextmanager
def _project_write_lock(out_dir: Path):
    """Exclusive project lock around one short UI-initiated state mutation.

    The pipeline holds ``<out>/.inkstone.lock`` for a whole run. A write that
    skipped it would load an older ``state.json`` and then overwrite the run's
    checkpoint with a full dump — last writer wins, silently. Review merge,
    ledger confirm and regen therefore take the same lock.

    ``blocking=False``: while a job runs the request is refused with 409 rather
    than queued behind a render that can take hours. The lock file is shared
    with ``core.pipelines.creative_comic._project_lock``; a different name would
    be a second lock and would not exclude anything.
    """
    # A missing project must answer 404 without leaving an empty directory (and
    # a lock file) behind: locking is not creation.
    if not out_dir.is_dir():
        raise FileNotFoundError(f"no project directory: {out_dir}")
    with exclusive_lock(
        out_dir / ".inkstone.lock",
        blocking=False,
        busy_message=f"project is already running: {out_dir}",
    ):
        yield


def _load_project_state(project_id: str) -> tuple[Path, ProjectState]:
    out_dir = _project_dir(project_id)
    state_path = out_dir / "state.json"
    if not state_path.is_file():
        raise FileNotFoundError(f"no state.json for project {project_id}")
    # Full load: merge/dismiss rewrites the heavy caches and its save must
    # persist them, so this reader needs cache.json (a corrupt one raises).
    state = ProjectState.load(state_path)
    return out_dir, state


def apply_review(project_id: str, action: str, new_name: str, candidate: str) -> dict:
    """Merge or dismiss an alias suggestion; persist state.json."""
    out_dir = _project_dir(project_id)
    with _project_write_lock(out_dir):
        _, state = _load_project_state(project_id)
        ledger = ConsistencyLedger.load_or_rebuild(out_dir / "consistency.json", state)
        if action == "merge":
            try:
                merge_character_alias(state, new_name, candidate, ledger=ledger)
            except KeyError as exc:
                # Concurrent merges can remove the kept name between the UI load
                # and this request. Answer 400 instead of leaking a 500.
                raise ValueError(str(exc)) from exc
            remember_merged_alias(state, new_name, candidate)
        elif action == "dismiss":
            dismiss_character_alias(state, new_name, candidate)
        else:
            raise ValueError("action must be 'merge' or 'dismiss'")
        state.save(out_dir / "state.json")
        ledger.save(out_dir / "consistency.json")
        return _state_snapshot(state, out_dir)


def apply_ledger_confirm(
    project_id: str,
    character: str,
    pages: list[str] | None = None,
) -> dict:
    """Confirm all or a subset of a continuity review; persist the ledger.

    ``pages=None`` accepts every current page. A subset must name at least one
    page of this character; unknown characters or page keys raise ``ValueError``
    so the handler can answer 400/404 instead of claiming success.
    """
    out_dir = _project_dir(project_id)
    with _project_write_lock(out_dir):
        _, state = _load_project_state(project_id)
        ledger = ConsistencyLedger.load_or_rebuild(out_dir / "consistency.json", state)
        if character not in ledger.characters:
            raise KeyError(f"unknown character: {character}")
        if pages is not None:
            current = set(ledger.characters[character].pages)
            selected = [page for page in pages if page in current]
            if not selected:
                raise ValueError(f"none of the pages belong to {character}")
            pages = selected
        ledger.confirm_review(character, pages=pages)
        ledger.save(out_dir / "consistency.json")
        return _state_snapshot(state, out_dir)


def start_regen_job(
    project_id: str,
    *,
    stale: bool = False,
    keys: list[str] | None = None,
    fmt: str = "webtoon",
    style_guide: str | None = None,
) -> tuple[str, str]:
    """Force-regen stale/selected panels and start a background job."""
    out_dir = _project_dir(project_id)
    # The mutation is locked; the job itself must start after the lock is
    # released, because ``creative_comic`` takes the same lock for its whole run.
    with _project_write_lock(out_dir):
        _, state = _load_project_state(project_id)
        source_path = out_dir / "source.txt"
        if not source_path.is_file():
            raise FileNotFoundError("project has no source.txt; regenerate from scratch")
        text = source_path.read_text(encoding="utf-8")
        target_keys = list(keys or [])
        if stale:
            # ``stale_panels`` covers panel_compose; ``stale_pages`` covers the
            # default finished_page mode. A UI "redraw affected" request must be
            # able to start a job from either, otherwise the merge review flow is
            # a no-op.
            target_keys = list(
                dict.fromkeys([*target_keys, *state.stale_panels, *state.stale_pages])
            )
        if not target_keys:
            raise ValueError("no panel keys to regenerate")
        # Panel keys and page keys live in disjoint state fields and never
        # coexist in one project: ``render_mode`` selects exactly one render path
        # (0b). Route by the mode, not by guessing from the key string - the
        # positional page key ``c0000-p0000`` is shape-identical to a panel key,
        # so a delimiter heuristic would misroute it into ``stale_panels`` and the
        # repaint would be a no-op.
        if state.render_mode == "finished_page":
            state.stale_pages = sorted(set(state.stale_pages) | set(target_keys))
            state.pages_done = [k for k in state.pages_done if k not in set(target_keys)]
        else:
            force_regen_panels(state, target_keys)
        state.save(out_dir / "state.json")
    return _start_job(text, fmt, style_guide, project_id=project_id, panel_keys=target_keys)


def list_projects() -> list[dict]:
    """Scan comic_out for state.json checkpoints (read-only, no pipeline writes)."""
    if not OUTPUT_DIR.is_dir():
        return []
    projects: list[dict] = []
    for entry in sorted(OUTPUT_DIR.iterdir()):
        if not entry.is_dir():
            continue
        state_path = entry / "state.json"
        if not state_path.is_file():
            continue
        try:
            # Projection-only: the listing needs light fields, and a corrupt
            # cache.json must not hide a project with an intact checkpoint.
            state = ProjectState.load(state_path, caches=False)
        except Exception:  # noqa: BLE001
            continue
        projects.append(
            {
                "id": entry.name,
                "stage": state.stage,
                "panels_done": list(state.panels_done),
                "render_mode": state.render_mode,
                "pages_done": list(state.pages_done),
                "skipped_pages": list(state.skipped_pages),
                "has_timing": (entry / "timing.json").is_file(),
            }
        )
    return projects


def validate_experiment_id(experiment_id: str) -> str:
    """Reject traversal and hidden ids; the id is a single directory name."""
    value = (experiment_id or "").strip()
    if not _EXPERIMENT_ID_RE.fullmatch(value) or value in {".", ".."}:
        raise ValueError(f"invalid experiment id: {experiment_id!r}")
    return value


def _experiment_dir(experiment_id: str) -> Path:
    root = EXPERIMENTS_DIR.resolve()
    target = (root / validate_experiment_id(experiment_id)).resolve()
    if target.parent != root:
        raise ValueError(f"experiment escapes {root}")
    return target


def _experiment_manifest(path: Path) -> dict:
    manifest_path = path / "experiment.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"no experiment.json under {path}")
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def _experiment_selection(path: Path) -> dict | None:
    selection_path = path / "selection.json"
    if not selection_path.is_file():
        return None
    return json.loads(selection_path.read_text(encoding="utf-8"))


def list_experiments() -> list[dict]:
    """List planning-only A/B experiments; candidate labels stay hidden."""
    if not EXPERIMENTS_DIR.is_dir():
        return []
    out: list[dict] = []
    for entry in sorted(EXPERIMENTS_DIR.iterdir(), reverse=True):
        if not entry.is_dir():
            continue
        try:
            manifest = _experiment_manifest(entry)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        selection = _experiment_selection(entry)
        out.append(
            {
                "id": entry.name,
                "status": manifest.get("status", "unknown"),
                "created_at": manifest.get("created_at"),
                "chunk_index": manifest.get("chunk_index"),
                "genre": manifest.get("genre"),
                "cost": manifest.get("cost"),
                "candidates": sorted(manifest.get("candidates") or {}),
                "chosen": (selection or {}).get("chosen"),
                "chosen_label": (selection or {}).get("chosen_label"),
            }
        )
    return out


def experiment_detail(experiment_id: str) -> dict:
    """Read one experiment; labels are revealed only after a selection exists."""
    path = _experiment_dir(experiment_id)
    manifest = _experiment_manifest(path)
    selection = _experiment_selection(path)
    candidates: dict[str, dict] = {}
    for anon, record in sorted((manifest.get("candidates") or {}).items()):
        directory = path / "candidates" / anon
        plan: dict | None = None
        structure: dict | None = None
        try:
            plan = json.loads((directory / "plan.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
        try:
            structure = json.loads(
                (directory / "structure_report.json").read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            pass
        item = {
            "id": anon,
            "harness_digest": record.get("harness_digest"),
            "chat_calls": record.get("chat_calls"),
            "prompt_digest": record.get("prompt_digest"),
            "plan": plan,
            "structure": structure,
        }
        if selection is not None:
            item["label"] = record.get("label")
        candidates[anon] = item
    composed = None
    compose_path = path / "compose.json"
    if compose_path.is_file():
        try:
            composed = json.loads(compose_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            composed = None
    return {
        "id": experiment_id,
        "status": manifest.get("status", "unknown"),
        "created_at": manifest.get("created_at"),
        "chunk_index": manifest.get("chunk_index"),
        "genre": manifest.get("genre"),
        "cost": manifest.get("cost"),
        "candidates": candidates,
        "selection": selection,
        "composed": composed,
    }


def compose_experiment_api(
    experiment_id: str,
    *,
    merge_from: str | None = None,
    components: list[str] | None = None,
) -> dict:
    """Compose the picked harness into the experiment's own ``adopted/`` tree.

    The Web UI never chooses a filesystem path: the output is fixed under the
    experiment directory, so a browser cannot write arbitrary harness trees.
    """
    from core.experiment import ExperimentError, compose_harness

    path = _experiment_dir(experiment_id)
    manifest = _experiment_manifest(path)
    selection = _experiment_selection(path)
    if selection is None:
        raise ValueError("pick a candidate before composing")
    chosen = str(selection.get("chosen") or "")
    candidates = manifest.get("candidates") or {}
    merge = (merge_from or "").strip().upper()
    comps = [str(component) for component in (components or [])]
    if merge:
        if merge not in candidates:
            raise ValueError(f"merge_from {merge!r} is not a candidate")
        if merge == chosen:
            raise ValueError("merge_from must be the other candidate")
        if not comps:
            comps = ["stage.page_plan"]
        for component in comps:
            if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,80}", component):
                raise ValueError(f"invalid component id: {component!r}")
    elif comps:
        raise ValueError("components require merge_from")
    out = path / "adopted"
    try:
        return compose_harness(
            path,
            choice=chosen,
            out_dir=out,
            merge_from=[merge] if merge else [],
            components=comps,
        )
    except ExperimentError as exc:
        if "output already exists" in str(exc):
            raise FileExistsError(str(exc)) from exc
        raise ValueError(str(exc)) from exc


def pick_experiment_api(experiment_id: str, choice: str, why: str) -> dict:
    """Record a human pick through the same writer the CLI uses."""
    from core.experiment import ExperimentError, pick_experiment

    path = _experiment_dir(experiment_id)
    try:
        return pick_experiment(path, choice=choice, why=why)
    except ExperimentError as exc:
        raise ValueError(str(exc)) from exc


def _sequence_dir(sequence_id: str) -> Path:
    root = EXPERIMENTS_DIR.resolve()
    target = (root / validate_experiment_id(sequence_id)).resolve()
    if target.parent != root:
        raise ValueError(f"sequence escapes {root}")
    if not (target / "sequence.json").is_file():
        raise FileNotFoundError(f"no sequence.json under {target}")
    return target


def _sequence_round_detail(sequence_dir: Path, round_number: int) -> dict | None:
    round_dir = sequence_dir / "rounds" / str(round_number)
    experiment_path = round_dir / "experiment.json"
    if not experiment_path.is_file():
        return None
    try:
        experiment = json.loads(experiment_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    selection = _experiment_selection(round_dir)
    candidates: dict[str, dict] = {}
    for anon, record in sorted((experiment.get("candidates") or {}).items()):
        directory = round_dir / "candidates" / anon
        plan = None
        structure = None
        try:
            plan = json.loads((directory / "plan.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
        try:
            structure = json.loads(
                (directory / "structure_report.json").read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            pass
        item = {
            "id": anon,
            "harness_digest": record.get("harness_digest"),
            "chat_calls": record.get("chat_calls"),
            "plan": plan,
            "structure": structure,
        }
        if selection is not None:
            item["label"] = record.get("label")
        candidates[anon] = item
    checkpoint = round_dir / "checkpoint"
    composed = None
    if (round_dir / "compose.json").is_file():
        try:
            composed = json.loads((round_dir / "compose.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            composed = None
    return {
        "round": round_number,
        "status": "picked" if selection is not None else experiment.get("status"),
        "candidates": candidates,
        "selection": selection,
        "composed": composed,
        "checkpoint": str(checkpoint) if checkpoint.is_dir() else None,
    }


def _sequence_summary(sequence_dir: Path) -> dict:
    sequence = json.loads((sequence_dir / "sequence.json").read_text(encoding="utf-8"))
    return {
        "id": sequence_dir.name,
        "status": sequence.get("status"),
        "rounds": sequence.get("rounds"),
        "current_round": sequence.get("current_round"),
        "completed_rounds": sequence.get("completed_rounds", []),
        "current_checkpoint": sequence.get("current_checkpoint"),
        "candidates": sequence.get("candidates", []),
    }


def list_sequences() -> list[dict]:
    if not EXPERIMENTS_DIR.is_dir():
        return []
    out: list[dict] = []
    for entry in sorted(EXPERIMENTS_DIR.iterdir(), reverse=True):
        if not entry.is_dir() or not (entry / "sequence.json").is_file():
            continue
        try:
            out.append(_sequence_summary(entry))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
    return out


def sequence_detail(sequence_id: str) -> dict:
    path = _sequence_dir(sequence_id)
    summary = _sequence_summary(path)
    sequence = json.loads((path / "sequence.json").read_text(encoding="utf-8"))
    current = int(sequence.get("current_round", 1))
    rounds: list[dict] = []
    for number in range(1, int(sequence.get("rounds", 1)) + 1):
        detail = _sequence_round_detail(path, number)
        if detail is not None:
            rounds.append(
                {
                    "round": number,
                    "status": detail["status"],
                    "chosen": (detail.get("selection") or {}).get("chosen"),
                    "checkpoint": detail.get("checkpoint"),
                }
            )
    return {
        **summary,
        "round_detail": _sequence_round_detail(path, current),
        "rounds_detail": rounds,
    }


def pick_sequence_api(sequence_id: str, choice: str, why: str) -> dict:
    from core.experiment import ExperimentError, pick_experiment

    path = _sequence_dir(sequence_id)
    sequence = json.loads((path / "sequence.json").read_text(encoding="utf-8"))
    if sequence.get("status") == "done":
        raise ValueError("sequence is already done")
    current = int(sequence.get("current_round", 1))
    round_dir = path / "rounds" / str(current)
    try:
        return pick_experiment(round_dir, choice=choice, why=why)
    except ExperimentError as exc:
        raise ValueError(str(exc)) from exc


async def advance_sequence_api(
    sequence_id: str,
    *,
    merge_from: list[str] | None = None,
    components: list[str] | None = None,
) -> dict:
    from core.experiment import ExperimentError, advance_sequence_async

    path = _sequence_dir(sequence_id)
    try:
        await advance_sequence_async(
            path,
            merge_from=merge_from or [],
            components=components or [],
        )
    except ExperimentError as exc:
        raise ValueError(str(exc)) from exc
    return sequence_detail(sequence_id)


class Handler(BaseHTTPRequestHandler):
    server_version = "InkstoneUI/0.1"

    def _send_json(self, obj, status: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    _MIME = {
        ".html": "text/html",
        ".css": "text/css",
        ".js": "application/javascript",
        ".json": "application/json",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".svg": "image/svg+xml",
        ".ico": "image/x-icon",
        ".pdf": "application/pdf",
        ".txt": "text/plain",
        ".woff2": "font/woff2",
    }

    def _send_file(self, path: Path, status: int = 200) -> None:
        data = path.read_bytes()
        self.send_response(status)
        mime = self._MIME.get(path.suffix, "application/octet-stream")
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args) -> None:  # quieter default logging
        pass

    def do_GET(self) -> None:
        if self.path == "/" or self.path == "/index.html":
            self._send_file(Path(__file__).resolve().parent / "index.html")
            return
        if self.path == "/api/health":
            self._send_json({"ok": True, "key": _providers_configured()})
            return
        if self.path.startswith("/assets/"):
            rel = self.path[len("/assets/") :]
            target = (ROOT / "assets" / rel).resolve()
            if target.is_file() and _is_within(target, ROOT / "assets"):
                self._send_file(target)
            else:
                self._send_json({"error": "not found"}, status=404)
            return
        if self.path.startswith("/files/"):
            rel = self.path[len("/files/") :]
            target = (OUTPUT_DIR / rel).resolve()
            if (
                target.name == "reader.html"
                and _is_within(target.parent, OUTPUT_DIR)
                and chapter_pages(target.parent / "pages")
            ):
                write_reader(target.parent)
            if target.is_file() and _is_within(target, OUTPUT_DIR):
                self._send_file(target)
            else:
                self._send_json({"error": "not found"}, status=404)
            return
        if self.path == "/api/projects":
            self._send_json(list_projects())
            return
        if self.path == "/api/experiments":
            self._send_json(list_experiments())
            return
        if self.path == "/api/sequences":
            self._send_json(list_sequences())
            return
        if self.path.startswith("/api/sequence/"):
            sequence_id = self.path[len("/api/sequence/") :]
            try:
                payload = sequence_detail(sequence_id)
            except ValueError as exc:
                self._send_json({"error": str(exc)}, status=400)
                return
            except FileNotFoundError as exc:
                self._send_json({"error": str(exc)}, status=404)
                return
            self._send_json(payload)
            return
        if self.path.startswith("/api/experiment/"):
            experiment_id = self.path[len("/api/experiment/") :]
            try:
                payload = experiment_detail(experiment_id)
            except ValueError as exc:
                self._send_json({"error": str(exc)}, status=400)
                return
            except FileNotFoundError as exc:
                self._send_json({"error": str(exc)}, status=404)
                return
            self._send_json(payload)
            return
        if self.path.startswith("/api/job/"):
            job_id = self.path.split("/")[-1]
            job, error = _resolve_job(job_id)
            if error == "expired":
                self._send_json(
                    {"error": "job expired (finished jobs are removed after TTL)"},
                    status=410,
                )
                return
            if job is None:
                self._send_json({"error": "unknown job"}, status=404)
                return
            with JOBS_LOCK:
                if job["status"] == "running":
                    _refresh_job_timing(job)
                payload = {
                    "status": job["status"],
                    "log": job["log"][-200:],
                    "panels": job["panels"],
                    "webtoon": job["webtoon"],
                    "pdf": job["pdf"],
                    "reader": job.get("reader"),
                    "skipped": job.get("skipped", []),
                    "skipped_chunks": job.get("skipped_chunks", []),
                    "needs_review": job.get("needs_review", []),
                    "stale_panels": job.get("stale_panels", []),
                    "render_mode": job.get("render_mode", "finished_page"),
                    "pages_done": job.get("pages_done", []),
                    "skipped_pages": job.get("skipped_pages", []),
                    "project_id": job.get("project_id"),
                    "pause_reason": job.get("pause_reason"),
                    "elapsed_seconds": job.get("elapsed_seconds"),
                    "remaining_seconds": job.get("remaining_seconds"),
                    "progress": job.get("progress"),
                    "stage": job.get("stage"),
                    "error": job["error"],
                    "cancel_requested": bool(job.get("cancel_requested")),
                }
            self._send_json(payload)
            return
        self._send_json({"error": "not found"}, status=404)

    def do_POST(self) -> None:
        if not self._post_origin_allowed():
            self._send_json(
                {"error": "cross-origin POST blocked (open the UI from this server)"},
                status=403,
            )
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_json({"error": "invalid Content-Length"}, status=400)
            return
        if length > MAX_JSON_BODY_BYTES:
            self._send_json(
                {"error": f"request body too large (max {MAX_JSON_BODY_BYTES} bytes)"},
                status=413,
            )
            return
        try:
            if self.path == "/api/generate":
                self._post_generate()
                return
            if self.path.startswith("/api/job/") and self.path.endswith("/stop"):
                self._post_stop()
                return
            if self.path.startswith("/api/experiment/") and self.path.endswith("/pick"):
                self._post_experiment_pick()
                return
            if self.path.startswith("/api/experiment/") and self.path.endswith("/compose"):
                self._post_experiment_compose()
                return
            if self.path.startswith("/api/sequence/") and self.path.endswith("/pick"):
                self._post_sequence_pick()
                return
            if self.path.startswith("/api/sequence/") and self.path.endswith("/advance"):
                self._post_sequence_advance()
                return
            if self.path.startswith("/api/project/") and self.path.endswith("/review"):
                self._post_review()
                return
            if self.path.startswith("/api/project/") and self.path.endswith("/ledger/confirm"):
                self._post_ledger_confirm()
                return
            if self.path.startswith("/api/project/") and self.path.endswith("/regen"):
                self._post_regen()
                return
        except _BodyTooLarge as exc:
            self._send_json({"error": str(exc)}, status=413)
            return
        except RuntimeError as exc:
            # A project lock held by a running job is a conflict, not a server
            # fault: the caller can retry once the run pauses or finishes.
            self._send_json({"error": str(exc)}, status=409)
            return
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=400)
            return
        except FileNotFoundError as exc:
            self._send_json({"error": str(exc)}, status=404)
            return
        except (json.JSONDecodeError, TypeError):
            self._send_json({"error": "invalid JSON"}, status=400)
            return
        self._send_json({"error": "not found"}, status=404)

    def _post_origin_allowed(self) -> bool:
        """Block browser CSRF from other sites; allow curl (no Origin / Fetch metadata)."""
        site = (self.headers.get("Sec-Fetch-Site") or "").strip().lower()
        if site == "cross-site":
            return False
        origin = (self.headers.get("Origin") or "").strip()
        if not origin:
            return True
        host = (self.headers.get("Host") or "").strip()
        if not host:
            return False
        allowed = {f"http://{host}", f"https://{host}"}
        # Also accept loopback aliases when bound locally.
        if HOST in _LOOPBACK_HOSTS:
            for loop in ("127.0.0.1", "localhost", "::1"):
                allowed.add(f"http://{loop}:{PORT}")
                allowed.add(f"https://{loop}:{PORT}")
        return origin in allowed

    def _read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("invalid Content-Length") from exc
        if length < 0:
            raise ValueError("invalid Content-Length")
        if length > MAX_JSON_BODY_BYTES:
            raise _BodyTooLarge(f"request body too large (max {MAX_JSON_BODY_BYTES} bytes)")
        return json.loads(self.rfile.read(length) or b"{}")

    def _post_generate(self) -> None:
        payload = self._read_json()
        text = (payload.get("text") or "").strip()
        fmt = payload.get("format", "webtoon")
        style_guide = (payload.get("style_guide") or "").strip() or None
        project_id = (payload.get("project_id") or "").strip() or None
        if not text:
            self._send_json({"error": "missing 'text'"}, status=400)
            return
        if not _providers_configured():
            self._send_json(
                {
                    "error": "Selected provider credentials are incomplete. "
                    "Check your environment and restart."
                },
                status=503,
            )
            return
        if fmt not in ("page", "webtoon"):
            fmt = "webtoon"
        if project_id:
            validate_project_id(project_id)
        job_id, pid = _start_job(text, fmt, style_guide, project_id=project_id)
        self._send_json({"job_id": job_id, "project_id": pid})

    def _post_stop(self) -> None:
        # /api/job/<id>/stop
        parts = self.path.strip("/").split("/")
        if len(parts) != 4:
            self._send_json({"error": "not found"}, status=404)
            return
        job_id = parts[2]
        ok, error = request_stop(job_id)
        if ok:
            self._send_json({"ok": True})
        elif error == "expired":
            self._send_json(
                {"error": "job expired (finished jobs are removed after TTL)"},
                status=410,
            )
        else:
            self._send_json({"error": "unknown job"}, status=404)

    def _post_experiment_compose(self) -> None:
        experiment_id = self.path[len("/api/experiment/") : -len("/compose")]
        body = self._read_json()
        merge_from = body.get("merge_from")
        components = body.get("components") or []
        if merge_from is not None and not isinstance(merge_from, str):
            self._send_json({"error": "merge_from must be a string"}, status=400)
            return
        if not isinstance(components, list):
            self._send_json({"error": "components must be a list"}, status=400)
            return
        try:
            record = compose_experiment_api(
                experiment_id, merge_from=merge_from, components=components
            )
        except FileExistsError as exc:
            self._send_json({"error": str(exc)}, status=409)
            return
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=400)
            return
        self._send_json(record)

    def _post_sequence_pick(self) -> None:
        sequence_id = self.path[len("/api/sequence/") : -len("/pick")]
        body = self._read_json()
        choice = str(body.get("choice", "")).strip()
        why = str(body.get("why", "")).strip()
        if not choice or not why:
            self._send_json({"error": "missing choice/why"}, status=400)
            return
        try:
            selection = pick_sequence_api(sequence_id, choice, why)
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=400)
            return
        except FileNotFoundError as exc:
            self._send_json({"error": str(exc)}, status=404)
            return
        self._send_json(selection)

    def _post_sequence_advance(self) -> None:
        sequence_id = self.path[len("/api/sequence/") : -len("/advance")]
        body = self._read_json()
        merge_from = body.get("merge_from")
        components = body.get("components") or []
        if merge_from is not None and not isinstance(merge_from, str):
            self._send_json({"error": "merge_from must be a string"}, status=400)
            return
        if not isinstance(components, list):
            self._send_json({"error": "components must be a list"}, status=400)
            return
        try:
            payload = asyncio.run(
                advance_sequence_api(
                    sequence_id,
                    merge_from=[merge_from] if merge_from else [],
                    components=[str(component) for component in components],
                )
            )
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=400)
            return
        except FileNotFoundError as exc:
            self._send_json({"error": str(exc)}, status=404)
            return
        except Exception as exc:  # noqa: BLE001 - provider failure is a request error
            self._send_json({"error": f"{type(exc).__name__}: {exc}"}, status=500)
            return
        self._send_json(payload)

    def _post_experiment_pick(self) -> None:
        experiment_id = self.path[len("/api/experiment/") : -len("/pick")]
        body = self._read_json()
        choice = str(body.get("choice", "")).strip()
        why = str(body.get("why", "")).strip()
        if not choice or not why:
            self._send_json({"error": "missing choice/why"}, status=400)
            return
        try:
            selection = pick_experiment_api(experiment_id, choice, why)
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=400)
            return
        except FileNotFoundError as exc:
            self._send_json({"error": str(exc)}, status=404)
            return
        self._send_json(selection)

    def _post_review(self) -> None:
        # /api/project/<id>/review
        parts = self.path.strip("/").split("/")
        if len(parts) != 4:
            self._send_json({"error": "not found"}, status=404)
            return
        project_id = parts[2]
        payload = self._read_json()
        action = (payload.get("action") or "").strip()
        new_name = (payload.get("new_name") or "").strip()
        candidate = (payload.get("candidate") or "").strip()
        if not new_name or not candidate:
            self._send_json({"error": "missing new_name/candidate"}, status=400)
            return
        result = apply_review(project_id, action, new_name, candidate)
        self._send_json({"project_id": project_id, **result})

    def _post_ledger_confirm(self) -> None:
        # /api/project/<id>/ledger/confirm
        parts = self.path.strip("/").split("/")
        if len(parts) != 5:
            self._send_json({"error": "not found"}, status=404)
            return
        project_id = parts[2]
        payload = self._read_json()
        character = (payload.get("character") or "").strip()
        pages = payload.get("pages")
        if not character:
            self._send_json({"error": "missing character"}, status=400)
            return
        if pages is not None and not isinstance(pages, list):
            self._send_json({"error": "pages must be a list"}, status=400)
            return
        if pages is not None:
            pages = [str(page) for page in pages if str(page).strip()]
        try:
            result = apply_ledger_confirm(project_id, character, pages)
        except KeyError as exc:
            self._send_json({"error": str(exc)}, status=404)
            return
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=400)
            return
        self._send_json({"project_id": project_id, **result})

    def _post_regen(self) -> None:
        parts = self.path.strip("/").split("/")
        if len(parts) != 4:
            self._send_json({"error": "not found"}, status=404)
            return
        project_id = parts[2]
        if not _providers_configured():
            self._send_json({"error": "provider credentials incomplete"}, status=503)
            return
        payload = self._read_json()
        fmt = payload.get("format", "webtoon")
        if fmt not in ("page", "webtoon"):
            fmt = "webtoon"
        style_guide = (payload.get("style_guide") or "").strip() or None
        keys = payload.get("keys") or []
        if not isinstance(keys, list):
            raise ValueError("keys must be a list")
        stale = bool(payload.get("stale"))
        job_id, pid = start_regen_job(
            project_id,
            stale=stale,
            keys=[str(k) for k in keys],
            fmt=fmt,
            style_guide=style_guide,
        )
        self._send_json({"job_id": job_id, "project_id": pid})


_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def main() -> None:
    _load_dotenv()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    if not os.environ.get("AGNES_API_KEY"):
        print("WARNING: AGNES_API_KEY is not set. Set it (or add to .env) before generating.")
    if HOST not in _LOOPBACK_HOSTS:
        print(
            f"WARNING: binding to {HOST!r} exposes this unauthenticated UI to the network — "
            "anyone who can reach this port can spend your API quota and read your projects.",
            file=sys.stderr,
        )
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Inkstone UI running at http://{HOST}:{PORT}  (Ctrl+C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")


if __name__ == "__main__":
    main()
