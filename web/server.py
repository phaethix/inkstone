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
    POST /api/generate            -> body {"text", "format"} -> {"job_id"}
    GET  /api/job/<job_id>        -> {"status", "log", "panels", "webtoon", "pdf"}
    GET  /files/<path>            -> serves generated artifacts (path-safe)

Generation runs in a background thread (it makes real upstream API calls and
can take minutes), so the UI polls ``/api/job/<id>`` until status is "done".
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# Make the repo root importable so `core` resolves regardless of CWD.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.pipelines.creative_comic import creative_comic  # noqa: E402

OUTPUT_DIR = ROOT / "comic_out"
HOST = os.environ.get("INKSTONE_UI_HOST", "127.0.0.1")
PORT = int(os.environ.get("INKSTONE_UI_PORT", "8000"))

# In-memory job registry (single local user; latest job wins the output dir).
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


def _run_job(job_id: str, text: str, fmt: str) -> None:
    job = JOBS[job_id]
    handler = _JobLogHandler(job)
    handler.setLevel(logging.INFO)
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        proj = asyncio.run(creative_comic(text, output_dir=str(OUTPUT_DIR), output_format=fmt))
        job["panels"] = [
            {"id": pid, "url": f"/files/panels/{pid}.png"} for pid in proj.state.panels_done
        ]
        job["webtoon"] = "/files/pages/webtoon.png" if proj.webtoon else None
        job["pdf"] = "/files/comic.pdf" if proj.pdf else None
        job["skipped"] = list(proj.state.skipped)
        job["status"] = "done"
    except Exception as exc:  # noqa: BLE001 — surface any failure to the UI
        job["status"] = "error"
        job["error"] = str(exc)
        job["log"].append(f"ERROR: {exc}")
    finally:
        root.removeHandler(handler)


def _start_job(text: str, fmt: str) -> str:
    job_id = uuid.uuid4().hex[:12]
    with JOBS_LOCK:
        JOBS[job_id] = {
            "status": "running",
            "log": [],
            "panels": [],
            "webtoon": None,
            "pdf": None,
            "skipped": [],
            "error": None,
        }
    threading.Thread(target=_run_job, args=(job_id, text, fmt), daemon=True).start()
    return job_id


class Handler(BaseHTTPRequestHandler):
    server_version = "InkstoneUI/0.1"

    def _send_json(self, obj, status: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, status: int = 200) -> None:
        data = path.read_bytes()
        self.send_response(status)
        mime = "text/html" if path.suffix == ".html" else "image/png"
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
            # Lets the SPA distinguish live (backend present) from demo (static Pages).
            self._send_json({"ok": True, "key": bool(os.environ.get("AGNES_API_KEY"))})
            return
        if self.path.startswith("/assets/"):
            # Serve repo-root assets (logo, sample panels) so the same SPA works
            # both locally and on GitHub Pages with relative paths.
            rel = self.path[len("/assets/") :]
            target = (ROOT / "assets" / rel).resolve()
            if target.is_file() and str(target).startswith(str((ROOT / "assets").resolve())):
                self._send_file(target)
            else:
                self._send_json({"error": "not found"}, status=404)
            return
        if self.path.startswith("/files/"):
            rel = self.path[len("/files/") :]
            target = (OUTPUT_DIR / rel).resolve()
            if target.is_file() and str(target).startswith(str(OUTPUT_DIR.resolve())):
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
                        "skipped": job["skipped"],
                        "error": job["error"],
                    }
                )
            return
        self._send_json({"error": "not found"}, status=404)

    def do_POST(self) -> None:
        if self.path != "/api/generate":
            self._send_json({"error": "not found"}, status=404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self._send_json({"error": "invalid JSON"}, status=400)
            return
        text = (payload.get("text") or "").strip()
        fmt = payload.get("format", "webtoon")
        if not text:
            self._send_json({"error": "missing 'text'"}, status=400)
            return
        if not os.environ.get("AGNES_API_KEY"):
            self._send_json(
                {"error": "AGNES_API_KEY is not set. Export it (or put it in .env) and restart."},
                status=503,
            )
            return
        if fmt not in ("page", "webtoon"):
            fmt = "webtoon"
        job_id = _start_job(text, fmt)
        self._send_json({"job_id": job_id})


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
