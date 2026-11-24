"""--no-lettering flag 解析与接线测试。"""

import sys

import pytest

from core.cli import _build_parser
from core.cli_generate import _parse_args as _parse_generate_args


def test_cli_generate_flag_parses_true():
    args = _build_parser().parse_args(["generate", "--no-lettering"])
    assert args.no_lettering is True


def test_cli_generate_flag_defaults_false():
    args = _build_parser().parse_args(["generate"])
    assert args.no_lettering is False


def test_cli_generate_entry_flag_parses_true():
    args = _parse_generate_args(["--no-lettering", "story.txt"])
    assert args.no_lettering is True


def test_cli_generate_entry_flag_defaults_false():
    args = _parse_generate_args(["story.txt"])
    assert args.no_lettering is False


def _watch_run_generate(monkeypatch) -> dict:
    """Capture generate kwargs with a no-op runner."""
    import core.cli_generate as cli_generate_module

    captured: dict = {}

    def record(*_args, **kwargs):
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(cli_generate_module, "run_generate", record)
    return captured


def test_cli_generate_handler_wiring_passes_no_lettering_flag(monkeypatch):
    """generate 处理器收到 --no-lettering 时必须传 per-run False。"""
    import core.cli as cli_module

    captured = _watch_run_generate(monkeypatch)
    args = _build_parser().parse_args(["generate", "--no-lettering"])
    with pytest.raises(SystemExit):
        cli_module._run_generate(args)
    assert captured["lettering_enabled"] is False


def test_cli_generate_handler_wiring_without_flag_does_not_touch_env(monkeypatch):
    """未传 flag 时处理器保留 None，让已有 env/config 生效。"""
    import core.cli as cli_module

    captured = _watch_run_generate(monkeypatch)
    args = _build_parser().parse_args(["generate"])
    with pytest.raises(SystemExit):
        cli_module._run_generate(args)
    assert captured["lettering_enabled"] is None


def test_cli_generate_main_wiring_passes_no_lettering_flag(monkeypatch):
    """独立入口 main() 的接线与统一入口行为一致。"""
    import core.cli_generate as cli_generate_module

    captured = _watch_run_generate(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["generate", "--no-lettering", "story.txt"])
    with pytest.raises(SystemExit):
        cli_generate_module.main()
    assert captured["lettering_enabled"] is False


def test_cli_generate_main_wiring_without_flag_does_not_touch_env(monkeypatch):
    """独立入口未传 flag 时同样不调用 set_lettering_env。"""
    import core.cli_generate as cli_generate_module

    captured = _watch_run_generate(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["generate", "story.txt"])
    with pytest.raises(SystemExit):
        cli_generate_module.main()
    assert captured["lettering_enabled"] is None
