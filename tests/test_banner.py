"""tests/test_banner.py — 启动横幅的回归约束（单色细线 / 纯文本降级）。"""

import io
import re

import pytest

from core import banner
from core.banner import print_banner

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_ANSI_FG_RE = re.compile(r"\x1b\[(38;2;\d+;\d+;\d+)m")


def _strip(text: str) -> str:
    return _ANSI_RE.sub("", text)


def test_art_is_single_color_thin_lines_not_blocks():
    """细线字形：不含实心方块，且所有可见非空白字符都是轻量线字符。"""
    rows = banner._ART_ROWS
    assert len(rows) == 5
    assert all("█" not in row for row in rows)
    assert all(len(row) == len(rows[0]) for row in rows)
    # 参照图的细线字符集，不含任何实心 / 填充块。
    allowed = set(" _|/\\()<>`-.'")
    assert all(ch in allowed for row in rows for ch in row)


def test_line_title_has_name_dash_and_slogan():
    """说明行复刻参照图的『名称 - 描述』结构，保留原 slogan。"""
    title = banner._LINE_TITLE
    assert "Inkstone" in title
    assert " - " in title
    assert banner._TAGLINE in title


def test_colored_banner_is_single_color_no_frame_no_blocks(capsys):
    """彩色模式：单色 truecolor、无圆角边框、无实心块、无加粗。"""
    print_banner(color=True)
    out = capsys.readouterr().out
    plain = _strip(out)
    assert "╭" not in plain and "│" not in plain and "╰" not in plain
    assert "█" not in plain
    assert banner._TAGLINE in plain
    assert "\x1b[1m" not in out  # 不加粗
    colors = set(_ANSI_FG_RE.findall(out))
    assert len(colors) == 1  # 整体只用一种前景色
    assert next(iter(colors)).startswith("38;2;")  # truecolor 单色


def test_plain_banner_has_no_ansi_no_frame_no_blocks():
    """纯文本降级：无 ANSI、无边框、无实心块，但保留细线字形与说明。"""
    buf = io.StringIO()
    print_banner(stream=buf, color=False)
    out = buf.getvalue()
    assert "\x1b[" not in out
    assert "╭" not in out and "│" not in out and "╰" not in out
    assert "█" not in out
    assert banner._TAGLINE in out
    for row in banner._ART_ROWS:
        assert row.rstrip() in out


def test_colored_banner_is_compact_single_tagline(capsys):
    """文字区只有一行说明，无副标题 / 版本 / 印章分隔线。"""
    print_banner(color=True)
    out = _strip(capsys.readouterr().out)
    assert out.count(banner._TAGLINE) == 1
    assert "砚" not in out
    assert "AI-Powered Novel-to-Comic Generator" not in out
    assert not re.search(r"v\d+\.\d+\.\d+", out)


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({"NO_COLOR": "1"}, False),
        ({"TERM": "dumb"}, False),
        ({"FORCE_COLOR": "1"}, True),
    ],
)
def test_auto_color_env_overrides(monkeypatch, env, expected):
    for key in ("NO_COLOR", "TERM", "FORCE_COLOR"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    class FakeStream(io.StringIO):
        def isatty(self):
            return False

    assert banner._auto_color(FakeStream()) is expected


def test_no_color_degrades_to_plain(monkeypatch, capsys):
    monkeypatch.setenv("NO_COLOR", "1")
    print_banner()
    out = capsys.readouterr().out
    assert "\x1b[" not in out


def test_print_banner_writes_to_stdout_by_default(capsys):
    print_banner(color=False)
    assert banner._TAGLINE in capsys.readouterr().out
