"""core.api.error_collector — API failure collector (persists to logs/*.jsonl).

Lightweight, dependency-free. Each failure is appended as one JSON object per
line to a single JSONL log (``logs/<project>.jsonl``), so a long run
produces one aggregatable file instead of hundreds of loose ``.json`` files.
Writes are serialized with a module-level lock and pushed off the event loop by
callers via ``asyncio.to_thread``.

Called only when an Agnes call fails; its own exceptions never break the main
flow.
"""

import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

from core.config import error_log_name

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_DEFAULT_LOG = "logs/errors.jsonl"


def _log_path() -> Path:
    # Allow a project-scoped override via env; default to a single shared JSONL.
    name = error_log_name()
    p = Path(name) if name else Path(_DEFAULT_LOG)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _extract_http_error(exc: Exception):
    """Extract (status_code, body, enhanced_message) from a requests.HTTPError."""
    if isinstance(exc, requests.exceptions.HTTPError):
        resp = getattr(exc, "response", None)
        if resp is not None:
            body = resp.text or ""
            msg = str(exc)
            try:
                data = json.loads(body)
                err = data.get("error", {})
                api_msg = err.get("message", "") if isinstance(err, dict) else ""
                if api_msg:
                    msg = f"{api_msg} (HTTP {resp.status_code})"
            except (json.JSONDecodeError, ValueError):
                pass
            return resp.status_code, body, msg
    return None, "", str(exc)


def collect_error(
    model_type: str,
    api_method: str,
    prompt: str = "",
    error_type: str = "",
    error_message: str = "",
    status_code: int | None = None,
    response_body: str = "",
    retry_count: int = 0,
    **_: Any,
) -> str | None:
    """Append a single API-call error as one JSON line to the JSONL error log.

    Returns the file path, or ``None`` if the collector itself fails.
    """
    try:
        now = datetime.now()
        record = {
            "timestamp": now.isoformat(),
            "model_type": model_type,
            "api_method": api_method,
            "prompt": (prompt or "")[:5000],
            "error_type": error_type,
            "error_message": (error_message or "")[:3000],
            "status_code": status_code,
            "response_body": (response_body or "")[:5000],
            "retry_count": retry_count,
        }
        line = json.dumps(record, ensure_ascii=False)
        path = _log_path()
        with _lock, open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        logger.info(f"[ErrorCollector] appended -> {path}")
        return str(path)
    except OSError as e:  # Filesystem errors during writing
        logger.error(f"[ErrorCollector] OS error when writing log: {e}")
        return None
    except (ValueError, TypeError, json.JSONEncodeError) as e:  # Data serialization issues
        logger.error(f"[ErrorCollector] serialization error: {e}")
        return None
    except Exception as e:  # Catch-all safety net; cannot fail entirely
        logger.error(f"[ErrorCollector] unexpected failure: {e}")
        return None


def collect_error_from_exception(
    model_type: str,
    api_method: str,
    exc: Exception,
    prompt: str = "",
    status_code: int | None = None,
    response_body: str = "",
    retry_count: int = 0,
    **_: Any,
) -> str | None:
    """Auto-extract error_type / message from an exception object, then call collect_error."""
    error_type = type(exc).__name__
    sc, body, msg = _extract_http_error(exc)
    if status_code is None:
        status_code = sc
    if not response_body:
        response_body = body
    return collect_error(
        model_type=model_type,
        api_method=api_method,
        prompt=prompt,
        error_type=error_type,
        error_message=msg,
        status_code=status_code,
        response_body=response_body,
        retry_count=retry_count,
    )
