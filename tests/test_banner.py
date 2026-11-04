"""tests/test_banner.py — 启动横幅的回归约束（彩色 / 纯文本降级）。"""

import io
import re

import pytest

from core import banner
from core.banner import (
    _ANSI_RE,
    _GLYPHS,
    _INNER_W,
    _TAGLINE,
    _gradient_at,
    _paint,
    _render,
    print_banner,
)


def _strip(text: str) -> str:
    return _ANSI_RE.sub("", text)


def test_glyphs_are_uniform_and_cover_the_word():
    assert set("INKSTONE") <= set(_GLYPHS)
    for glyph in _GLYPHS.values():
        assert len(glyph) == 5
        assert all(len(row) == 5 for row in glyph)


def test_render_produces_five_equal_width_rows():
    rows = _render("INKSTONE")
    assert len(rows) == 5
    assert len({len(row) for row in rows}) == 1
    assert all("█" in row for row in rows)


def test_gradient_endpoints_and_midpoint():
    assert _gradient_at(0.0) == banner._TEAL
    assert _gradient_at(1.0) == banner._CINNABAR
    assert _gradient_at(0.5) == banner._CREAM


def test_colored_banner_has_frame_brand_and_truecolor(capsys):
    print_banner(color=True)
    out = capsys.readouterr().out
    assert "Grind your novel into comics." in out
    assert "╭" in out and "╰" in out  # 圆角边框
    assert "\x1b[38;2;" in out  # truecolor
    assert "█" in out


def test_colored_banner_is_compact_single_tagline(capsys):
    print_banner(color=True)
    out = _strip(capsys.readouterr().out)
    assert out.count(_TAGLINE) == 1  # 文字区只有一行标语
    assert "砚" not in out  # 无印章分隔线
    assert "AI-Powered Novel-to-Comic Generator" not in out  # 无副标题
    assert not re.search(r"v\d+\.\d+\.\d+", out)  # 无版本行


def test_colored_frame_lines_have_consistent_display_width(capsys):
    import unicodedata

    print_banner(color=True)
    frame = [
        line for line in capsys.readouterr().out.splitlines() if line.startswith("│") or "│" in line
    ]
    widths = set()
    for line in frame:
        plain = _strip(line)
        widths.add(sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in plain))
    assert len(widths) == 1
    assert widths.pop() == _INNER_W + 2  # 两条边框竖线


def test_plain_banner_has_no_ansi_and_no_frame():
    buf = io.StringIO()
    print_banner(stream=buf, color=False)
    out = buf.getvalue()
    assert "\x1b[" not in out
    assert "╭" not in out and "│" not in out
    assert "Grind your novel into comics." in out
    assert "█" in out
    assert "砚" not in out
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
    assert "Grind your novel into comics." in capsys.readouterr().out


def test_paint_wraps_with_reset():
    colored = _paint("x", banner._CINNABAR)
    assert colored.startswith("\x1b[38;2;")
    assert colored.endswith("\x1b[0m")
    assert re.search(r"\x1b\[38;2;\d+;\d+;\d+m", colored)
