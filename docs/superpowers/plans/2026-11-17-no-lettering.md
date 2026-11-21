# Inkstone `--no-lettering` 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 `inkstone generate` 增加 `--no-lettering` 开关：关闭本地叠字，输出纯画面漫画（无旁白条、无对白气泡、无拟声词），并跳过视觉定位的 chat 调用。

**Architecture:** 配置层新增 `INKSTONE_LETTERING` 环境变量、`lettering_enabled()` 读取函数与 `set_lettering_env()` 置位函数。管线侧唯一叠字 funnel `_letter_page_from_blank` 在开关关闭时跳过 PIL 叠字，把 blank 底图按字节复制为成品页，仍走既有 `run_stage("letter", ...)` 缓存与清单机制；开关值折进 letter 阶段标识 `_placement_stage_src`，切换开关只失效 letter 缓存键，blank 底图全部命中缓存。页循环中的视觉定位调用在开关关闭时短路。CLI 两个入口（`core/cli.py` 的 `generate` 子命令、`core/cli_generate.py`）各加 `--no-lettering` flag，通过 `set_lettering_env()` 落地。

**Tech Stack:** Python 3.12 / pytest / PIL (Pillow) / argparse；测试运行器 `uv run pytest`。

## Global Constraints

- 测试统一用 `uv run pytest` 运行（项目用 uv 管理依赖）。
- 写完代码后必须执行 `uvx ruff format .`（AGENTS.md 规则 1）。
- 环境变量名精确为 `INKSTONE_LETTERING`；值语义：`"0"` 关、`"1"` 开、缺省 `"1"`（默认保持叠字）。
- CLI flag 名精确为 `--no-lettering`（`action="store_true"`）。
- 页面记录 mode 沿用 `"finished_lettered"`，不新增枚举值；导出（export_stages / PDF / CBZ / webtoon）与 reader 零改动。
- plan 中的 caption/dialogue/sfx 字段照常生成，不做 scrub。
- prompt 层的无字禁令（`core/comic/page_prompt.py` 等）不碰。
- 默认行为不变：不传 flag 且无环境变量时，叠字照常。

---

### Task 1: 配置层 `lettering_enabled()` 与 `set_lettering_env()`

**Files:**
- Modify: `core/config.py`（常量区约 L33 `ENV_RENDER_MODE` 附近加常量；`render_mode()` 约 L294 附近加两个函数）
- Test: `tests/test_config_lettering.py`（新建）

**Interfaces:**
- Consumes: `core.config` 内已有的 `_get(key, default)` 帮助函数与常量定义模式（参照 `ENV_RENDER_MODE = "INKSTONE_RENDER_MODE"`）。
- Produces: 常量 `ENV_LETTERING = "INKSTONE_LETTERING"`；`lettering_enabled() -> bool`（`_get(ENV_LETTERING, "1").strip().lower()` 不在 `{"0","false","no","off",""}` 内即 True）；`set_lettering_env(no_lettering: bool) -> None`（True → `os.environ[ENV_LETTERING] = "0"`；False → `os.environ.pop(ENV_LETTERING, None)`）。后续任务消费：Task 2 管线读 `lettering_enabled()`；Task 3 两个 CLI 调 `set_lettering_env()`。注意先确认 `core/config.py` 是否已 `import os`，无则补。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_config_lettering.py
"""INKSTONE_LETTERING 配置函数的行为测试。"""

import pytest

from core import config


def test_lettering_default_is_enabled(monkeypatch):
    monkeypatch.delenv(config.ENV_LETTERING, raising=False)
    assert config.lettering_enabled() is True


@pytest.mark.parametrize("raw", ["0", " false", "FALSE", "no", "OFF", ""])
def test_lettering_falsy_values_disable(monkeypatch, raw):
    monkeypatch.setenv(config.ENV_LETTERING, raw)
    assert config.lettering_enabled() is False


def test_set_lettering_env_true_sets_zero(monkeypatch):
    monkeypatch.delenv(config.ENV_LETTERING, raising=False)
    config.set_lettering_env(True)
    import os

    assert os.environ[config.ENV_LETTERING] == "0"


def test_set_lettering_env_false_pops(monkeypatch):
    monkeypatch.setenv(config.ENV_LETTERING, "1")
    config.set_lettering_env(False)
    import os

    assert config.ENV_LETTERING not in os.environ
