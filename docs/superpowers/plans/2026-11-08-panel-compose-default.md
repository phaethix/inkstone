# Panel-Compose Default (逐格渲染 + 本地拼版) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `panel_compose` 重定义为「page-plan 规划链 + 逐格成像 + LayoutEngine 本地拼版/字幕」并设为默认渲染方式；旧整页模式保留为 `finished_page` 回退；旧 storyboard 链仅经 `INKSTONE_RENDER_MODE=storyboard` 进入。

**Architecture:** 规划链（beats → page_plan → bible → 全部守卫）与 `finished_page` 完全共享；只把 `_render_page_node` 的「页级一次成像」换成逐格渲染（每格只挂本格角色参考图、prompt 带硬人数声明与禁字令、action 文本做人物断言清洗、不注入 beat ID），然后本地 PIL 拼版并绘制台词。页面状态模型（`generated.pages`/`pages_done`/`page_cache`）不变，导出/续作/审阅全部复用。

**Tech Stack:** Python + uv、pydantic、Pillow（LayoutEngine）、pytest、ruff。无新依赖。

## Global Constraints

- 测试一律 `uv run pytest ...`；格式化一律 `uvx ruff format .`（AGENTS.md 规则1）；lint 用 `uv run ruff check core tests`。
- 每个任务严格 TDD：先写失败测试→跑红→最小实现→跑绿→`uvx ruff format .` + lint→commit。
- 不新增第三方依赖；不改 `PagePanelSpec`/`ComicPagePlan` schema 字段。
- 图像 prompt 用英文；字幕/日志用户可见文案可中文。
- 编辑前必须先读目标文件当前真实内容（行号以当前 HEAD 为准，下列行号是编写计划时的参考）。
- 提交信息用英文 conventional commits（feat/test/refactor/docs）。

---

### Task 1: render_mode 三模式 + 预算 stage + panel 尺寸配置

**Files:**
- Modify: `core/config.py:293-297`（`render_mode`）、`core/config.py:299-302` 附近（新增 `panel_size`）
- Modify: `core/pipelines/creative_comic.py:1222-1236`（`_required_budget_stages`）
- Test: `tests/test_render_mode_config.py`（新建）

**Interfaces:**
- Produces: `render_mode() -> str` 返回 `panel_compose | finished_page | storyboard`（本任务默认仍是 `finished_page`，Task 9 才翻转默认）；`panel_size() -> str`（默认 `"1024x1024"`，env `INKSTONE_PANEL_SIZE`）；`_required_budget_stages(mode, page_script)` 三分支。

- [ ] **Step 1: 写失败测试**

```python
"""tests/test_render_mode_config.py — render-mode selection and budget stages."""

import pytest

from core.config import panel_size, render_mode
from core.pipelines.creative_comic import _required_budget_stages


def test_render_mode_defaults_to_finished_page_for_now(monkeypatch):
    monkeypatch.delenv("INKSTONE_RENDER_MODE", raising=False)
    assert render_mode() == "finished_page"


def test_render_mode_recognizes_storyboard(monkeypatch):
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "storyboard")
    assert render_mode() == "storyboard"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("panel_compose", "panel_compose"),
        ("panel", "panel_compose"),
        ("compose", "panel_compose"),
        ("finished_page", "finished_page"),
        ("legacy_storyboard", "storyboard"),
        ("bogus", "finished_page"),
    ],
)
def test_render_mode_aliases(monkeypatch, raw, expected):
    monkeypatch.setenv("INKSTONE_RENDER_MODE", raw)
    assert render_mode() == expected


def test_panel_size_default_and_override(monkeypatch):
    monkeypatch.delenv("INKSTONE_PANEL_SIZE", raising=False)
    assert panel_size() == "1024x1024"
    monkeypatch.setenv("INKSTONE_PANEL_SIZE", "1536x1024")
    assert panel_size() == "1536x1024"


def test_budget_stages_storyboard_needs_storyboard_not_page_plan():
    stages = _required_budget_stages("storyboard", False)
    assert "storyboard" in stages
    assert "page_plan" not in stages


def test_budget_stages_panel_compose_matches_page_planning():
    stages = _required_budget_stages("panel_compose", False)
    assert {"beats", "page_plan", "render"} <= stages
    assert "storyboard" not in stages
```

- [ ] **Step 2: 跑红** — `uv run pytest tests/test_render_mode_config.py -v`，预期 FAIL（`storyboard` 被归为 `finished_page`、`panel_size` 不存在、预算分支不符）。

- [ ] **Step 3: 实现**

`core/config.py` 把 `render_mode` 改为：

```python
def render_mode() -> str:
    raw = _get(ENV_RENDER_MODE, "finished_page").strip().lower()
    if raw in {"panel_compose", "panel", "compose"}:
        return "panel_compose"
    if raw in {"storyboard", "legacy_storyboard"}:
        return "storyboard"
    return "finished_page"


def panel_size() -> str:
    raw = _get(ENV_PANEL_SIZE, "1024x1024").strip()
    return raw or "1024x1024"
```

并在常量区（`ENV_RENDER_MODE = "INKSTONE_RENDER_MODE"` 附近）加 `ENV_PANEL_SIZE = "INKSTONE_PANEL_SIZE"`。默认值本任务保持 `finished_page`（Task 9 翻转）。

`core/pipelines/creative_comic.py` 的 `_required_budget_stages` 改为：

```python
    required = {"extract", "bible", "portrait", "render"}
    if mode == "storyboard":
        required.add("storyboard")
        if page_script:
            required.add("page_script")
    else:
        required.update({"beats", "page_plan"})
    return required
```

- [ ] **Step 4: 跑绿** — `uv run pytest tests/test_render_mode_config.py -v` 全 PASS；再跑 `uv run pytest tests/test_budget.py tests/test_fingerprint_split.py -q`（若存在）确认无回归。
- [ ] **Step 5:** `uvx ruff format . && uv run ruff check core tests`，然后 `git add -A && git commit -m "feat(config): recognize storyboard render mode and panel size"`

---

### Task 2: LayoutEngine.compose_single_page

**Files:**
- Modify: `core/comic/layout.py:104`（`compose` 之后加公共方法）
- Test: `tests/test_layout.py`（追加）

