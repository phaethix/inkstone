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


def _watch_set_lettering_env(monkeypatch) -> list[bool]:
    """Record set_lettering_env calls; the CLI wiring imports it at call time."""
    import core.config as config_module

    seen: list[bool] = []
    monkeypatch.setattr(config_module, "set_lettering_env", seen.append)
    return seen


def test_cli_generate_handler_wiring_passes_no_lettering_flag(monkeypatch):
    """generate 处理器收到 --no-lettering 时必须调用 set_lettering_env(True)。"""
    import core.cli as cli_module
    import core.cli_generate as cli_generate_module

    seen = _watch_set_lettering_env(monkeypatch)
    args = _build_parser().parse_args(["generate", "--no-lettering"])
    monkeypatch.setattr(
        cli_generate_module,
        "run_generate",
        lambda *a, **k: 0,
    )
    with pytest.raises(SystemExit):
        cli_module._run_generate(args)
    assert seen == [True]


def test_cli_generate_handler_wiring_without_flag_does_not_touch_env(monkeypatch):
    """未传 flag 时处理器不调用 set_lettering_env，用户 shell 预设的 env 保持原样。"""
    import core.cli as cli_module
    import core.cli_generate as cli_generate_module

    seen = _watch_set_lettering_env(monkeypatch)
    args = _build_parser().parse_args(["generate"])
    monkeypatch.setattr(
        cli_generate_module,
        "run_generate",
        lambda *a, **k: 0,
    )
    with pytest.raises(SystemExit):
        cli_module._run_generate(args)
    assert seen == []


def test_cli_generate_main_wiring_passes_no_lettering_flag(monkeypatch):
    """独立入口 main() 的接线与统一入口行为一致。"""
    import core.cli_generate as cli_generate_module

    seen = _watch_set_lettering_env(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["generate", "--no-lettering", "story.txt"])
    monkeypatch.setattr(
        cli_generate_module,
        "run_generate",
        lambda *a, **k: 0,
    )
    with pytest.raises(SystemExit):
        cli_generate_module.main()
    assert seen == [True]


def test_cli_generate_main_wiring_without_flag_does_not_touch_env(monkeypatch):
    """独立入口未传 flag 时同样不调用 set_lettering_env。"""
    import core.cli_generate as cli_generate_module

    seen = _watch_set_lettering_env(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["generate", "story.txt"])
    monkeypatch.setattr(
        cli_generate_module,
        "run_generate",
        lambda *a, **k: 0,
    )
    with pytest.raises(SystemExit):
        cli_generate_module.main()
    assert seen == []
