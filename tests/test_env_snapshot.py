"""Guards for the h_env digest and its registry (design §5, §9 phase 0a).

Two properties are load-bearing. First, the registry must partition every
environment variable the app reads, so adding an output-affecting switch without
registering it fails a test. Second, the digest must follow *resolved* values
rather than config strings, and must ignore runtime-only knobs and credentials.
"""

import core.comic.fonts as fonts
from core.comic.env_snapshot import H_ENV_EXCLUDED, H_ENV_REGISTERED, h_env
from core.comic.fonts import resolved_font_digest
from core.config import ALL_ENV_VARS


def test_registry_partitions_every_declared_env_var():
    assert H_ENV_REGISTERED | H_ENV_EXCLUDED == set(ALL_ENV_VARS)
    assert H_ENV_REGISTERED & H_ENV_EXCLUDED == set()


def test_every_registered_var_is_read_by_config():
    """A registered name that nothing reads is a typo, not a registration."""
    assert H_ENV_REGISTERED <= set(ALL_ENV_VARS)


def test_h_env_follows_resolved_size_not_config_string(monkeypatch):
    """§8 invariant 6b: a size fallback must change the key."""
    monkeypatch.setenv("INKSTONE_PAGE_SIZE", "1024x1536")

    declared = h_env(text="plain")
    fallen_back = h_env(text="plain", resolved_size="1024x1024")

    assert declared != fallen_back


def test_h_env_ignores_runtime_only_switches(monkeypatch):
    """Concurrency, throttling and retries cannot change bytes."""
    baseline = h_env(text="plain")
    monkeypatch.setenv("INKSTONE_IMAGE_CONCURRENCY", "7")
    monkeypatch.setenv("AGNES_IMAGE_3K_RPM", "3")
    monkeypatch.setenv("AGNES_IMAGE_MAX_RETRIES", "1")
    monkeypatch.setenv("AGNES_IMAGE_RETRY_BASE_DELAY", "0.1")
    monkeypatch.setenv("INKSTONE_RUN_DEADLINE_HOURS", "2")
    monkeypatch.setenv("INKSTONE_WEBTOON_WARN_MB", "1")

    assert h_env(text="plain") == baseline


def test_h_env_changes_when_a_registered_switch_changes(monkeypatch):
    baseline = h_env(text="plain")
    monkeypatch.setenv("INKSTONE_PANEL_CONTINUITY", "0")

    assert h_env(text="plain") != baseline


def test_h_env_never_contains_credentials(monkeypatch):
    """Secrets must not influence the key, so rotating one cannot invalidate work."""
    monkeypatch.setenv("AGNES_API_KEY", "secret-a")
    first = h_env(text="plain")
    monkeypatch.setenv("AGNES_API_KEY", "secret-b")

    assert h_env(text="plain") == first


def test_font_path_string_does_not_enter_h_env(monkeypatch):
    """The path string is not identity; the resolved bytes are."""
    monkeypatch.setenv("INKSTONE_FONT_PATH", "/nonexistent/a.ttf")
    first = h_env(text="汉字")

    monkeypatch.setenv("INKSTONE_FONT_PATH", "/nonexistent/b.ttf")

    assert h_env(text="汉字") == first


def test_resolved_font_digest_hashes_bytes_not_path(tmp_path, monkeypatch):
    """Same call shape, different bytes behind it => different digest."""
    first_path = tmp_path / "first.ttf"
    first_path.write_bytes(b"synthetic-font-bytes-a")
    second_path = tmp_path / "second.ttf"
    second_path.write_bytes(b"synthetic-font-bytes-b")

    class _FakeFont:
        """Stand-in for a resolved FreeType font; the digest only reads the file."""

    monkeypatch.setattr(fonts, "_find_cjk_font", lambda size, path: (_FakeFont(), str(path)))

    first = resolved_font_digest("汉字", font_path=str(first_path))
    second = resolved_font_digest("汉字", font_path=str(second_path))

    assert first != second


def test_resolved_font_digest_is_stable_for_builtin(monkeypatch):
    """Non-CJK text uses Pillow's builtin font and has no file to hash."""
    monkeypatch.setattr(fonts, "_find_cjk_font", lambda size, path: None)

    assert resolved_font_digest("plain ascii") == resolved_font_digest("other ascii")