**Interfaces:**
- Produces: `LayoutEngine.compose_single_page(panels: list[PanelImage]) -> Image.Image`（不分页，把这些格画到一张画布）。

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_layout.py`）

```python
def test_compose_single_page_places_all_panels_on_one_canvas():
    engine = LayoutEngine(page_width=800, cell_height=600)
    panels = [
        PanelImage(Image.new("RGB", (300, 300), (255, 0, 0))),
        PanelImage(
            Image.new("RGB", (300, 300), (0, 255, 0)),
            dialogue="你好。",
            caption="旁白",
        ),
    ]
    canvas = engine.compose_single_page(panels)
    assert canvas.size[0] == 800
    assert canvas.size[1] >= 600
```

- [ ] **Step 2: 跑红** — `uv run pytest tests/test_layout.py::test_compose_single_page_places_all_panels_on_one_canvas -v`，FAIL（无此方法）。
- [ ] **Step 3: 实现** — `core/comic/layout.py` 在 `compose` 方法后加：

```python
    def compose_single_page(self, panels: list[PanelImage]) -> Image.Image:
        """Render exactly these panels onto one page canvas without paginating."""
        return self._render_page(panels)
```

- [ ] **Step 4: 跑绿** — 同名测试 PASS；`uv run pytest tests/test_layout.py tests/test_layout_cjk.py -q` 无回归。
- [ ] **Step 5:** format + lint + `git commit -m "feat(layout): add compose_single_page for one planned page"`

---

### Task 3: 逐格 prompt 构造（人数硬声明 + action 人物清洗 + 禁字令）

**Files:**
- Modify: `core/comic/page_prompt.py`（`_strip_setting_people` 之后加函数；复用 `_page_scene`、`_character_desc_for_prompt`、`_reference_bindings`、`DEFAULT_PANEL_STYLE`、`parse_stage_ref`、`resolve_character_asset`——均为本模块已有导入/定义）
- Test: `tests/test_page_panel_prompt.py`（新建）

**Interfaces:**
- Consumes: `ComicPagePlan`/`PagePanelSpec`（core.schemas）。
- Produces: `render_page_panel_prompt(plan, panel, *, characters_by_name, settings_by_name, style_guide, visual_bible=None, reference_images=None, strict=False) -> str`；`_PANEL_NO_TEXT_BAN` 常量。

- [ ] **Step 1: 写失败测试**

```python
"""tests/test_page_panel_prompt.py — per-panel prompt for panel_compose."""

from core.comic.page_prompt import render_page_panel_prompt
from core.schemas import CharacterAsset, ComicPagePlan, PagePanelSpec, Setting


def _plan(action, characters, setting_ref="亭子"):
    panel = PagePanelSpec(
        panel_id="1", action=action, characters=list(characters), setting_ref=setting_ref
    )
    return ComicPagePlan(
        page_id="p1",
        panels=[panel],
        setting_refs=[setting_ref],
        reference_characters=list(characters),
    )


def _assets():
    return {"沈知意": CharacterAsset(name="沈知意", l1_prompt="a shy young woman")}


def _settings():
    return {
        "亭子": Setting(
            name="亭子",
            scene_prompt=(
                "lakeside pavilion at dusk, one young man fishing, "
                "two distant figures on the railing"
            ),
        )
    }


def test_panel_prompt_asserts_cast_and_scrubs_extra_people():
    plan = _plan("沈知意 pours tea; two distant figures on the railing", ["沈知意"])
    prompt = render_page_panel_prompt(
        plan,
        plan.panels[0],
        characters_by_name=_assets(),
        settings_by_name=_settings(),
        style_guide="manhua",
    )
    assert "Exactly 1 character in this panel: 沈知意" in prompt
    assert "two distant figures" not in prompt
    assert "one young man fishing" not in prompt
    assert "lakeside pavilion" in prompt
    assert "pours tea" in prompt
    assert "no speech bubbles" in prompt


def test_panel_prompt_empty_cast_is_scenery_only():
    plan = _plan("the boat drifts", [])
    prompt = render_page_panel_prompt(
        plan, plan.panels[0], characters_by_name={}, settings_by_name={}, style_guide="manhua"
    )
    assert "No people in this panel" in prompt


def test_panel_prompt_multi_cast_counts_in_order():
    assets = _assets()
    assets["福贵"] = CharacterAsset(name="福贵", l1_prompt="an old farmer")
    plan = _plan("沈知意 pours tea for 福贵", ["沈知意", "福贵"])
    prompt = render_page_panel_prompt(
        plan, plan.panels[0], characters_by_name=assets, settings_by_name={}, style_guide=""
    )
    assert "Exactly 2 characters in this panel: 沈知意, 福贵" in prompt


def test_panel_prompt_strict_ban_and_reference_bindings():
    plan = _plan("沈知意 waits", ["沈知意"])
    prompt = render_page_panel_prompt(
        plan,
        plan.panels[0],
        characters_by_name=_assets(),
        settings_by_name={},
        style_guide="manhua",
        reference_images=["/tmp/x.png"],
        strict=True,
    )
    assert "absolutely no text" in prompt
    assert "reference image 1 is 沈知意" in prompt
```

注意：`CharacterAsset`/`Setting` 构造若必填字段不同，对照 `core/schemas.py` 与 `tests/test_finished_page_pipeline.py` 里的既有构造调整。

- [ ] **Step 2: 跑红** — FAIL（函数不存在）。
- [ ] **Step 3: 实现** — `core/comic/page_prompt.py` 追加：

```python
_PANEL_NO_TEXT_BAN = (
    "no text, no speech bubbles, no captions, no sound effects, "
    "no letters, no numbers, no watermarks, no subtitles"
)