```

- [ ] **Step 2: 运行确认红灯**

Run: `uv run pytest tests/test_config_lettering.py -v`
Expected: FAIL（`ENV_LETTERING`/`lettering_enabled`/`set_lettering_env` 不存在 → AttributeError）

- [ ] **Step 3: 最小实现**

在 `core/config.py` 常量区（`ENV_RENDER_MODE` 旁）：

```python
ENV_LETTERING = "INKSTONE_LETTERING"
```

在 `render_mode()` 旁：

```python
def lettering_enabled() -> bool:
    raw = _get(ENV_LETTERING, "1").strip().lower()
    return raw not in {"0", "false", "no", "off", ""}


def set_lettering_env(no_lettering: bool) -> None:
    """CLI flag 落地点：True 强制关闭叠字；False 清除干预，尊重用户已有 env。"""
    import os

    if no_lettering:
        os.environ[ENV_LETTERING] = "0"
    else:
        os.environ.pop(ENV_LETTERING, None)
```

（若 `core/config.py` 顶部已有 `import os`，则函数内改为直接用模块级 `os`，去掉局部 import。）

- [ ] **Step 4: 运行确认绿灯**

Run: `uv run pytest tests/test_config_lettering.py -v`
Expected: PASS（5 个测试全绿）

- [ ] **Step 5: 格式化并提交**

```bash
uvx ruff format .
git add core/config.py tests/test_config_lettering.py
git commit -m "feat(config): add INKSTONE_LETTERING with lettering_enabled/set_lettering_env"
```

---

### Task 2: 管线：开关收口到 `_letter_page_from_blank` 与视觉定位短路

**Files:**
- Modify: `core/pipelines/creative_comic.py:540-551`（`_placement_stage_src`）、`creative_comic.py:1139-1192`（`_letter_page_from_blank`）、`creative_comic.py:3133-3150`（页循环视觉定位守卫）
- Test: `tests/test_no_lettering.py`（新建）

**Interfaces:**
- Consumes: Task 1 的 `lettering_enabled()`；既有 `_placement_stage_src`、`run_stage`、`letter_finished_page`、`letter_inputs`、`_letter_key`、`_letter_resume_status`、`shutil`（该文件如未导入 shutil 则补）。
- Produces:
  - `_placement_stage_src(placement_boxes, *, lettering: bool = True) -> str`：`lettering=True` 时返回值与现状完全一致（`_LETTER_STAGE_SRC` 或 `f"{_LETTER_STAGE_SRC}:vp-{digest}"`）；`lettering=False` 时返回 `f"{base}:nl-1"`（base 为无 placement 时的 `_LETTER_STAGE_SRC`）。
  - `_letter_page_from_blank(blank_path, local_path, plan, *, source_text="", output_dir=None, resolved_size=None, placement_boxes=None, lettering: bool | None = None) -> None`：`lettering=None` → 读 `lettering_enabled()`；非 None → 用参数值。开关关闭时：不调用 `letter_finished_page`，直接 `shutil.copyfile(blank_path, local_path)`（先 `mkdir(parents=True, exist_ok=True)`），`output_dir` 非 None 时仍走 `run_stage(root, "letter", key, ...)`，closure 返回 `[(local_path.name, local_path.read_bytes())]`，`run.replayed` 时回写 `local_path.write_bytes(run.files[0][1])`（与现状一致）。
  - 页循环（约 L3139）：`if lettering_enabled(): placement_boxes = await vision_placement_boxes(...)`，else `placement_boxes = None`（短路时不发起 chat 调用）；下游 `placement_stage_src` 与 `_letter_page_from_blank` 调用点把 `lettering=lettering_enabled()` 传入。

- [ ] **Step 1: 写失败测试**

先定位两个依赖形状（只读，不改）：
- Run: `grep -n "class ComicPagePlan" core/schemas*.py core/schemas/*.py 2>/dev/null || grep -rn "class ComicPagePlan" core/ | head -5`，确认 plan 最小构造字段。
- blank fixture：优先从 `~/Downloads/comic_out/pages/blank/` 复制一张真实底图；目录为空则用 PIL 造 64x96 灰图。

```python
# tests/test_no_lettering.py
"""--no-lettering: 关闭叠字时 blank 直出为成品页，letter 缓存键随开关变化。"""

import shutil
from pathlib import Path

import pytest
from PIL import Image

from core import config
from core.pipelines import creative_comic as cc


@pytest.fixture
def blank_and_plan(tmp_path):
    from core.schemas import ComicPagePlan

    blank = tmp_path / "blank.png"
    Image.new("RGB", (64, 96), (200, 200, 200)).save(blank)
    # 以 schema 实际字段为准构造最小合法 plan（含一个 panel，caption/dialogue 非空）
    plan = ComicPagePlan.model_validate(
        {
            "panels": [
                {
                    "shot": "wide",
                    "action": "test panel",
                    "caption": "narration",
                    "dialogue": "hello",
                }
            ]
        }
    )
    return blank, plan


def test_lettering_off_copies_blank_as_finished_page(tmp_path, blank_and_plan, monkeypatch):
    blank, plan = blank_and_plan
    monkeypatch.setenv(config.ENV_LETTERING, "0")
    finished = tmp_path / "finished.png"
    calls: list[object] = []
    monkeypatch.setattr(
        cc,
        "letter_finished_page",
        lambda *a, **k: calls.append(a) or (_ for _ in ()).throw(AssertionError("must not letter")),
    )
    cc._letter_page_from_blank(blank, finished, plan, output_dir=tmp_path)
    assert finished.is_file()
    assert finished.read_bytes() == blank.read_bytes()


def test_lettering_on_keeps_current_behavior(tmp_path, blank_and_plan, monkeypatch):
    blank, plan = blank_and_plan
    monkeypatch.delenv(config.ENV_LETTERING, raising=False)
    finished = tmp_path / "finished.png"
    seen: dict[str, bool] = {"called": False}

    def _fake_letter(blank_img, plan_arg, **kwargs):
        seen["called"] = True
        return blank_img.copy()

    monkeypatch.setattr(cc, "letter_finished_page", _fake_letter)
    cc._letter_page_from_blank(blank, finished, plan, output_dir=tmp_path)
    assert seen["called"] is True
    assert finished.is_file()


def test_explicit_lettering_param_overrides_env(tmp_path, blank_and_plan, monkeypatch):
    blank, plan = blank_and_plan
    monkeypatch.setenv(config.ENV_LETTERING, "0")
    finished = tmp_path / "finished.png"
    seen: dict[str, bool] = {"called": False}

    def _fake_letter(blank_img, plan_arg, **kwargs):
        seen["called"] = True
        return blank_img.copy()

    monkeypatch.setattr(cc, "letter_finished_page", _fake_letter)
    cc._letter_page_from_blank(blank, finished, plan, output_dir=tmp_path, lettering=True)
    assert seen["called"] is True


def test_stage_src_folds_lettering_flag():
    on = cc._placement_stage_src(None, lettering=True)
    off = cc._placement_stage_src(None, lettering=False)
    assert on == cc._placement_stage_src(None, lettering=True)  # 稳定可复算
    assert off == cc._placement_stage_src(None, lettering=False)
    assert off != on
    assert off.startswith(on) or on.startswith(off)


def test_default_lettering_arg_reads_env(tmp_path, blank_and_plan, monkeypatch):
    blank, plan = blank_and_plan
    monkeypatch.setenv(config.ENV_LETTERING, "0")
    finished = tmp_path / "finished.png"
    cc._letter_page_from_blank(blank, finished, plan, output_dir=tmp_path)
    assert finished.read_bytes() == blank.read_bytes()
```

注意：`ComicPagePlan`/panel 的最小构造字段以 Step 1 的 grep 结果为准调整；若 `shot`/`action` 字段名不符，用 schema 真实字段名替换。

- [ ] **Step 2: 运行确认红灯**

Run: `uv run pytest tests/test_no_lettering.py -v`
Expected: FAIL（`_placement_stage_src` 不接受 `lettering` 关键字 → TypeError；`_letter_page_from_blank` 不接受 `lettering` → TypeError）

- [ ] **Step 3: 最小实现**

3a. `_placement_stage_src`（约 L540）加 kw-only 参数并折叠开关：

```python
def _placement_stage_src(
    placement_boxes: list[tuple[int, int, int, int]] | None,
    *,
    lettering: bool = True,
) -> str:
    """Fold vision coordinates (and the lettering toggle) into the stage identity.

    The letter cache keys on blank + plan + stage_src. Vision boxes are a new
    overlay input that is NOT in the plan, so without this a changed AI layout
    would silently reuse the previous cached overlay. ``lettering=False`` adds
    a marker so toggling the flag only invalidates letter keys, never blanks.
    """
    base = _LETTER_STAGE_SRC
    if not placement_boxes:
        return base if lettering else f"{base}:nl-1"
    canonical = json.dumps([list(b) for b in placement_boxes], separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    return f"{base}:vp-{digest}" if lettering else f"{base}:vp-{digest}:nl-1"
```

3b. `_letter_page_from_blank`（约 L1139）加 `lettering: bool | None = None` 参数并短路：

```python
def _letter_page_from_blank(
    blank_path: Path,
    local_path: Path,
    plan: ComicPagePlan,
    *,
    source_text: str = "",
    output_dir: Path | None = None,
    resolved_size: str | None = None,
    placement_boxes: list[tuple[int, int, int, int]] | None = None,
    lettering: bool | None = None,
) -> None:
    """Render deferred lettering from a persisted blank page.

    ...（保留原 docstring，并补一句）When ``lettering`` is False the blank is
    copied to ``local_path`` verbatim: art-only publishing with no caption,
    dialogue, or SFX overlay. ``None`` reads INKSTONE_LETTERING.
    """
    if lettering is None:
        lettering = lettering_enabled()
    root = Path(output_dir) if output_dir is not None else None
    stage_src = _placement_stage_src(placement_boxes, lettering=lettering)
    key = ""
    env = ""
    plan_json = ""
    if root is not None:
        key, env, plan_json = _letter_key(
            blank_path.read_bytes(),
            plan,
            source_text,
            resolved_size=resolved_size,
            stage_src=stage_src,
        )

    def _letter() -> list[tuple[str, bytes]]:
        local_path.parent.mkdir(parents=True, exist_ok=True)
        if lettering:
            with Image.open(blank_path) as blank:
                lettered = letter_finished_page(
                    blank, plan, source_text=source_text, placement_boxes=placement_boxes
                )
            lettered.save(local_path)
        else:
            shutil.copyfile(blank_path, local_path)
        return [(local_path.name, local_path.read_bytes())]

    if root is None:
        _letter()
        return
    run = run_stage(
        root,
        "letter",
        key,
        _letter,
        stage_src=stage_src,
        env=env,
        inputs=letter_inputs(blank_path.read_bytes(), plan_json),
    )
    if run.tombstoned or not run.files:
        return
    if run.replayed:
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(run.files[0][1])
```

导入补充：`import shutil`（若缺失）与 `from core.config import lettering_enabled`（并入该文件现有的 `core.config` 导入组）。

3c. 页循环视觉定位守卫（约 L3138-3150），现状：

```python
    placement_boxes: list[tuple[int, int, int, int]] | None = None
    if os.environ.get("INKSTONE_VISION_PLACEMENT", "1") != "0":
        ...
        placement_boxes = await vision_placement_boxes(...)
    else:
        placement_boxes = None
    placement_stage_src = _placement_stage_src(placement_boxes)
```

改为（保留 `INKSTONE_VISION_PLACEMENT` 既有开关，外层加 lettering 短路）：

```python
    placement_boxes: list[tuple[int, int, int, int]] | None = None
    if lettering_enabled() and os.environ.get("INKSTONE_VISION_PLACEMENT", "1") != "0":
        ...
        placement_boxes = await vision_placement_boxes(...)
    else:
        placement_boxes = None
    placement_stage_src = _placement_stage_src(placement_boxes, lettering=lettering_enabled())
```

同函数下方 `_letter_page_from_blank` 调用点（约 L3166 与 L3265、L3448 三处）各补 `lettering=lettering_enabled()` 参数（对应 read_file 中 stage_src=_LETTER_STAGE_SRC 的既有调用）。

- [ ] **Step 4: 运行确认绿灯**

Run: `uv run pytest tests/test_no_lettering.py tests/test_page_lettering.py -v`
Expected: PASS（新测试全绿，既有 lettering 测试无回归）

- [ ] **Step 5: 格式化并提交**

```bash
uvx ruff format .
git add core/pipelines/creative_comic.py tests/test_no_lettering.py
git commit -m "feat(pipeline): INKSTONE_LETTERING=0 publishes art-only pages, folds toggle into letter stage id"
```

---

### Task 3: CLI `--no-lettering`（两个入口）

**Files:**
- Modify: `core/cli.py`：parser 区 `--export-lettered` 参数（约 L193-197）之后加 flag；`_run_generate`（约 L1027-1047）调 `set_lettering_env(args.no_lettering)`；顶部若无 `import os` 不需要（helper 在 config 内）。
- Modify: `core/cli_generate.py`：parser 区 `--export-lettered`（约 L409-413）之后加 flag；`main()`（约 L432-444）在 `run_generate(...)` 之前调 `set_lettering_env(args.no_lettering)`。
- Test: `tests/test_cli_no_lettering.py`（新建）

**Interfaces:**
- Consumes: Task 1 的 `set_lettering_env()`。
- Produces: `--no-lettering` flag（`args.no_lettering: bool`，两个解析器均支持）；行为契约：flag 传入 → `set_lettering_env(True)` 强制关闭；flag 未传 → `set_lettering_env(False)` 清除干预（尊重用户已有 env 配置）。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_cli_no_lettering.py
"""--no-lettering flag 解析测试。"""

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
```

- [ ] **Step 2: 运行确认红灯**

Run: `uv run pytest tests/test_cli_no_lettering.py -v`
Expected: FAIL（`unrecognized arguments: --no-lettering`）

- [ ] **Step 3: 最小实现**

3a. `core/cli.py` parser（`--export-lettered` 之后）：

```python
    p_gen.add_argument(
        "--no-lettering",
        action="store_true",
        help="Skip local lettering overlay: publish art-only pages "
        "(no captions, dialogue, or SFX); PDF binds the clean art.",
    )
```

3b. `core/cli.py` `_run_generate` 在 `from core.cli_generate import run_generate` 之后、`sys.exit(...)` 之前：

```python
    from core.config import set_lettering_env

    set_lettering_env(getattr(args, "no_lettering", False))
```

3c. `core/cli_generate.py` parser（`--export-lettered` 之后）加同样 flag（help 文案相同）。

3d. `core/cli_generate.py` `main()` 在 `args = _parse_args()` 之后、`sys.exit(run_generate(...))` 之前：

```python
    from core.config import set_lettering_env

    set_lettering_env(getattr(args, "no_lettering", False))
```

- [ ] **Step 4: 运行确认绿灯**

Run: `uv run pytest tests/test_cli_no_lettering.py -v`
Expected: PASS（4 个测试全绿）

- [ ] **Step 5: 格式化并提交**

```bash
uvx ruff format .
git add core/cli.py core/cli_generate.py tests/test_cli_no_lettering.py
git commit -m "feat(cli): add --no-lettering to generate in both entry points"
```

---

### Task 4: 文档与全量验证收尾

**Files:**
- Modify: `README.md`（仅当存在 flag 用法文档区；先 `grep -n "export-lettered\|density" README.md` 定位，无则跳过 README 变更）
- Test: 全量测试（无新测试文件）

**Interfaces:**
- Consumes: 前三个任务的全部产出。
- Produces: 可交付状态：全量测试绿、代码已 format、文档已更新（如适用）。

- [ ] **Step 1: 更新 README（如适用）**

Run: `grep -n "export-lettered" README.md`
若命中用法区，在该区追加一行：

```markdown
- `--no-lettering` — skip lettering: art-only pages (no captions/dialogue/SFX); PDF binds the clean art.
```

若无命中，跳过本步（不改 README）。

- [ ] **Step 2: 全量测试与格式化**

Run: `uv run pytest -q`
Expected: 全绿
Run: `uvx ruff format .`
若 format 改动了文件，再跑一次 `uv run pytest -q` 确认全绿。

- [ ] **Step 3: 提交（仅当 README 有变更）**

```bash
git add README.md
git commit -m "docs: document --no-lettering flag"
```

- [ ] **Step 4: 验证端到端行为（人工冒烟，不调用 API）**

Run: `uv run inkstone generate --book examples/sample_novel.txt --out /tmp/inkstone-nl-smoke --no-lettering --budget 0 2>&1 | head -20`
Expected: 管线启动且不报未知参数错误；`--budget 0` 使其在花任何费用前暂停（冒烟只验证参数传导，不产图）。完成后 `rm -rf /tmp/inkstone-nl-smoke`。
