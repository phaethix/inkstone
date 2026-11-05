# 启动横幅设计（单色细线 · 极简）

日期：2026-11-04（2026-11-05 改为极简版，取代最初的渐变面板方案）
范围：单文件视觉调整，零业务逻辑。

## 目标

让 `uv run inkstone`（`generate` 路径）的启动横幅克制、安静，贴近参照图的
单色细线 ASCII 风格，不再"花里胡哨"。

## 视觉语言

- 标志：figlet `standard` 字体拼出 `INKSTONE`，只用 `_ | / \ ( )` 等轻量线字符，
  5 行等宽，左缩进两格；无边框、无渐变、无实心块、无加粗。
- 配色：标志整体只用一种柔和青灰绿 `#7FB0C0`（比品牌青绿 `#63D3BE` 更灰更淡，
  truecolor），说明行保持终端默认前景色，不做颜色分级。
- 说明行：复刻参照图的"名称 - 描述"结构 `Inkstone - Grind your novel into comics.`，
  只一行，无副标题、无版本号、无印章分隔线。
- 排版：前导空行、标志、空行、说明行、尾空行，留出呼吸感。

## 降级策略

遵循 NO_COLOR 约定，自动检测：

- `NO_COLOR` 非空 / `TERM=dumb` / 输出非 TTY → 打印无 ANSI、无边框的纯文本。
- `FORCE_COLOR` 非空 → 强制上色。
- 提供 `color: bool | None` 显式开关，便于测试与预览生成。

`plan` / `structure` / `coverage` / `read` 等机读子命令与 `--help` 不打印横幅。

## 改动范围

- `core/banner.py`：重写渲染为单色细线；公开 `print_banner(stream, color)` 不变；
  字形存为 `_ART_ROWS` 常量，说明存为 `_LINE_TITLE`，不引入外部字体依赖。
- `core/cli.py`：调用点保持 `print_banner()` 不变。
- `tests/test_banner.py`：覆盖细线字形（无实心块 / 字符集）、单色 truecolor、
  无边框 / 无加粗、说明行结构、纯文本降级、自动检测。
- 本文档：记录极简方案。

## 验证

`uvx ruff format .`、`uvx ruff check .`、`uv run pytest -q`（含 banner 用例），
实跑 `print_banner(color=True)` 与 `print_banner(color=False)` 核对彩色 / 纯文本两种效果。

## 不在范围内

不引入任何新依赖（figlet 字形以静态常量落地）；不改动 generate 管线；
不改 CLI 子命令分发；不处理 `--fresh` 等重跑入口（另案）。
