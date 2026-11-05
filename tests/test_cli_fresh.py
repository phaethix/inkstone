"""tests/test_cli_fresh.py — ``--fresh`` 强制重跑的回归约束。"""

import argparse
from pathlib import Path

import pytest

from core import cli, cli_generate


def _args(*argv: str):
    return cli._build_parser().parse_args(list(argv))


def test_generate_fresh_defaults_off():
    assert _args("generate", "book.txt").fresh is False
    assert _args("generate", "book.txt", "--fresh").fresh is True


def test_reset_out_dir_removes_existing_state_and_artifacts(tmp_path):
    out = tmp_path / "comic_out"
    out.mkdir()
    (out / "state.json").write_text("{}", encoding="utf-8")
    (out / "comic.pdf").write_bytes(b"pdf")
    pages = out / "pages"
    pages.mkdir()
    (pages / "page_c0000_p0000.png").write_bytes(b"png")
    keep = out / "keep.txt"
    keep.write_text("gone", encoding="utf-8")

    cli_generate._reset_out_dir(out)

    assert out.is_dir()  # 目录本身保留，pipeline 随后重建
    assert not (out / "state.json").exists()
    assert not (out / "comic.pdf").exists()
    assert not (out / "keep.txt").exists()
    assert not pages.exists()


def test_reset_out_dir_on_missing_dir_is_noop(tmp_path):
    out = tmp_path / "not_created_yet"
    cli_generate._reset_out_dir(out)  # 不抛错
    assert not out.exists()


def test_reset_out_dir_refuses_unsafe_paths():
    for unsafe in (Path("."), Path("/"), Path("~").expanduser()):
        with pytest.raises(ValueError):
            cli_generate._reset_out_dir(unsafe)


def test_run_generate_calls_reset_when_fresh(tmp_path, monkeypatch):
    calls: list[Path] = []
    monkeypatch.setattr(cli_generate, "_reset_out_dir", lambda p: calls.append(Path(p)))
    monkeypatch.setattr(cli_generate, "_providers_configured", lambda: True)
    monkeypatch.setattr(cli_generate.asyncio, "run", lambda coro: coro.close())
    monkeypatch.setattr(cli_generate, "_configure_background_logging", lambda: None)
    source = tmp_path / "book.txt"
    source.write_text("text", encoding="utf-8")

    code = cli_generate.run_generate(
        source=str(source),
        out=str(tmp_path / "comic_out"),
        fmt="page",
        project_id=None,
        fresh=True,
    )

    assert code == 0
    assert calls == [tmp_path / "comic_out"]


def test_run_generate_skips_reset_when_not_fresh(tmp_path, monkeypatch):
    calls: list[Path] = []
    monkeypatch.setattr(cli_generate, "_reset_out_dir", lambda p: calls.append(Path(p)))
    monkeypatch.setattr(cli_generate, "_providers_configured", lambda: True)
    monkeypatch.setattr(cli_generate.asyncio, "run", lambda coro: coro.close())
    monkeypatch.setattr(cli_generate, "_configure_background_logging", lambda: None)
    source = tmp_path / "book.txt"
    source.write_text("text", encoding="utf-8")

    code = cli_generate.run_generate(
        source=str(source),
        out=str(tmp_path / "comic_out"),
        fmt="page",
        project_id=None,
        fresh=False,
    )

    assert code == 0
    assert calls == []


def test_cli_generate_dispatches_fresh_flag(monkeypatch, tmp_path):
    seen: dict[str, object] = {}

    def fake_run_generate(**kwargs):
        seen.update(kwargs)
        return 0

    monkeypatch.setattr("core.cli_generate.run_generate", fake_run_generate)
    args = argparse.Namespace(
        book="book.txt",
        source=None,
        out=str(tmp_path / "comic_out"),
        format="page",
        project=None,
        budget=None,
        carry_over=False,
        yes=False,
        density=None,
        harness_dir=None,
        fresh=True,
    )
    with pytest.raises(SystemExit) as exc_info:
        cli._run_generate(args)
    assert exc_info.value.code == 0
    assert seen.get("fresh") is True
