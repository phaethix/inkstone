"""D-17: the resolved font digest is cached by path + mtime + size."""

import os

from core.comic import fonts


def test_resolved_font_digest_reuses_the_hash_for_an_unchanged_file(tmp_path, monkeypatch):
    font_file = tmp_path / "font.ttc"
    font_file.write_bytes(b"font-bytes-v1")
    monkeypatch.setattr(
        fonts, "_find_cjk_font", lambda size, font_path: (object(), f"font_path:{font_file}")
    )
    calls = {"n": 0}
    original_hash = fonts._hash_font_file

    def counted(source: str) -> str:
        calls["n"] += 1
        return original_hash(source)

    monkeypatch.setattr(fonts, "_hash_font_file", counted)
    fonts.reset_caches()
    try:
        first = fonts.resolved_font_digest("汉")
        second = fonts.resolved_font_digest("汉")
    finally:
        fonts.reset_caches()
    assert first == second
    assert calls["n"] == 1


def test_resolved_font_digest_rehashes_when_the_file_changes(tmp_path, monkeypatch):
    font_file = tmp_path / "font.ttc"
    font_file.write_bytes(b"font-bytes-v1")
    monkeypatch.setattr(
        fonts, "_find_cjk_font", lambda size, font_path: (object(), f"font_path:{font_file}")
    )
    fonts.reset_caches()
    try:
        first = fonts.resolved_font_digest("汉")
        stat = font_file.stat()
        font_file.write_bytes(b"font-bytes-v2")
        os.utime(font_file, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
        second = fonts.resolved_font_digest("汉")
    finally:
        fonts.reset_caches()
    assert first != second


def test_non_cjk_text_does_not_touch_the_font_file(monkeypatch):
    def explode(*args, **kwargs):
        raise AssertionError("font lookup must not run for Latin-only text")

    monkeypatch.setattr(fonts, "_hash_font_file", explode)
    assert fonts.resolved_font_digest("hello") == "builtin"
