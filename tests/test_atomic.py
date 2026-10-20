"""Atomic writes keep a readable mode, replace content, and fsync the directory."""

import os
import stat

from core.comic import atomic


def _umask() -> int:
    current = os.umask(0)
    os.umask(current)
    return current


def test_a_new_file_gets_the_process_umask_mode(tmp_path):
    target = tmp_path / "state.json"
    expected = 0o666 & ~_umask()

    atomic.atomic_write_text(target, "new")

    assert target.read_text(encoding="utf-8") == "new"
    assert stat.S_IMODE(target.stat().st_mode) == expected


def test_an_existing_mode_is_preserved(tmp_path):
    target = tmp_path / "state.json"
    target.write_text("old", encoding="utf-8")
    target.chmod(0o640)

    atomic.atomic_write_text(target, "new")

    assert stat.S_IMODE(target.stat().st_mode) == 0o640
    assert target.read_text(encoding="utf-8") == "new"


def test_bytes_and_text_leave_no_temp_files(tmp_path):
    text_path = tmp_path / "a.json"
    bytes_path = tmp_path / "b.bin"
    atomic.atomic_write_text(text_path, "text")
    atomic.atomic_write_bytes(bytes_path, b"bytes")
    assert text_path.read_text(encoding="utf-8") == "text"
    assert bytes_path.read_bytes() == b"bytes"
    assert not list(tmp_path.glob(".*"))


def test_the_directory_is_fsynced_after_replace(tmp_path, monkeypatch):
    calls: list = []
    monkeypatch.setattr(atomic, "_fsync_directory", lambda directory: calls.append(directory))
    target = tmp_path / "state.json"
    atomic.atomic_write_text(target, "x")
    assert calls == [tmp_path]
