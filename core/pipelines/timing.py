"""Progress timing helpers (elapsed / ETA)."""

from __future__ import annotations

MIN_PROGRESS_FOR_ETA = 0.05


def estimate_remaining(elapsed_seconds: float, progress: float) -> float | None:
    """Linear ETA remaining from cumulative elapsed and progress fraction."""
    if progress >= 1.0:
        return 0.0
    if progress < MIN_PROGRESS_FOR_ETA:
        return None
    if elapsed_seconds < 0:
        elapsed_seconds = 0.0
    return float(elapsed_seconds) * (1.0 - float(progress)) / float(progress)