def render_page_panel_prompt(
    plan: ComicPagePlan,
    panel: PagePanelSpec,
    *,
    characters_by_name: dict[str, CharacterAsset],
    settings_by_name: dict[str, Setting],
    style_guide: str,
    visual_bible: VisualBible | None = None,
    reference_images: list[str] | None = None,
    strict: bool = False,
) -> str:
    """Prompt for ONE panel of a page plan, drawn as its own image.

    Identity comes from the character descriptions plus the reference images;
    the cast count is asserted so the model cannot invent extra figures, the
    action is scrubbed of people-assertions the cast does not name, and beat
    bookkeeping never reaches the image prompt.
    """
    scene = _page_scene(plan, settings_by_name)
    cast_names = [parse_stage_ref(name)[0] for name in panel.characters if name]
    descs: list[str] = []
    for name in panel.characters:
        asset = resolve_character_asset(name, characters_by_name, visual_bible)
        if asset is not None:
            descs.append(_character_desc_for_prompt(name, asset, visual_bible))
    count = len(cast_names)
    if count == 0:
        count_line = "No people in this panel; scenery only"
    elif count == 1:
        count_line = f"Exactly 1 character in this panel: {cast_names[0]}"
    else:
        count_line = f"Exactly {count} characters in this panel: {', '.join(cast_names)}"
    action = _strip_setting_people(panel.action or "", cast_names)
    if strict:
        ban = "absolutely no text or lettering of any kind anywhere in the image"
    else:
        ban = _PANEL_NO_TEXT_BAN
    if reference_images:
        bindings = _reference_bindings(
            list(panel.characters), characters_by_name, reference_images, visual_bible
        )
        owners = ", ".join(f"reference image {index} is {name}" for index, name in bindings)
        if owners:
            ban = f"{ban}; {owners}"
    style = f"{style_guide}, {DEFAULT_PANEL_STYLE}" if style_guide else DEFAULT_PANEL_STYLE
    parts = [p for p in (scene, *descs, count_line, action, ban, style) if p]
    return ", ".join(parts)
```

- [ ] **Step 4: 跑绿** — 4 个测试 PASS；`uv run pytest tests/test_screenwriter_pages.py -q` 无回归。
- [ ] **Step 5:** format + lint + `git commit -m "feat(prompt): per-panel prompt with cast assertion, action scrub, no-text ban"`

---

### Task 4: 逐格参考图收集（pipeline 私有助手）

**Files:**
- Modify: `core/pipelines/creative_comic.py`（`_panel_key_material` 附近加模块级函数；按需补 `from core.comic.identity import resolve_character_asset`、`from core.comic.visual_bible import parse_stage_ref, resolve_canonical_name`——先 grep 确认是否已导入）
- Test: `tests/test_page_panel_prompt.py`（追加）

**Interfaces:**
- Produces: `_page_panel_reference_paths(panel, characters_by_name, visual_bible, ledger_refs, output_dir) -> list[str]`（只含本格角色、去重、存在且在项目目录内）。

- [ ] **Step 1: 写失败测试**（追加）

```python
def test_page_panel_refs_only_panel_cast(tmp_path):
    from core.pipelines.creative_comic import _page_panel_reference_paths

    shen = tmp_path / "shen.png"
    fu = tmp_path / "fu.png"
    shen.write_bytes(b"p")
    fu.write_bytes(b"p")
    assets = {
        "沈知意": CharacterAsset(name="沈知意", l1_prompt="x", portrait_local=str(shen)),
        "福贵": CharacterAsset(name="福贵", l1_prompt="x", portrait_local=str(fu)),
    }
    panel_a = PagePanelSpec(panel_id="1", characters=["沈知意"])
    panel_b = PagePanelSpec(panel_id="2", characters=["福贵", "沈知意"])
    assert _page_panel_reference_paths(panel_a, assets, None, {}, tmp_path) == [str(shen)]
    assert _page_panel_reference_paths(panel_b, assets, None, {}, tmp_path) == [str(fu), str(shen)]


def test_page_panel_refs_drop_missing_files(tmp_path):
    from core.pipelines.creative_comic import _page_panel_reference_paths

    assets = {"沈知意": CharacterAsset(name="沈知意", l1_prompt="x", portrait_local="/nope.png")}
    panel = PagePanelSpec(panel_id="1", characters=["沈知意"])
    assert _page_panel_reference_paths(panel, assets, None, {}, tmp_path) == []
```

- [ ] **Step 2: 跑红** — FAIL。
- [ ] **Step 3: 实现** — `creative_comic.py` 加：

```python
def _page_panel_reference_paths(
    panel,
    characters_by_name: dict,
    visual_bible,
    ledger_refs: dict[str, str],
    output_dir: Path,
) -> list[str]:
    """Portrait paths for ONE page-plan panel: its cast only, deduped, on disk."""
    paths: list[str] = []
    seen: set[str] = set()
    for name in panel.characters:
        asset = resolve_character_asset(name, characters_by_name, visual_bible)
        loc = getattr(asset, "portrait_local", None) if asset is not None else None
        if not loc and ledger_refs:
            base = parse_stage_ref(name)[0]
            if visual_bible is not None:
                base = resolve_canonical_name(base, visual_bible)
            loc = ledger_refs.get(base) or ledger_refs.get(name)
        if loc and loc not in seen and _is_within(loc, output_dir) and Path(loc).is_file():
            seen.add(loc)
            paths.append(loc)
    return paths
```

- [ ] **Step 4: 跑绿** — PASS。
- [ ] **Step 5:** format + lint + `git commit -m "feat(pipeline): collect per-panel portrait references"`

---

### Task 5: resume 助手（page-panel key + chunk 完整判定）

**Files:**
- Modify: `core/pipelines/resume.py`（`page_asset_path`/`page_needs_generation` 附近）
- Test: `tests/test_panel_page_resume.py`（新建）

**Interfaces:**
- Produces: `page_panel_state_key(chunk_index, page_index, panel_index) -> str`（如 `"c0000-p0000-g0000"`）；`panel_page_chunk_complete(state, pageset, output_dir, chunk_index) -> bool`。

- [ ] **Step 1: 写失败测试**

```python
"""tests/test_panel_page_resume.py — panel_compose resume helpers."""

from pathlib import Path

from PIL import Image

from core.pipelines.resume import (
    page_panel_state_key,
    page_state_key,
    panel_page_chunk_complete,
)
from core.schemas import (
    ComicPagePlan,
    ComicPagePlanSet,
    GeneratedPanel,
    GeneratedPage,
    ProjectState,
    PagePanelSpec,
)


