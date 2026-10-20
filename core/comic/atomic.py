"""core.comic.atomic — atomic file replacement with sane permissions.

``tempfile.mkstemp`` creates a ``0600`` file, and ``os.replace`` preserves that
mode. A checkpoint that used to be created with the process umask therefore
became owner-only after its first atomic save, which breaks other local
workers that can write but cannot read it. This module:

- gives a new file the process umask (usually ``0644``);
- preserves the mode of an existing file;
- fsyncs the directory after the rename, so the rename is durable;
- closes the temp fd if ``os.fdopen`` fails.
"""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path


def _default_mode() -> int:
    current = os.umask(0)
    os.umask(current)
    return 0o666 & ~current


def _target_mode(path: Path) -> int:
    try:
        return stat.S_IMODE(path.stat().st_mode)
    except FileNotFoundError:
        return _default_mode()


def _fsync_directory(directory: Path) -> None:
    if os.name == "nt":  # pragma: no cover - directory fsync is POSIX-only
        return
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _atomic_write(path: str | Path, data: str | bytes) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    mode = _target_mode(target)
    fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        try:
            os.fchmod(fd, mode)
        except (AttributeError, OSError):  # pragma: no cover - Windows fallback
            os.chmod(temp_name, mode)
        text = isinstance(data, str)
        handle = os.fdopen(fd, "w" if text else "wb", encoding="utf-8" if text else None)
        fd = -1  # handle owns the descriptor now
        with handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, target)
        _fsync_directory(target.parent)
    except Exception:
        if fd >= 0:
            os.close(fd)
        Path(temp_name).unlink(missing_ok=True)
        raise


def atomic_write_text(path: str | Path, text: str) -> None:
    """Atomically replace ``path`` with UTF-8 ``text``."""
    _atomic_write(path, text)


def atomic_write_bytes(path: str | Path, data: bytes) -> None:
    """Atomically replace ``path`` with ``data``."""
    _atomic_write(path, data)
