"""core.comic.locks — cross-process file locks for shared mutable state.

``budget.json`` and ``gate.json`` live in the shared data directory and are
read-modified-written by more than one project. A process project lock does not
protect them, so each writer takes an exclusive lock on the file's directory
instead. The lock file is a sibling (``.budget.lock`` / ``.gate.lock``); the
data files themselves are never locked in place, because an atomic replace would
invalidate the lock.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - exercised only on Windows
    fcntl = None

try:
    import msvcrt
except ImportError:  # pragma: no cover - exercised only outside Windows
    msvcrt = None


@contextmanager
def exclusive_lock(
    path: str | Path,
    *,
    blocking: bool = True,
    busy_message: str | None = None,
):
    """Hold an exclusive inter-process lock for the duration of the block.

    ``blocking=False`` raises ``RuntimeError`` instead of waiting, matching the
    existing project lock's behaviour. The lock file is created when absent.
    """
    lock_path = Path(path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as handle:
        if fcntl is not None:
            flags = fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB
            try:
                fcntl.flock(handle.fileno(), flags)
            except BlockingIOError as exc:
                raise RuntimeError(busy_message or f"lock is busy: {lock_path}") from exc
        elif msvcrt is not None:  # pragma: no cover - exercised only on Windows
            handle.seek(0)
            handle.write("0")
            handle.flush()
            handle.seek(0)
            mode = msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK
            try:
                msvcrt.locking(handle.fileno(), mode, 1)
            except OSError as exc:
                raise RuntimeError(busy_message or f"lock is busy: {lock_path}") from exc
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            elif msvcrt is not None:  # pragma: no cover - exercised only on Windows
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
