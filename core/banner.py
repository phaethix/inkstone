"""core.banner — Inkstone 启动横幅（单色细线）。

主生成命令（``inkstone`` / ``inkstone generate``）启动时打印一次品牌画面，
风格取克制路线：单色细线 ASCII 标志 + 一行说明，无边框、无渐变、无实心块。

- 彩色模式：细线字形整体用一种柔和青灰绿（truecolor），说明行保持默认前景色。
- 纯文本模式：无 ANSI、无边框，仅细线字形与说明，便于管道 / 日志 / CI。

颜色自动检测遵循 NO_COLOR 约定：``NO_COLOR`` 或 ``TERM=dumb`` 或非 TTY
时降级；``FORCE_COLOR`` 强制上色；也可用 ``color=`` 显式指定，便于测试与预览。

``plan`` / ``structure`` / ``coverage`` / ``read`` 等输出结构化数据的子命令
以及 ``--help`` 刻意不打印横幅，避免污染可机读的 stdout。
"""

from __future__ import annotations

import os
import sys
from typing import TextIO

# 细线字形：figlet standard 字体，用 _ | / \ ( ) 等轻量字符拼出 INKSTONE。
# 每行等宽，不依赖任何外部字体文件或依赖，便于纯文本降级直接复用。
_ART_ROWS: tuple[str, ...] = (
    " ___ _   _ _  ______ _____ ___  _   _ _____ ",
    "|_ _| \\ | | |/ / ___|_   _/ _ \\| \\ | | ____|",
    " | ||  \\| | ' /\\___ \\ | || | | |  \\| |  _|  ",
    " | || |\\  | . \\ ___) || || |_| | |\\  | |___ ",
    "|___|_| \\_|_|\\_\\____/ |_| \\___/|_| \\_|_____|",
)

_TAGLINE = "Grind your novel into comics."
_LINE_TITLE = "Inkstone - Grind your novel into comics."

# 柔和青灰绿（比品牌青绿 #63D3BE 更灰更淡），深色终端上清晰但不刺眼。
_TEAL = (127, 176, 192)  # #7FB0C0

_RESET = "\x1b[0m"


def _fg(rgb: tuple[int, int, int]) -> str:
    return f"\x1b[38;2;{rgb[0]};{rgb[1]};{rgb[2]}m"


def _paint(text: str, rgb: tuple[int, int, int]) -> str:
    return _fg(rgb) + text + _RESET


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
    art = [_paint(row.rstrip(), _TEAL) for row in _ART_ROWS]
    return ["", *["  " + row for row in art], "", "  " + _LINE_TITLE, ""]


def _plain_lines() -> list[str]:
    art = [row.rstrip() for row in _ART_ROWS]
    return ["", *["  " + row for row in art], "", "  " + _LINE_TITLE, ""]


def print_banner(
    *,
    stream: TextIO | None = None,
    color: bool | None = None,
) -> None:
    """打印启动横幅。默认写 stdout；颜色自动检测，可用 ``color=`` 显式覆盖。"""
    out = stream if stream is not None else sys.stdout
    use_color = _auto_color(out) if color is None else color
    lines = _colored_lines() if use_color else _plain_lines()
    for line in lines:
        print(line, file=out)
    out.flush()
