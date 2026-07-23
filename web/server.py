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
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# Make the repo root importable so `core` resolves regardless of CWD.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.api import get_chat_provider, get_image_provider  # noqa: E402
from core.comic.identity import (  # noqa: E402
    dismiss_character_alias,
    force_regen_panels,
    merge_character_alias,
)
from core.pipelines.creative_comic import creative_comic  # noqa: E402
from core.schemas import ProjectState  # noqa: E402

OUTPUT_DIR = ROOT / "comic_out"
HOST = os.environ.get("INKSTONE_UI_HOST", "127.0.0.1")
PORT = int(os.environ.get("INKSTONE_UI_PORT", "8000"))

_PROJECT_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

# In-memory job registry. Each job writes under comic_out/<project_id>/.
JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()


def _load_dotenv() -> None:
    """Best-effort .env loader (no python-dotenv dependency)."""
    env_path = ROOT / ".env"
    if not env_path.exists() or os.environ.get("AGNES_API_KEY"):
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
        self._job["log"].append(record.getMessage())


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


def _state_snapshot(state: ProjectState) -> dict:
    return {
        "skipped": list(state.skipped),
        "skipped_chunks": list(state.skipped_chunks),
        "needs_review": [s.model_dump() for s in state.needs_review],
        "stale_panels": list(state.stale_panels),
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
    job.update(_state_snapshot(proj.state))
    job["project_id"] = proj.project_id


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
    try:
        out_dir = _project_dir(project_id)
        proj = asyncio.run(
            creative_comic(
                text,
                output_dir=str(out_dir),
                project_id=project_id,
                output_format=fmt,
                style_guide=style_guide,
                panel_keys=panel_keys,
            )
        )
        _fill_job_from_project(job, proj)
        job["status"] = "done"
    except Exception as exc:  # noqa: BLE001 — surface any failure to the UI
        job["status"] = "error"
        job["error"] = str(exc)
        job["log"].append(f"ERROR: {exc}")
    finally:
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
    with JOBS_LOCK:
        JOBS[job_id] = {
            "status": "running",
            "log": [],
            "panels": [],
            "webtoon": None,
            "pdf": None,
            "skipped": [],
            "skipped_chunks": [],
            "needs_review": [],
            "stale_panels": [],
            "project_id": pid,
            "error": None,
        }
    threading.Thread(
        target=_run_job,
        args=(job_id, text, fmt, style_guide, pid, panel_keys),
        daemon=True,
    ).start()
    return job_id, pid


def _load_project_state(project_id: str) -> tuple[Path, ProjectState]:
    out_dir = _project_dir(project_id)
    state_path = out_dir / "state.json"
    if not state_path.is_file():
        raise FileNotFoundError(f"no state.json for project {project_id}")
    return out_dir, ProjectState.load(state_path)


def apply_review(project_id: str, action: str, new_name: str, candidate: str) -> dict:
    """Merge or dismiss an alias suggestion; persist state.json."""
    out_dir, state = _load_project_state(project_id)
    if action == "merge":
        merge_character_alias(state, new_name, candidate)
    elif action == "dismiss":
        dismiss_character_alias(state, new_name, candidate)
    else:
        raise ValueError("action must be 'merge' or 'dismiss'")
    state.save(out_dir / "state.json")
    return _state_snapshot(state)


def start_regen_job(
    project_id: str,
    *,
    stale: bool = False,
    keys: list[str] | None = None,
    fmt: str = "webtoon",
    style_guide: str | None = None,
) -> tuple[str, str]:
    """Force-regen stale/selected panels and start a background job."""
    out_dir, state = _load_project_state(project_id)
    source_path = out_dir / "source.txt"
    if not source_path.is_file():
        raise FileNotFoundError("project has no source.txt; regenerate from scratch")
    text = source_path.read_text(encoding="utf-8")
    target_keys = list(keys or [])
    if stale:
        target_keys = list(dict.fromkeys([*target_keys, *state.stale_panels]))
    if not target_keys:
        raise ValueError("no panel keys to regenerate")
    force_regen_panels(state, target_keys)
    state.save(out_dir / "state.json")
    return _start_job(text, fmt, style_guide, project_id=project_id, panel_keys=target_keys)


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

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length) or b"{}")

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
            if target.is_file() and _is_within(target, OUTPUT_DIR):
                self._send_file(target)
            else:
                self._send_json({"error": "not found"}, status=404)
            return
        if self.path.startswith("/api/job/"):
            job_id = self.path.split("/")[-1]
            with JOBS_LOCK:
                job = JOBS.get(job_id)
            if job is None:
                self._send_json({"error": "unknown job"}, status=404)
            else:
                self._send_json(
                    {
                        "status": job["status"],
                        "log": job["log"][-200:],
                        "panels": job["panels"],
                        "webtoon": job["webtoon"],
                        "pdf": job["pdf"],
                        "skipped": job.get("skipped", []),
                        "skipped_chunks": job.get("skipped_chunks", []),
                        "needs_review": job.get("needs_review", []),
                        "stale_panels": job.get("stale_panels", []),
                        "project_id": job.get("project_id"),
                        "error": job["error"],
                    }
                )
            return
        self._send_json({"error": "not found"}, status=404)

    def do_POST(self) -> None:
        try:
            if self.path == "/api/generate":
                self._post_generate()
                return
            if self.path.startswith("/api/project/") and self.path.endswith("/review"):
                self._post_review()
                return
            if self.path.startswith("/api/project/") and self.path.endswith("/regen"):
                self._post_regen()
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


def main() -> None:
    _load_dotenv()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    if not os.environ.get("AGNES_API_KEY"):
        print("WARNING: AGNES_API_KEY is not set. Set it (or add to .env) before generating.")
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Inkstone UI running at http://{HOST}:{PORT}  (Ctrl+C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")


if __name__ == "__main__":
    main()
