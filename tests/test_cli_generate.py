"""tests/test_cli_generate.py — generate command ownership (no network, no API key).

Regression guard for the packaging bug: the ``inkstone generate`` entry used to
forward into ``examples/generate_comic.py``, which is not shipped in the wheel,
so any non-editable install broke the official CLI. The implementation now
lives in core and must be importable and input-validating without examples/.
"""

import subprocess
import sys
from pathlib import Path

import pytest

from core import cli, cli_generate

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_run_generate_requires_provider_credentials(monkeypatch, capsys):
    monkeypatch.delenv("AGNES_API_KEY", raising=False)
    monkeypatch.delenv("PROVIDER", raising=False)
    code = cli_generate.run_generate(
        source=str(REPO_ROOT / "examples" / "scene1.txt"),
        out=None,
        fmt="page",
        project_id=None,
    )
    assert code == 1
    assert "AGNES_API_KEY" in capsys.readouterr().err


def test_run_generate_accepts_openai_compat_without_agnes_key(monkeypatch, capsys):
    monkeypatch.delenv("AGNES_API_KEY", raising=False)
    monkeypatch.setenv("PROVIDER", "openai_compat")
    monkeypatch.setenv("OPENAI_COMPAT_BASE_URL", "https://images.example/v1")
    monkeypatch.setenv("OPENAI_COMPAT_API_KEY", "image-key")
    monkeypatch.setenv("OPENAI_COMPAT_CHAT_BASE_URL", "https://chat.example/v1")
    monkeypatch.setenv("OPENAI_COMPAT_CHAT_API_KEY", "chat-key")
    monkeypatch.setattr(
        cli_generate.asyncio,
        "run",
        lambda coro: coro.close(),
    )
    code = cli_generate.run_generate(
        source=str(REPO_ROOT / "examples" / "scene1.txt"),
        out=str(REPO_ROOT / "comic_out" / "cli_test"),
        fmt="page",
        project_id="cli_test",
    )
    assert code == 0
    assert "AGNES_API_KEY" not in capsys.readouterr().err


def test_run_generate_missing_source(monkeypatch, capsys):
    monkeypatch.setenv("AGNES_API_KEY", "sk-test")
    code = cli_generate.run_generate(
        source="/nonexistent/novel.txt", out=None, fmt="page", project_id=None
    )
    assert code == 1
    assert "source file not found" in capsys.readouterr().err


def test_run_generate_requires_source_when_no_bundled_scene(monkeypatch, capsys):
    monkeypatch.setenv("AGNES_API_KEY", "sk-test")
    monkeypatch.setattr(cli_generate, "_default_scene", lambda: None)
    code = cli_generate.run_generate(source=None, out=None, fmt="page", project_id=None)
    assert code == 1
    assert "source file is required" in capsys.readouterr().err


def test_default_scene_found_in_repo_checkout():
    scene = cli_generate._default_scene()
    assert scene is not None
    assert scene.name == "scene1.txt"
    assert scene.exists()


def test_cli_generate_help_works_without_examples_package(monkeypatch):
    # The CLI dispatch must not import examples/ at all.
    monkeypatch.setattr(sys, "argv", ["inkstone", "generate", "--help"])
    with pytest.raises(SystemExit) as exc_info:
        cli.main()
    assert exc_info.value.code == 0


def test_examples_wrapper_help_still_works():
    # Backward compat: running the demo script directly must keep working.
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "examples" / "generate_comic.py"), "--help"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0
    assert "--format" in result.stdout


def test_examples_wrapper_no_longer_owns_implementation():
    source = (REPO_ROOT / "examples" / "generate_comic.py").read_text(encoding="utf-8")
    assert "run_until_complete" not in source
    assert "core.cli_generate" in source
