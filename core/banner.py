"""core.banner — Inkstone 启动横幅（渐变面板）。

主生成命令（``inkstone`` / ``inkstone generate``）启动时打印一次品牌画面，
配色取自 ``assets/logo.svg`` 与 README hero：墨黑、朱砂、米白、青绿。

- 彩色模式：5×5 方块大字按列做 truecolor 水平渐变（青绿 → 米白 → 朱砂），
  置于圆角边框面板内，下方一行标语。
- 纯文本模式：无 ANSI、无边框，仅保留方块大字与标语，便于管道 / 日志 / CI。

颜色自动检测遵循 NO_COLOR 约定：``NO_COLOR`` 或 ``TERM=dumb`` 或非 TTY
时降级；``FORCE_COLOR`` 强制上色；也可用 ``color=`` 显式指定，便于测试与预览。

``plan`` / ``structure`` / ``coverage`` / ``read`` 等输出结构化数据的子命令
以及 ``--help`` 刻意不打印横幅，避免污染可机读的 stdout。
"""

from __future__ import annotations

import os
import re
import sys
import unicodedata
from typing import TextIO

# 品牌字形：每个字母固定 5 行 × 5 列，拼接时以 1 列空格分隔
_GLYPHS: dict[str, tuple[str, ...]] = {
    "I": ("█████", "  █  ", "  █  ", "  █  ", "█████"),
    "N": ("█   █", "██  █", "█ █ █", "█  ██", "█   █"),
    "K": ("█   █", "█  █ ", "███  ", "█  █ ", "█   █"),
    "S": ("█████", "█    ", "█████", "    █", "█████"),
    "T": ("█████", "  █  ", "  █  ", "  █  ", "  █  "),
    "O": (" ███ ", "█   █", "█   █", "█   █", " ███ "),
    "E": ("█████", "█    ", "████ ", "█    ", "█████"),
}

_WORD = "INKSTONE"
_TAGLINE = "Grind your novel into comics."

_GLYPH_W = 5
_GAP = 1
_ART_W = len(_WORD) * _GLYPH_W + (len(_WORD) - 1) * _GAP  # 47
_PAD = 4
_INNER_W = _ART_W + 2 * _PAD  # 55

# 品牌配色（RGB），深色终端上取值略提亮
_TEAL = (99, 211, 190)  # #63D3BE
_CREAM = (240, 236, 228)  # #F0ECE4
_CINNABAR = (232, 96, 70)  # 亮朱砂（基于 #C0412B）
_SLATE = (90, 84, 102)  # 面板边框

_GRADIENT_STOPS: tuple[tuple[float, tuple[int, int, int]], ...] = (
    (0.0, _TEAL),
    (0.5, _CREAM),
    (1.0, _CINNABAR),
)

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_RESET = "\x1b[0m"
_BOLD = "\x1b[1m"


def _render(word: str) -> list[str]:
    """把一个单词渲染成 5 行等宽的大字（每行宽 ``len(word)*5 + (n-1)``）。"""
    return [" ".join(_GLYPHS[ch][row] for ch in word) for row in range(5)]


def _fg(rgb: tuple[int, int, int], *, bold: bool = False) -> str:
    prefix = _BOLD if bold else ""
    return f"{prefix}\x1b[38;2;{rgb[0]};{rgb[1]};{rgb[2]}m"


def _paint(text: str, rgb: tuple[int, int, int], *, bold: bool = False) -> str:
    return _fg(rgb, bold=bold) + text + _RESET


def _display_width(text: str) -> int:
    """ANSI 转义不计宽；CJK 全角按 2 列，其余（含方块/制表符）按 1 列。"""
    plain = _ANSI_RE.sub("", text)
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in plain)


def _center(content: str, width: int) -> str:
    """按显示宽度居中，content 可含 ANSI 转义。"""
    gap = max(width - _display_width(content), 0)
    left = gap // 2
    right = gap - left
    return " " * left + content + " " * right


def _gradient_at(t: float) -> tuple[int, int, int]:
    """在渐变色标上做线性插值。"""
    for (t0, c0), (t1, c1) in zip(_GRADIENT_STOPS, _GRADIENT_STOPS[1:], strict=False):
        if t <= t1:
            f = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
            return tuple(round(c0[i] + (c1[i] - c0[i]) * f) for i in range(3))
    return _GRADIENT_STOPS[-1][1]


def _gradient_row(row: str) -> str:
    """逐字符按列上色构成水平渐变；空格不着色，行尾统一复位。"""
    denom = max(len(row) - 1, 1)
    parts = [
        " " if ch == " " else _fg(_gradient_at(col / denom)) + ch for col, ch in enumerate(row)
    ]
    parts.append(_RESET)
    return "".join(parts)


def _auto_color(stream: TextIO) -> bool:
    if os.environ.get("FORCE_COLOR"):
        return True
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("TERM") == "dumb":
        return False
    try:
        return bool(stream.isatty())
    except Exception:
        return False


def _colored_lines() -> list[str]:
    top = _paint("╭" + "─" * _INNER_W + "╮", _SLATE)
    bottom = _paint("╰" + "─" * _INNER_W + "╯", _SLATE)
    pipe = _paint("│", _SLATE)
    pad = " " * _PAD

    def wrap(content: str) -> str:
        return pipe + pad + content + pad + pipe

    blank = wrap(" " * _ART_W)
    art = [wrap(_gradient_row(row)) for row in _render(_WORD)]
    tagline = wrap(_center(_paint(_TAGLINE, _CREAM, bold=True), _ART_W))

    return [
        top,
        blank,
        *art,
        blank,
        tagline,
        blank,
        bottom,
    ]


def _plain_lines() -> list[str]:
    indent = "  "
    rows = [indent + row.rstrip() for row in _render(_WORD)]
    return [
        *rows,
        "",
        indent + _center(_TAGLINE, _ART_W).rstrip(),
    ]


def print_banner(
    *,
    stream: TextIO | None = None,
    color: bool | None = None,
) -> None:
    """打印启动横幅。默认写 stdout；颜色自动检测，可用 ``color=`` 显式覆盖。"""
    out = stream if stream is not None else sys.stdout
    use_color = _auto_color(out) if color is None else color
    lines = _colored_lines() if use_color else _plain_lines()
    print(file=out)
    for line in lines:
        print(line, file=out)
    print(file=out)
    out.flush()
