"""core.pipelines.run_until_complete — unattended supervisor around creative_comic.

One upload should keep making progress through free-tier timeouts / 503s until
the comic is finished or a wall-clock deadline pauses the run with state saved.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass

import requests

from core.pipelines.creative_comic import ComicProject, creative_comic
from core.schemas import ProjectState

logger = logging.getLogger(__name__)

_TRANSIENT_MARKERS = (
    "max retries",
    "last status 429",
    "last status 500",
    "last status 502",
    "last status 503",
    "last status 504",
    "service busy",
    "queue is full",
    "timed out",
    "timeout",
    "connection aborted",
    "connection reset",
    "temporarily unavailable",
    "comfyui is not reachable",
)


@dataclass
class PausedRun:
    """Supervisor stopped on wall-clock deadline; project progress is on disk."""

    project_id: str
    output_dir: str
    reason: str
    state: ProjectState | None = None
    elapsed_seconds: float = 0.0


def is_transient_error(exc: BaseException) -> bool:
    """Return True for free-tier / network failures worth auto-retrying."""
    if isinstance(exc, (requests.Timeout, requests.ConnectionError)):
        return True
    if isinstance(exc, RuntimeError):
        msg = str(exc).lower()
        if "project is already running" in msg:
            return False
        return any(marker in msg for marker in _TRANSIENT_MARKERS)
    # HTTPError from requests after raise_for_status (non-retryable path may still
    # surface 5xx if something bypassed retryable_post — treat 5xx/429 as transient).
    if isinstance(exc, requests.HTTPError):
        resp = getattr(exc, "response", None)
        code = getattr(resp, "status_code", None)
        return code in (429, 500, 502, 503, 504)
    return False


def _deadline_hours(override: float | None) -> float:
    if override is not None:
        return max(0.0, float(override))
    return max(0.0, float(os.environ.get("INKSTONE_RUN_DEADLINE_HOURS", "24")))


def _backoff_base(override: float | None) -> float:
    if override is not None:
        return max(0.1, float(override))
    return max(0.1, float(os.environ.get("INKSTONE_SUPERVISOR_BACKOFF_BASE", "30")))


def _backoff_cap(override: float | None) -> float:
    if override is not None:
        return max(0.1, float(override))
    return max(0.1, float(os.environ.get("INKSTONE_SUPERVISOR_BACKOFF_CAP", "300")))


def _supervisor_backoff(attempt: int, base: float, cap: float) -> float:
    """Exponential backoff capped for sleeps between full pipeline attempts."""
    delay = base * (2**attempt)
    return min(delay, cap)


def _load_state(output_dir: str) -> ProjectState | None:
    path = os.path.join(output_dir, "state.json")
    if not os.path.isfile(path):
        return None
    try:
        return ProjectState.load(path)
    except Exception:  # noqa: BLE001 — pause path must not crash on corrupt state
        logger.warning("could not load state.json from %s", output_dir)
        return None


async def run_until_complete(
    source_txt: str,
    *,
    output_dir: str,
    project_id: str | None = None,
    chat=None,
    image=None,
    style_guide: str | None = None,
    output_format: str = "page",
    progress_callback: Callable[[str, float | None], None] | None = None,
    panel_keys: list[str] | None = None,
    deadline_hours: float | None = None,
    backoff_base: float | None = None,
    backoff_cap: float | None = None,
) -> ComicProject | PausedRun:
    """Run ``creative_comic`` until success or wall-clock pause.

    Transient upstream failures trigger a backoff sleep and another attempt on
    the same ``output_dir`` (resume via ``state.json``). Permanent errors raise.
    """
    start = time.monotonic()
    deadline_s = _deadline_hours(deadline_hours) * 3600.0
    base = _backoff_base(backoff_base)
    cap = _backoff_cap(backoff_cap)
    attempt = 0
    pid = project_id or os.path.basename(os.path.abspath(output_dir)) or "comic"

    while True:
        elapsed = time.monotonic() - start
        if deadline_s > 0 and elapsed >= deadline_s:
            reason = (
                f"wall-clock deadline reached after {elapsed / 3600:.2f}h "
                f"(limit {_deadline_hours(deadline_hours):g}h); progress saved — "
                "resume with the same project_id"
            )
            logger.warning("%s", reason)
            return PausedRun(
                project_id=pid,
                output_dir=output_dir,
                reason=reason,
                state=_load_state(output_dir),
                elapsed_seconds=elapsed,
            )

        try:
            return await creative_comic(
                source_txt,
                output_dir=output_dir,
                project_id=project_id,
                chat=chat,
                image=image,
                style_guide=style_guide,
                output_format=output_format,
                progress_callback=progress_callback,
                panel_keys=panel_keys,
            )
        except Exception as exc:
            if not is_transient_error(exc):
                raise
            elapsed = time.monotonic() - start
            remaining = deadline_s - elapsed if deadline_s > 0 else float("inf")
            delay = _supervisor_backoff(attempt, base, cap)
            if deadline_s > 0 and remaining <= 0:
                reason = (
                    f"wall-clock deadline reached after transient error ({exc!s}); "
                    "progress saved — resume with the same project_id"
                )
                logger.warning("%s", reason)
                return PausedRun(
                    project_id=pid,
                    output_dir=output_dir,
                    reason=reason,
                    state=_load_state(output_dir),
                    elapsed_seconds=elapsed,
                )
            if deadline_s > 0:
                delay = min(delay, max(0.0, remaining))
            logger.warning(
                "transient failure (attempt %s): %s; sleeping %.1fs then resuming %s",
                attempt + 1,
                exc,
                delay,
                output_dir,
            )
            if progress_callback is not None:
                progress_callback("retry", None)
            await asyncio.sleep(delay)
            attempt += 1
