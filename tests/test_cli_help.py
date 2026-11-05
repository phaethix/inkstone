"""tests/test_cli_help.py — 顶层 ``--help`` 用法手册的回归约束。"""

import sys

import pytest

from core import cli


def _top_level_help(monkeypatch, capsys) -> str:
    monkeypatch.setattr(sys, "argv", ["inkstone", "--help"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 0
    return capsys.readouterr().out


def test_top_level_help_is_a_full_usage_guide(monkeypatch, capsys):
    """--help 必须把常用命令与 flag 一次讲清，且输出是纯文本、可机读。"""
    out = _top_level_help(monkeypatch, capsys)
    for token in (
        "uv run inkstone",
        "generate",
        "plan",
        "web",
        "--book",
        "--out",
        "--project",
        "--fresh",
        "--format",
        "--density",
        "--budget",
        "--chat-model",
        "--image-model",
        "--provider",
        "--base-url",
        "NO_COLOR",
        "FORCE_COLOR",
        "--help",
    ):
        assert token in out, f"--help is missing usage token: {token}"
    assert "\x1b[" not in out  # 无 ANSI，机器可读子命令的 stdout 不被污染


def test_top_level_help_omits_stale_gradient_wording(monkeypatch, capsys):
    """颜色说明与现行单色细线 banner 一致，不残留旧『渐变面板』描述。"""
    out = _top_level_help(monkeypatch, capsys)
    assert "gradient panel" not in out
    assert "single-color" in out


def test_subcommand_help_is_not_polluted_by_top_level_epilog(monkeypatch, capsys):
    """formatter 只作用于顶层；子命令 help 仍干净、仍列自己的 flag。"""
    monkeypatch.setattr(sys, "argv", ["inkstone", "generate", "--help"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "--fresh" in out
    assert "Quick start" not in out
