import re
from pathlib import Path

import core.config


def test_render_mode_defaults_finished_page(monkeypatch):
    monkeypatch.delenv("INKSTONE_RENDER_MODE", raising=False)
    from core.config import render_mode

    assert render_mode() == "finished_page"


def test_render_mode_panel_compose(monkeypatch):
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "panel_compose")
    from core.config import render_mode

    assert render_mode() == "panel_compose"


def test_finished_page_size_default(monkeypatch):
    monkeypatch.delenv("INKSTONE_PAGE_SIZE", raising=False)
    from core.config import finished_page_size

    assert finished_page_size() == "1024x1536"


# Every shape that reads an environment variable by bare string literal inside
# core/config.py. A literal is invisible to ALL_ENV_VARS, so the h_env registry
# would silently miss it; tests/test_env_snapshot.py asserts the registry
# partitions ALL_ENV_VARS exactly, which is only meaningful if no literal hides.
_ENV_READ_PATTERNS = (
    re.compile(r'_get\(\s*"([A-Za-z0-9_]+)"'),
    re.compile(r'env_int\(\s*"([A-Za-z0-9_]+)"'),
    re.compile(r'env_float\(\s*"([A-Za-z0-9_]+)"'),
    re.compile(r'env_bool\(\s*"([A-Za-z0-9_]+)"'),
    re.compile(r'os\.environ\.get\(\s*"([A-Za-z0-9_]+)"'),
)


def _env_names_read_as_string_literals() -> set[str]:
    """Env var names reached through a bare string literal, if any remain."""
    source = Path(core.config.__file__).read_text(encoding="utf-8")
    found: set[str] = set()
    for pattern in _ENV_READ_PATTERNS:
        found.update(pattern.findall(source))
    return found


def test_config_reads_env_names_through_constants_only():
    """A bare literal is invisible to the registry, so it must not exist."""
    literals = _env_names_read_as_string_literals()
    assert literals == set(), (
        "core/config.py reads env vars by string literal; declare an ENV_* "
        f"constant and reference it instead: {sorted(literals)}"
    )


def test_all_env_vars_covers_every_declared_constant():
    declared = {
        value
        for name, value in vars(core.config).items()
        if name.startswith("ENV_") and isinstance(value, str)
    }
    assert declared, "no ENV_* constants found; the registry test would pass vacuously"
    assert set(core.config.ALL_ENV_VARS) == declared


def test_harness_enabled_defaults_true_and_can_be_killed(monkeypatch):
    from core.config import harness_enabled, lessons_enabled

    monkeypatch.delenv("INKSTONE_HARNESS", raising=False)
    monkeypatch.delenv("INKSTONE_LESSONS", raising=False)
    assert harness_enabled() is True
    assert lessons_enabled() is True

    monkeypatch.setenv("INKSTONE_HARNESS", "0")
    assert harness_enabled() is False
    assert lessons_enabled() is False


def test_agnes_base_url_defaults_to_the_documented_host(monkeypatch):
    from core.config import ENV_AGNES_BASE_URL, agnes_base_url

    monkeypatch.delenv(ENV_AGNES_BASE_URL, raising=False)
    assert agnes_base_url() == "https://apihub.agnes-ai.com/v1"
    monkeypatch.setenv(ENV_AGNES_BASE_URL, "https://apihub.agnes-ai.cn/v1/")
    assert agnes_base_url() == "https://apihub.agnes-ai.cn/v1"


def test_pdf_batch_size_is_registered_and_parsed(monkeypatch):
    from core.config import ALL_ENV_VARS, ENV_PDF_BATCH, pdf_batch_size

    assert ENV_PDF_BATCH in ALL_ENV_VARS
    monkeypatch.delenv(ENV_PDF_BATCH, raising=False)
    assert pdf_batch_size() == 8
    monkeypatch.setenv(ENV_PDF_BATCH, "3")
    assert pdf_batch_size() == 3
    monkeypatch.setenv(ENV_PDF_BATCH, "0")
    assert pdf_batch_size() == 1
    monkeypatch.setenv(ENV_PDF_BATCH, "nope")
    assert pdf_batch_size() == 8
