"""core.pipelines.cancel — cooperative cancellation primitive for pipelines.

Long-running pipeline runs poll a caller-supplied ``cancel_check`` at defined
checkpoints so a user-requested stop takes effect promptly without tearing
down state mid-write.
"""

from __future__ import annotations

from collections.abc import Callable


class PipelineCancelled(Exception):
    """Cooperative user stop — progress should already be on disk."""

    def __init__(self, reason: str = "stopped by user") -> None:
        self.reason = reason
        super().__init__(reason)


def check_cancel(cancel_check: Callable[[], bool] | None) -> None:
    """Raise ``PipelineCancelled`` when ``cancel_check`` reports a stop request."""
    if cancel_check is not None and cancel_check():
        raise PipelineCancelled()