def test_page_panel_state_key_distinct_from_page_key():
    assert page_panel_state_key(0, 0, 0) == "c0000-p0000-g0000"
    assert page_panel_state_key(0, 0, 0) != page_state_key(0, 0)


def _state_with(pageset, tmp_path: Path, *, panels_done: bool, pages_done: bool):
    state = ProjectState(
        project_id="p",
        source_file="s",
        source_fingerprint="f",
        structure_fingerprint="f",
        render_fingerprint="r",
        model_snapshot={},
    )
    state.page_cache["0"] = pageset
    for page_index, plan in enumerate(pageset.pages):
        page_key = page_state_key(0, page_index)
        if pages_done:
            for panel_index in range(len(plan.panels)):
                panel_key = page_panel_state_key(0, page_index, panel_index)
                panel_path = tmp_path / f"{panel_key}.png"
                if panels_done:
                    Image.new("RGB", (4, 4)).save(panel_path)
                    state.generated.panels[panel_key] = GeneratedPanel(
                        local=str(panel_path), chunk_index=0, panel_index=panel_index
                    )
                    state.panels_done.append(panel_key)
            page_path = tmp_path / f"{page_key}.png"
            Image.new("RGB", (4, 4)).save(page_path)
            state.generated.pages[page_key] = GeneratedPage(local=str(page_path))
            state.pages_done.append(page_key)
    return state


def test_panel_page_chunk_complete_requires_every_panel(tmp_path):
    plan = ComicPagePlan(
        page_id="p1", panels=[PagePanelSpec(panel_id="1"), PagePanelSpec(panel_id="2")]
    )
    pageset = ComicPagePlanSet(pages=[plan])
    state = _state_with(pageset, tmp_path, panels_done=True, pages_done=True)
    assert panel_page_chunk_complete(state, pageset, tmp_path, 0)
    # 第二格未画：不完整。
    second = page_panel_state_key(0, 0, 1)
    state.panels_done.remove(second)
    assert not panel_page_chunk_complete(state, pageset, tmp_path, 0)


def test_panel_page_chunk_complete_false_when_page_missing(tmp_path):
    plan = ComicPagePlan(page_id="p1", panels=[PagePanelSpec(panel_id="1")])
    pageset = ComicPagePlanSet(pages=[plan])
    state = _state_with(pageset, tmp_path, panels_done=False, pages_done=False)
    assert not panel_page_chunk_complete(state, pageset, tmp_path, 0)
```

（`ProjectState`/`GeneratedPage` 必填字段以 `core/schemas.py` 为准，必要时对照 `tests/test_finished_page_pipeline.py` 的构造方式调整；`model_snapshot` 若需 `ModelSnapshot` 则用 `ModelSnapshot()`。）

- [ ] **Step 2: 跑红** — FAIL。
- [ ] **Step 3: 实现** — `resume.py` 加：

```python
def page_panel_state_key(chunk_index: int, page_index: int, panel_index: int) -> str:
    """Pipeline identity for one panel of a planned page (panel_compose mode)."""
    return f"{page_state_key(chunk_index, page_index)}-g{panel_index:04d}"


def panel_page_chunk_complete(
    state: ProjectState,
    pageset: ComicPagePlanSet,
    output_dir: Path,
    chunk_index: int,
) -> bool:
    """True when every planned page's panels are generated or policy-skipped.

    panel_compose pages are recomposed locally on every run: a complete chunk
    needs its panel images on disk, not a letter-key hit.
    """
    for page_index, plan in enumerate(pageset.pages):
        page_key = page_state_key(chunk_index, page_index)
        if page_key in state.skipped_pages:
            continue
        rec = state.generated.pages.get(page_key)
        if rec is None or page_key not in state.pages_done:
            return False
        if not is_within(rec.local, output_dir) or not Path(rec.local).exists():
            return False
        for panel_index in range(len(plan.panels)):
            panel_key = page_panel_state_key(chunk_index, page_index, panel_index)
            if panel_key in state.skipped:
                continue
            gen = state.generated.panels.get(panel_key)
            if gen is None or panel_key not in state.panels_done:
                return False
            if not is_within(gen.local, output_dir) or not Path(gen.local).exists():
                return False
    return True
```

- [ ] **Step 4: 跑绿** — PASS；`uv run pytest tests/test_render_node_expansion.py -q` 无回归。
- [ ] **Step 5:** format + lint + `git commit -m "feat(resume): page-panel state keys and panel-page chunk completeness"`

---

### Task 6: compose_page_panels 本地拼版

**Files:**
- Create: `core/comic/page_compose.py`
- Test: `tests/test_page_compose.py`（新建）

**Interfaces:**
- Consumes: Task 2 的 `compose_single_page`。
- Produces: `compose_page_panels(plan: ComicPagePlan, panel_images: list[Image.Image], blank_path: Path, lettered_path: Path) -> None`。

- [ ] **Step 1: 写失败测试**

```python
"""tests/test_page_compose.py — local composition of page-plan panels."""

from PIL import Image

from core.comic.page_compose import compose_page_panels
from core.schemas import ComicPagePlan, PagePanelSpec


def test_compose_writes_blank_and_lettered_pages(tmp_path):
    plan = ComicPagePlan(
        page_id="p1",
        panels=[
            PagePanelSpec(panel_id="1", dialogue="我回来了。", caption="傍晚，村口。"),
            PagePanelSpec(panel_id="2"),
        ],
    )
    images = [Image.new("RGB", (300, 200), (200, 200, 200)) for _ in plan.panels]
    blank = tmp_path / "blank.png"
    lettered = tmp_path / "lettered.png"
    compose_page_panels(plan, images, blank, lettered)
    assert blank.is_file() and lettered.is_file()
    assert blank.read_bytes() != lettered.read_bytes()


def test_compose_rejects_panel_count_mismatch(tmp_path):
    import pytest

    from core.comic.page_compose import compose_page_panels

    plan = ComicPagePlan(page_id="p1", panels=[PagePanelSpec(panel_id="1")])
    with pytest.raises(ValueError):
        compose_page_panels(plan, [], tmp_path / "b.png", tmp_path / "l.png")
