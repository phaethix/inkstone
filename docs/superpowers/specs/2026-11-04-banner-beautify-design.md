# 启动横幅美化设计（水墨印章 · 渐变面板）

日期：2026-11-04
范围：单文件视觉调整，零业务逻辑。

## 目标

让 `uv run inkstone`（`generate` 路径）的启动横幅与项目品牌视觉一致，尽量美观。

## 视觉语言

配色取自 `assets/logo.svg` 与 README hero：墨黑 `#1B212C`、朱砂 `#C0412B`、
米白 `#F0ECE4`、青绿 `#63D3BE`、紫灰 `#8A8594`。

- 大字：保留 5×5 方块字形（已验证对齐），按列做 truecolor 水平渐变
  （青绿 → 米白 → 亮朱砂），深色终端上清晰。
- 容器：圆角 Unicode 边框面板，石板灰描边，左右留白。
- 点睛：朱砂「砚」印章 + 细分隔线，呼应 hero 里的印章圆点。
- 排版：标语（米白加粗）、副标题（紫灰）、版本号（亮朱砂）分级居中。

## 降级策略

遵循 NO_COLOR 约定，自动检测：

- `NO_COLOR` 非空 / `TERM=dumb` / 输出非 TTY → 打印无 ANSI、无边框的纯文本。
- `FORCE_COLOR` 非空 → 强制上色。
- 提供 `color: bool | None` 显式开关，便于测试与预览生成。

`plan` / `structure` / `coverage` / `read` 等机读子命令与 `--help` 不打印横幅。

## 改动范围

- `core/banner.py`：重写渲染与配色，公开 `print_banner(stream, color, version)`；
  保留 `_GLYPHS` / `_render` 供测试与预览复用。
- `core/cli.py`：调用点保持 `print_banner()` 不变。
- `tests/test_banner.py`：覆盖彩色分支、纯文本降级、自动检测、字形结构。

## 验证

`uvx ruff format .`、相关 pytest、实跑 `uv run inkstone`（彩色与管道降级两种）。

## 不在范围内

不引入任何新依赖；不改动 generate 管线；不改 CLI 子命令分发。
