# 统一 CLI 入口设计（uv run inkstone）

日期：2026-11-04
状态：已获用户批准（彻底统一 / 物理删除旧脚本 / `inkstone web` 子命令 / flag 单次覆盖模型）

## 目标

整个项目对外只有一种运行方式：`uv run inkstone <子命令> [参数]`。
Web UI、自定义模型、指定书目都通过子命令或标准 `--flag` 表达，并在 README 写清楚。

## 现状缺口

1. `inkstone` 入口已存在（`pyproject.toml`：`inkstone = "core.cli:main"`），17 个子命令已挂载。
2. Web UI 仍是独立入口 `uv run python web/server.py`，未进命令树。
3. 自定义模型只能靠环境变量（`AGNES_CHAT_MODEL` 等），无 CLI 参数。
4. **CLI 自身不读 `.env`**（仅 `web/server.py` 与 `scripts/start.sh` 读）——这是
   `uv run inkstone` 直接报 `AGNES_API_KEY is not set` 的根因。

## 命令设计

```
uv run inkstone                                   # 默认 generate 默认示例，打印横幅
uv run inkstone generate --book examples/scene1.txt --density B --format cbz
uv run inkstone generate --chat-model agnes-3.0-flash --image-model agnes-image-2.5-flash
uv run inkstone web                               # http://127.0.0.1:8000
uv run inkstone web --host 0.0.0.0 --port 9000
uv run inkstone plan --book examples/scene1.txt
```

- `generate --book` 与既有位置参数 `source` 等价（`args.book or args.source`）。
- 生成类命令新增 `--chat-model / --image-model / --provider / --base-url`：
  单次覆盖、仅写 `os.environ`、不落盘、优先于 `.env`、`--help` 可查。
  映射：`--chat-model`→`AGNES_CHAT_MODEL`/`OPENAI_COMPAT_CHAT_MODEL`，
  `--image-model`→`AGNES_IMAGE_I2I_MODEL`/`OPENAI_COMPAT_MODEL_I2I`，
  `--provider`→`PROVIDER`，`--base-url`→`AGNES_BASE_URL`/`OPENAI_COMPAT_BASE_URL`。
  这些 flag 同时加到 `web`，使 UI 启动的 job 沿用同一套覆盖。
- `web` 子命令复用 `web/server.py:main()`：按 `--host/--port` 预置
  `INKSTONE_UI_HOST/PORT`，`main()` 内 `apply_runtime_config()` 重读生效。
  零侵入服务主体。
- CLI 在 `main()` 最前、`parse_args` 之前加载 repo 根 `.env`（逻辑抽到
  `core/dotenv.py`，不覆盖已导出变量），对所有子命令统一生效。

## 组件

- `core/dotenv.py`（新）：`load_dotenv()`，纯 stdlib 解析 `KEY=value`，不执行 shell。
- `core/cli.py`：`main()` 加载 .env；parser 加 `web` 子命令与模型 flag；
  `generate` 加 `--book`；新增 `_apply_model_overrides()`、`_run_web()`；
  `_run_generate` 合并 `book/source`。
- `web/__init__.py`（新）+ `pyproject.toml`：`packages` 加 `web`，
  `package-data` 加 `web/index.html`，保证非 editable 安装下 `inkstone web` 可用。
- `web/server.py`：移除 `__main__` 直启块，头注释改指 `uv run inkstone web`。

## 物理删除（用户明确选择"彻底统一、物理删除"）

删除以下文件；范围经探查确认，交付时逐项列出：

- `scripts/start.sh`、`scripts/start.ps1`、`scripts/run_showcase.sh`
- `scripts/colab_run.sh`（Colab 远程编排子系统，29KB）与 `scripts/dotenv.sh`（其共享库）
- `docs/guides/colab-cli.md`（上者的专门指南）
- `tests/test_dotenv_loader.py`、`tests/test_showcase_script.py`（断言上述脚本内容）
- 连带清理"活文档"引用：`README.md`、`docs/ONBOARDING.md`、`.env.example`、
  `web/index.html`、`examples/showcase/journey-west-ch1/README.md`
- 历史归档（`CHANGELOG.md`、`docs/ROADMAP.md`、`docs/superpowers/*`）保留不改，
  它们记录的是过去版本的事实。

> 注：删除 `colab_run.sh` 等于移除整个 Colab 功能。这是「彻底统一」的连带结果，
> 用户已在选项「最彻底，但不可逆、牵动面大」下确认。

## 不在范围内

- 开发/测试命令（`uv run pytest`、`examples/first_panel.py`、CI 的 `uv run inkstone ...`）不变。
- `examples/generate_comic.py` 是演示 wrapper（有测试覆盖），保留；README 不主推。
- `plan` / `prompt` 等既有子命令与参数不变（prompt 已有 `--model`）。
- 不新增 `config` 子命令、不持久化模型选择到磁盘。

## 验证

`uvx ruff format .`、`uvx ruff check`、相关 pytest、`uv run inkstone --help`、
`uv run inkstone generate --help`、`uv run inkstone web --help`、
`NO_COLOR=1 uv run inkstone`（.env 加载与默认示例路径）、`uv run inkstone web` 启动探针。

## README

重写 Quick Start / One-click launch / Web UI 段，新增「Running Inkstone」章节：
统一入口、子命令速查表、自定义模型、指定书目、Web UI、`.env`、颜色开关；
Configuration 表补 flag 覆盖说明；移除所有旧启动脚本与 Colab 链接的主推。