```

- [ ] **Step 2: 跑红** — FAIL（模块不存在）。
- [ ] **Step 3: 实现** — 新建 `core/comic/page_compose.py`：

```python
"""core.comic.page_compose — local composition of page-plan panels.

panel_compose renders each panel of a page plan as its own image and assembles
the page here: grid layout plus deferred lettering drawn by PIL, so no image
model ever draws text.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from core.comic.layout import LayoutEngine, PanelImage
from core.schemas import ComicPagePlan


def compose_page_panels(
    plan: ComicPagePlan,
    panel_images: list[Image.Image],
    blank_path: Path,
    lettered_path: Path,
) -> None:
    """Write the blank (art-only) and lettered page for one planned page.

    ``panel_images`` must align with ``plan.panels``; lettering text comes from
    each panel's dialogue/caption/sfx.
    """
    if len(panel_images) != len(plan.panels):
        raise ValueError(
            f"panel image count {len(panel_images)} != plan panel count {len(plan.panels)}"
        )
    engine = LayoutEngine()
    blank = engine.compose_single_page([PanelImage(img) for img in panel_images])
    lettered = engine.compose_single_page(
        [
            PanelImage(img, dialogue=spec.dialogue, caption=spec.caption, sfx=spec.sfx)
            for img, spec in zip(panel_images, plan.panels, strict=True)
        ]
    )
    blank_path.parent.mkdir(parents=True, exist_ok=True)
    lettered_path.parent.mkdir(parents=True, exist_ok=True)
    blank.save(blank_path)
    lettered.save(lettered_path)
```

- [ ] **Step 4: 跑绿** — PASS。
- [ ] **Step 5:** format + lint + `git commit -m "feat(compose): compose page-plan panels locally with PIL lettering"`

---

### Task 7: face_lock 年纪前缀补修（young stage 不得继承 adult 前缀）

**Files:**
- Modify: `core/comic/visual_bible.py`（抽 `_STALE_AGE_PREFIX_RE`/`strip_stale_age_prefix` 供 `l1_from_canon` 与 `ensure_stage_locks` 共用；`ensure_stage_locks` 在用 `canon_face` 前先剥旧前缀再按本 stage `age_look` 重加）
- Test: `tests/test_visual_bible_v2.py`（追加）

**Interfaces:**
- Produces: `strip_stale_age_prefix(face: str) -> str`。

- [ ] **Step 1: 写失败测试**（构造方式对照该文件既有 `CharacterCanon` 用法）

```python
def test_young_stage_never_inherits_adult_prefix_from_face_lock():
    canon = CharacterCanon(
        canonical_name="阿远",
        gender="male",
        face_lock="adult man, sharp jaw",
        stages=[
            StageLock(
                stage="teen",
                age_look="sixteen-year-old boy",
                outfit_lock="school uniform",
                hair_lock="black mop",
                portrait_key="阿远",
            )
        ],
    )
    fixed = ensure_canon_locks(canon)
    row = next(s for s in fixed.stages if s.stage == "teen")
    assert "adult" not in (row.portrait_lock or "").lower()
    l1 = l1_from_canon(fixed, "teen")
    assert "adult" not in l1.lower()
    assert "sixteen-year-old boy" in l1
```

- [ ] **Step 2: 跑红** — FAIL（stage 行内仍含 `adult`）。
- [ ] **Step 3: 实现** — 先读 `ensure_stage_locks`（约 `visual_bible.py:1104` 附近，该行已算 `age_look = stage.age_look or default_age_look_for_stage(stage.stage)`）。把 `l1_from_canon`（约 2130 行）里的内联 `re.sub` 抽成模块级：

```python
_STALE_AGE_PREFIX_RE = re.compile(
    r"(?i)^((adult\s+|young\s+)?(man|woman|male|female|nonbinary person|boy|girl))\s*,\s*"
)


def strip_stale_age_prefix(face: str) -> str:
    """Drop a leading gender/age phrase; callers re-add the age-aware one."""
    return _STALE_AGE_PREFIX_RE.sub("", (face or "").strip()).strip()
```

`l1_from_canon` 改用它；`ensure_stage_locks` 在把 `canon_face` 写进 stage 锁之前同样先 `strip_stale_age_prefix`，再按 `gender_prefix(gender, stage_age)` 重加年龄一致前缀（仿 `l1_from_canon` 的 `if prefix and not face.casefold().startswith(prefix.casefold())` 逻辑）。

- [ ] **Step 4: 跑绿** — 新测试 PASS；`uv run pytest tests/test_visual_bible_v2.py tests/test_stage_lock.py -q` 无回归。
- [ ] **Step 5:** format + lint + `git commit -m "fix(bible): young stages never inherit a stale adult face prefix"`

---

### Task 8: 管线接入（条件放宽 + 逐格页节点 + 端到端）

**Files:**
- Modify: `core/pipelines/creative_comic.py`（多处，见下）
- Test: `tests/test_panel_compose_pipeline.py`（新建）

**Interfaces:**
- Consumes: Task 3/4/5/6 全部产物。
- Produces: `mode == "panel_compose"` 时走 page-plan 规划 + 逐格渲染 + 本地拼版；页面资产命名 `pages/page_cNNNN_pMMMM.png`（+ `blank/`），格图 `panels/cNNNN-pMMMM-gMMMM.png`；新 stage 源 `_RENDER_PAGE_PANEL_STAGE_SRC`。

- [ ] **Step 1: 写失败测试**（fake provider 自包含，模式显式设为 `panel_compose`）

```python
"""tests/test_panel_compose_pipeline.py — panel_compose orchestration (fakes, no network)."""

import asyncio
import json
from pathlib import Path

from PIL import Image

from core.api import ChatProvider, ImageProvider
from core.pipelines.creative_comic import creative_comic


class FakeImageOutput:
    def __init__(self):
        self.fmt = "b64"
        self.data = ""
        self.ext = "png"

    def save(self, path):
        Image.new("RGB", (20, 30), (90, 90, 90)).save(path)


class FakeImage(ImageProvider):
    def __init__(self):
        self.calls = 0
        self.panel_prompts: list[str] = []

    async def generate_single_image(self, prompt, reference_image_paths=None, size=None, **kw):
        self.calls += 1
        if "Exactly" in prompt or "No people in this panel" in prompt:
            self.panel_prompts.append(prompt)
        return FakeImageOutput()


class FakeChat(ChatProvider):
    """Same staged answers as tests/test_finished_page_pipeline.py."""

    def __init__(self):
        self.calls = 0

    async def chat_function_call(self, messages, tools, tool_choice, **kw):
        self.calls += 1
        name = tool_choice["function"]["name"]
        if name == "extract_story_elements":
            return {
                "characters": [
                    {
                        "name": "福贵",
                        "l1_prompt": "a middle-aged farmer",
                        "portrait_prompt": "portrait of a farmer",
                    }
                ],
                "settings": [{"name": "村口", "scene_prompt": "village entrance at dusk"}],
                "style_guide": "manhua",
            }
        if name == "reconcile_visual_bible":
            return {
                "merges": [],
                "stages": [],
                "keeps": [],
                "color_patches": [],
                "style_guide": "manhua",
                "color": {
                    "palette": [{"name": "ink", "hex": "#1A1A1A", "usage": "lines"}],
                    "lighting": "soft",
                    "forbidden": [],
                },
                "canons": [
                    {
                        "canonical_name": "福贵",
                        "face_lock": "a middle-aged farmer",
                        "stages": [
                            {
                                "stage": "adult",
                                "outfit_lock": "simple farmer clothes",
                                "hair_lock": "short dark hair",
                                "portrait_key": "福贵",
                            }
                        ],
                    }
                ],
            }
        if name == "extract_key_beats":
            return {
                "beats": [
                    {
                        "beat_id": "village_gate",
                        "summary": "福贵 walks through the village entrance",
                        "must_draw": True,
                        "characters": ["福贵"],
                    }
                ]
            }
        if name == "plan_comic_pages":
            return {
                "unit_id": "1",
                "pages": [
                    {
                        "page_id": "u1_p0001",
                        "purpose": "establish the village entrance",
                        "layout_intent": "single wide establishing panel",
                        "covers_beats": ["village_gate"],
                        "panels": [
                            {
                                "panel_id": "1",
                                "role": "establishing",
                                "shape_hint": "wide",
                                "shot": "wide",
                                "action": "福贵 walks through the village entrance",
                                "characters": ["福贵"],
                                "setting_ref": "村口",
                                "caption": "傍晚，村口。",
                                "dialogue": [{"speaker": "福贵", "text": "我回来了。"}],
                            }
                        ],
                        "reference_characters": ["福贵"],
                        "setting_refs": ["村口"],
                    }
                ],
            }
        return {}


def test_panel_compose_renders_panels_and_composes_pages(tmp_path, monkeypatch):
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "panel_compose")
    src = "第一章\n福贵在村口。"
    chat, img = FakeChat(), FakeImage()
    proj = asyncio.run(creative_comic(src, output_dir=str(tmp_path), chat=chat, image=img))

    page_files = sorted((tmp_path / "pages").glob("page_c*_p*.png"))
    blank_files = sorted((tmp_path / "pages" / "blank").glob("page_c*_p*.png"))
    panel_files = sorted((tmp_path / "panels").glob("c0000-p0000-g*.png"))
    assert page_files and blank_files and panel_files

    assert proj.state.render_mode == "panel_compose"
    page_key = "c0000-p0000"
    assert page_key in proj.state.generated.pages
    assert page_key in proj.state.pages_done
    page = proj.state.generated.pages[page_key]
    assert page.mode == "finished_lettered"
    assert Path(page.blank_local).is_file()
    assert blank_files[0].read_bytes() != page_files[0].read_bytes()
    panel_key = "c0000-p0000-g0000"
    assert panel_key in proj.state.generated.panels
    assert panel_key in proj.state.panels_done

    assert proj.pdf and Path(proj.pdf).exists()
    assert img.calls == 2  # 1 portrait + 1 panel
    assert chat.calls == 4  # extract + reconcile + beats + page_plan

    prompt = img.panel_prompts[0]
    assert "Exactly 1 character in this panel: 福贵" in prompt
    assert "village_gate" not in prompt
    assert "covers_beats" not in prompt
    assert "no speech bubbles" in prompt

    lines = [
        json.loads(line)
        for line in (tmp_path / "runs.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    intents = [row for row in lines if row["kind"] == "intent"]
    assert [(row["stage"], row["key"]) for row in intents] == [
        ("extract", "c0000"),
        ("bible", "c0000"),
        ("portrait", "福贵"),
        ("beats", "c0000"),
        ("page_plan", "c0000"),
        ("render", panel_key),
    ]

    chat2, img2 = FakeChat(), FakeImage()
    proj2 = asyncio.run(creative_comic(src, output_dir=str(tmp_path), chat=chat2, image=img2))
    assert img2.calls == 0
    assert chat2.calls == 0
    assert proj2.state.panels_done == proj.state.panels_done
```

- [ ] **Step 2: 跑红** — 当前 `panel_compose` 仍走 storyboard 链：无 `page_c*_p*.png`、chat 调用含 storyboard、intents 不符 → FAIL。

- [ ] **Step 3: 管线实现**（每处编辑前先读当前真实内容；下列行号为参考）：

3a. 扩展导入：`core.comic.page_prompt` 导入块加 `render_page_panel_prompt`；新增 `from core.comic.page_compose import compose_page_panels`；`core.config` 导入块加 `panel_size`；`core.pipelines.resume` 导入块加 `page_panel_state_key as _page_panel_state_key` 与 `panel_page_chunk_complete as _panel_page_chunk_complete`。

3b. 模块级 stage 源（`_RENDER_PAGE_STAGE_SRC` 旁）：

```python
_RENDER_PAGE_PANEL_STAGE_SRC = stage_source_hash(
    render_page_panel_prompt, render_finished_page_negative_prompt
)
```

3c. 模块级助手（`_sync_stored_panel_key` 旁）：

```python
def _sync_page_panel_key(state: ProjectState, panel_key: str, render_key: str) -> None:
    """Stamp a page-panel, or drop it from the done set when its key misses."""
    existing = state.generated.panels.get(panel_key)
    if existing is None or not Path(existing.local).is_file():
        return
    if panel_key in state.stale_panels:
        return
    if not existing.render_action_key:
        existing.render_action_key = render_key
        return
    if existing.render_action_key != render_key and panel_key in state.panels_done:
        state.panels_done.remove(panel_key)
```

3d. 条件放宽（把 `mode == "finished_page"` 换成 `mode in ("finished_page", "panel_compose")`，每处带上下方 2-4 行上下文确保唯一）：
- 约 1666 行 render 节点展开选择（else 分支保持给 storyboard）。
- 约 1703、1722 行（reference-binding 检查、beats 盖章）。
- 约 1732 行多行条件里的 `mode == "finished_page"`。
- 约 1751 行 `chunk_complete`：外层条件放宽后，内部拆两支——`finished_page` 保持原 `_page_chunk_complete(...)`；`panel_compose` 用：

```python
                else:
                    chunk_complete = (
                        pageset is not None
                        and elements is not None
                        and cacheable_beats(state.beat_cache.get(key)) is not None
                        and key in set(state.chunks_done)
                        and _panel_page_chunk_complete(state, pageset, output_dir, ci)
                        and panel_key_filter is None
                    )
```

- 约 2203 行大规划分支入口（`if pageset is not None and state.visual_bible is not None:` 之前那一行）。
- 约 3341 行导出分支（`# No LayoutEngine collage ...` 注释所在 if）。

3e. `_mark_page_chunk_done_if_complete`（约 1022 行）：先读全文；加 `render_mode: str = "finished_page"` 关键字参数，内部按模式选 `_page_chunk_complete(...)` 或 `_panel_page_chunk_complete(state, pageset, output_dir, ci)`；调用点（页节点分发后，约 2805 行附近）传 `render_mode=mode`。

3f. `_render_page_node` 内，`state_key = page_state_key(_chunk_index, page_index)` 之后立刻加：

```python
if mode == "panel_compose":
    await _render_page_panels_node(plan, page_index=page_index, state_key=state_key)
    return
```

并在同层作用域（`_render_page_node` 定义旁）加完整函数：

```python
async def _render_page_panels_node(
    plan: ComicPagePlan,
    *,
    page_index: int,
    state_key: str,
) -> None:
    """panel_compose: each panel is its own image; compose locally."""
    page_is_stale = state_key in state.stale_pages
    if page_is_stale:
        for panel_index in range(len(plan.panels)):
            p_key = _page_panel_state_key(_chunk_index, page_index, panel_index)
            if p_key in state.panels_done:
                state.panels_done.remove(p_key)
    if not gate.allow(state_key, _chunk_index, total_chunks=total_chunks):
        return
    negative_prompt = render_finished_page_negative_prompt()
    panel_images: list[Image.Image] = []
    for panel_index, spec in enumerate(plan.panels):
        p_key = _page_panel_state_key(_chunk_index, page_index, panel_index)
        if p_key in state.skipped:
            continue
        prompt = render_page_panel_prompt(
            plan,
            spec,
            characters_by_name=state.characters,
            settings_by_name=state.settings,
            style_guide=effective_style,
            visual_bible=state.visual_bible,
        )
        ledger_refs = (
            {
                name: entry.reference.path
                for name, entry in ledger.characters.items()
                if entry.reference.path and Path(entry.reference.path).is_file()
            }
            if state.visual_bible is not None
            else {}
        )
        refs = _page_panel_reference_paths(
            spec, state.characters, state.visual_bible, ledger_refs, output_dir
        )
        active_size = panel_size()
        render_env = h_env(text=prompt, resolved_size=active_size, scope="render")
        render_key = render_page_action_key(
            plan_json=spec.model_dump_json(),
            prompt=prompt,
            resolved_size=active_size,
            page_key=p_key,
            ref_policy="panel_page",
            env=render_env,
            stage_src=_RENDER_PAGE_PANEL_STAGE_SRC,
            negative_prompt=negative_prompt,
        )
        _sync_page_panel_key(state, p_key, render_key)
        if not _panel_needs_generation(state, p_key):
            gen = state.generated.panels.get(p_key)
            if gen is not None and Path(gen.local).is_file():
                panel_images.append(Image.open(gen.local))
                continue
        recorded = read_manifest_entry(output_dir, render_key, stage="render.page_panel")
        if recorded is not None and recorded.outcome != "ok":
            continue
        if p_key not in state.stale_panels:
            cached = stored_output(output_dir, render_key, stage="render.page_panel")
            if cached is not None:
                local = _asset_path(output_dir, "panels", p_key)
                local.write_bytes(cached)
                panel_images.append(Image.open(local))
                _mark_panel_done(state, p_key)
                continue
        check_cancel(cancel_check)
        stricter_attempted = False
        while True:
            _charge("render", p_key)
            image_intent(
                output_dir,
                stage="render",
                key=p_key,
                prompt=prompt,
                size=active_size,
            )
            t0 = time.monotonic()
            try:
                async with image_semaphore:
                    with perf.measure("panel"):
                        out = await image.generate_single_image(
                            prompt,
                            reference_image_paths=refs,
                            size=active_size,
                            negative_prompt=negative_prompt,
                        )
            except Exception as exc:
                image_result(
                    output_dir,
                    stage="render",
                    key=p_key,
                    duration_ms=round((time.monotonic() - t0) * 1000),
                    status="error",
                )
                image_failure(output_dir, stage="render", key=p_key, error=exc)
                if is_content_policy_rejection(exc):
                    logger.warning(
                        "panel %s skipped: content filter rejected it (%s)",
                        p_key,
                        exc,
                    )
                    state.tombstones[p_key] = Tombstone(
                        outcome="rejected",
                        reason="content_policy",
                        stage="render.page_panel",
                    )
                    if p_key in state.panels_done:
                        state.panels_done.remove(p_key)
                    if p_key not in state.skipped:
                        state.skipped.append(p_key)
                    if state_key not in state.skipped_pages:
                        state.skipped_pages.append(state_key)
                    state.save(state_path, caches=False)
                    return
                if not stricter_attempted:
                    stricter_attempted = True
                    prompt = render_page_panel_prompt(
                        plan,
                        spec,
                        characters_by_name=state.characters,
                        settings_by_name=state.settings,
                        style_guide=effective_style,
                        visual_bible=state.visual_bible,
                        strict=True,
                    )
                    negative_prompt = render_finished_page_negative_prompt(strict=True)
                    logger.warning(
                        "panel %s image failed (%s); retrying once stricter",
                        p_key,
                        exc,
                    )
                    continue
                raise
            else:
                image_result(
                    output_dir,
                    stage="render",
                    key=p_key,
                    duration_ms=round((time.monotonic() - t0) * 1000),
                    status="ok",
                )
                break
        local = _asset_path(output_dir, "panels", p_key)
        await asyncio.to_thread(out.save, str(local))
        state.generated.panels[p_key] = GeneratedPanel(
            local=str(local),
            chunk_index=_chunk_index,
            panel_index=panel_index,
            source_panel_id=spec.panel_id,
            render_action_key=render_key,
        )
        _mark_panel_done(state, p_key)
        state.save(state_path, caches=False)
        if p_key in state.stale_panels:
            state.stale_panels = [k for k in state.stale_panels if k != p_key]
        panel_images.append(Image.open(local))
    if len(panel_images) != len(plan.panels):
        return
    pages_dir.mkdir(parents=True, exist_ok=True)
    blank_dir = pages_dir / "blank"
    blank_dir.mkdir(parents=True, exist_ok=True)
    blank_path = _page_asset_path(blank_dir, _chunk_index, page_index)
    local = _page_asset_path(pages_dir, _chunk_index, page_index)
    await asyncio.to_thread(compose_page_panels, plan, panel_images, blank_path, local)
    record_page_artifact(
        state,
        state_key,
        GeneratedPage(
            local=str(local),
            blank_local=str(blank_path),
            lettering_version=LETTERING_VERSION,
            resolved_size=panel_size(),
            render_action_key="panel_compose",
            prompt_inputs=[_plan_json(plan)],
            reference_binding=_REFERENCE_BINDING,
            page_id=plan.page_id,
            unit_index=_chunk_index,
            page_index=page_index,
            mode="finished_lettered",
        ),
    )
    if state_key in state.stale_pages:
        state.stale_pages = [k for k in state.stale_pages if k != state_key]
    if state_key not in state.pages_done:
        state.pages_done.append(state_key)
    if ledger.rebuild_from_state(state):
        ledger.save(ledger_path)
    state.save(state_path, caches=False)
    _report("pages", _pct())
```

（`Tombstone`/`GeneratedPanel`/`h_env`/`read_manifest_entry`/`stored_output`/`_mark_panel_done` 等均已在该作用域可用，与 `_render_page_node`/storyboard 路径一致；`_mark_panel_done` 若签名不同以现文件为准。）

- [ ] **Step 4: 跑绿** — `uv run pytest tests/test_panel_compose_pipeline.py -v` PASS；`uv run pytest tests/test_finished_page_pipeline.py tests/test_render_node_expansion.py -q` 无回归（这三个文件若有用例依赖旧 `panel_compose` 语义，按 Task 9 的指引改为显式 env）。
- [ ] **Step 5:** format + lint + `git commit -m "feat(pipeline): panel_compose renders per-panel images and composes pages locally"`

---

### Task 9: 默认翻转 + 存量测试修复 + 文档

**Files:**
- Modify: `core/config.py:294`（默认值）
- Modify: 受影响测试（见 Step 2）、`README*`/`docs` 中提及 `INKSTONE_RENDER_MODE` 的段落

**Interfaces:**
- Produces: 不设 env 时 `render_mode() == "panel_compose"`。

- [ ] **Step 1: 翻转默认** — `render_mode()` 的 `_get(ENV_RENDER_MODE, "finished_page")` 改为 `_get(ENV_RENDER_MODE, "panel_compose")`；同步更新 `tests/test_render_mode_config.py::test_render_mode_defaults_to_finished_page_for_now`（改名 `test_render_mode_defaults_to_panel_compose`，断言 `panel_compose`）。

- [ ] **Step 2: 修复存量测试** — 全量跑 `uv run pytest -q`。对每个因默认翻转而失败的用例：若它测的是「整页成像」路径，在用例开头加 `monkeypatch.setenv("INKSTONE_RENDER_MODE", "finished_page")`；若是依赖旧 storyboard 语义的 `panel_compose` 用例（如 `tests/test_render_node_expansion.py`、`tests/test_plan_body_manifests.py`、`tests/test_cli_output_layout.py`、`tests/test_fingerprint_split.py`、`tests/conftest.py` 中的夹具），把 env 改为 `"storyboard"` 或更新断言到新语义。已知点名：`tests/test_finished_page_pipeline.py:274` `test_render_fingerprint_omits_lettering_for_panel_compose`——`panel_compose` 现在本地绘制字幕，指纹应像 `finished_page` 一样跟踪 `LETTERING_VERSION`；把该用例的 env 改为 `"storyboard"` 以继续覆盖「省略」行为，或反转断言（grep `LETTERING_VERSION` 在 `creative_comic.py` 的指纹调用点，让 `panel_compose` 归入跟踪一侧）。

- [ ] **Step 3: 文档** — `grep -rn "INKSTONE_RENDER_MODE" README* docs/ 2>/dev/null`，把默认值与三模式语义写清：`panel_compose`（默认，逐格+本地拼版字幕）、`finished_page`（整页一次成像回退）、`storyboard`（旧链，仅内部兼容）。

- [ ] **Step 4: 全量验证** — `uv run pytest -q` 全绿；`uvx ruff format .`；`uv run ruff check core tests` 通过。
- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat!: make panel_compose the default render mode (finished_page remains as fallback)"`

---

## 验收（人工）

1. `uv run inkstone generate comic_out/source.txt --out comic_out --fresh --yes --density B`（需 `AGNES_API_KEY`）：每格人数与 cast 一致、画面无文字、台词由本地绘制、无 beat ID 泄漏的幻觉人物。
2. 断点续作：重跑同一命令，`img.calls == 0`、`chat.calls == 0`（面板/规划全部命中缓存，仅本地重拼版）。
3. `INKSTONE_RENDER_MODE=finished_page` 回退可用；`INKSTONE_RENDER_MODE=storyboard` 进入旧链。
