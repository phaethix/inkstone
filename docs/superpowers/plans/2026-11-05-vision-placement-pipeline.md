# 多模态看图选位接入 generate 流水线 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 generate 在渲染出整页成品图后，调用 agnes-3.0-flash 看整页图决定每个文字框位置（贴说话者/放留白），失败时回退现有确定性选位，并让 4 页据此重新叠字、落产物。

**Architecture:** 在 async 主循环 `_render_page_node` 里、`await asyncio.to_thread(_letter_page_from_blank)` 之前，新增一次带缓存的异步视觉调用，产出「每 job 的归一化坐标 + 按文字收紧后的像素框」，作为新参数 `placement_boxes` 一路传到同步的 `letter_finished_page`。`letter_finished_page` 优先用这些框叠字，`placement_boxes is None` 时回退 `pick_lettering_region`。视觉结果按 blank 图 + 文案 + 模型 + prompt 版本缓存到 `comic_out/vision_placement/`。图像层完全不动，只换叠字坐标来源。

**Tech Stack:** Python 3.12, Pillow, 现有 `ChatProvider.chat_function_call`（OpenAI 多模态 `image_url` + forced function-call），asyncio.to_thread, 现有 stage/manifest 缓存。

## Global Constraints

- 不改图像层 / render 逻辑；`render_fingerprint` 必须保持 `b83e15e507975a27`（默认 `panel_continuity=True`）。generate 时**不设** `INKSTONE_PANEL_CONTINUITY`。
- 复用 `AGNES_API_KEY` + `agnes-3.0-flash`，不新增密钥；用 `core.dotenv.load_dotenv()` 加载 `.env`。
- 模型失败/非法坐标/无 key → 回退到现有 `pick_lettering_region` 确定性选位（用户明确选定）。
- 保留「借模型位置、尺寸由 `fit_lettering_box` 按文字算」；保留 `blend_fill_with_art` 环境色 + `text_ink_for_fill` 字色。
- AGENTS.md：写完代码后必须执行 `uvx ruff format .`。
- 不擅自 `git commit`（用户一贯习惯）。
- 已有文件：`core/comic/vision_placement.py`（`build_placement_request` / `parse_placements` / `fit_boxes_to_text` / `PLACEMENT_TOOL` / `PLACEMENT_TOOL_CHOICE` / `SYSTEM_PROMPT` / `PLACEMENT_PROMPT_VERSION="place_v2"`）与 `tests/test_vision_placement.py`（4 个通过测试）；`scripts/vision_placement_preview.py` 为已验收的预览脚本。以下任务在此基础上增改。

---

### Task 1: 视觉选位结果的缓存与编排模块

**Files:**
- Create: `core/comic/vision_layout.py`
- Test: `tests/test_vision_layout.py`

**Interfaces:**
- Consumes: `core.comic.vision_placement`（`build_placement_request`, `parse_placements`, `fit_boxes_to_text`, `PLACEMENT_PROMPT_VERSION`）, `core.api.chat_provider.get_chat_provider`, `core.schemas.ComicPagePlan`, `core.comic.page_lettering.resolve_lettering_jobs` / `fit_lettering_box`, `core.comic.layout.LayoutEngine`
- Produces: `async def vision_placement_boxes(blank: Image.Image, plan: ComicPagePlan, source_text: str, cache_dir: Path, *, font_path: str|None = None) -> list[tuple[int,int,int,int]] | None` —— 成功返回每个 job 收紧后的像素框（顺序与 `resolve_lettering_jobs` 一致），任何失败返回 `None`；`def placement_cache_key(blank_bytes: bytes, items: list[tuple[str,str]], model: str, prompt_version: str) -> str`

**实现要点：**
- `placement_cache_key`：sha256(blank_bytes + canonical(items) + model + prompt_version)。
- `vision_placement_boxes`：用 `resolve_lettering_jobs(plan, source_text=...)` 取 `[(pid,kind,text,frac)]` → `items=[(kind,text)]`；查 `cache_dir/<key>.json`，命中且条目数匹配则读回；否则 `req = build_placement_request(blank, items)` → `await get_chat_provider().chat_function_call(req["messages"], req["tools"], req["tool_choice"], max_retries=2)` → `parse_placements(raw, expected_count=len(items), page_size=blank.size)` → `fit_boxes_to_text(LayoutEngine(font_path=font_path), items, boxes, blank.size)` → 写缓存 JSON → 返回。全程 `try/except Exception` 返回 `None`（回退语义）。
- 顺序必须与 `resolve_lettering_jobs` 完全一致，供 `letter_finished_page` 按 zip 对齐。

- [ ] **Step 1: 写失败测试** `tests/test_vision_layout.py`：
  - `test_placement_cache_key_is_stable_and_content_sensitive`：同一输入稳定、不同图/文案/模型/prompt 版本不同。
  - `test_vision_placement_boxes_uses_cache_without_calling_model`（注入一个抛异常的假 chat / monkeypatch `get_chat_provider`，预置缓存 JSON，断言返回缓存框且未调用模型）。
  - `test_vision_placement_boxes_returns_none_on_model_failure`（monkeypatch `get_chat_provider` 抛异常，无缓存，断言 `None`）。
  - `test_vision_placement_boxes_shrinks_boxes_to_text`（monkeypatch 假 chat 返回超大框，断言返回框数量==jobs 数、每框小于模型框、在页内）。
- [ ] **Step 2: 运行确认 RED**：`uv run pytest tests/test_vision_layout.py -q` → ImportError（模块未建）。
- [ ] **Step 3: 实现** `core/comic/vision_layout.py` 如上。
- [ ] **Step 4: 运行确认 GREEN**：`uv run pytest tests/test_vision_layout.py -q` → 4 passed。
- [ ] **Step 5: 提交** `git add core/comic/vision_layout.py tests/test_vision_layout.py && git commit -m "feat: cached vision placement orchestration"`

### Task 2: letter_finished_page 支持优先使用视觉坐标、失败回退

**Files:**
- Modify: `core/comic/page_lettering.py`（`letter_finished_page` 函数，约 322-350 行）
- Test: `tests/test_page_lettering.py`

**Interfaces:**
- Consumes: Task 1 的 `vision_placement_boxes` 产出的 `list[tuple[int,int,int,int]] | None`
- Produces: `letter_finished_page(blank, plan, *, font_path=None, source_text="", placement_boxes: list[tuple[int,int,int,int]] | None = None) -> Image.Image`（新增可选参数，默认 `None` 保持旧行为）

**实现要点：**
- 当 `placement_boxes` 不为 None 且长度等于 jobs 数：对每个 job，用 `placement_boxes[i]` 作为 anchor → `fit_lettering_box(engine, kind, text, anchor, (w,h))` 得到最终框（再收紧一次保证一致）→ `_avoid_overlap` + `_shift_off_faces` 兜底整洁 → 叠字。
- 当 `placement_boxes is None`：保持现状（走 `pick_lettering_region`），一字不改。
- 视觉坐标分支里**不**再走 `_safe_anchor` 的 caption 边缘约束（那会把模型位置推走），但保留 `_avoid_overlap` 防堆叠。

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_page_lettering.py`）：
  - `test_letter_finished_page_uses_provided_placement_boxes`：给固定 `placement_boxes=[(60,60,200,40)]` 与单文字 plan，画到纯色 blank，断言框区域像素 != 背景（证明用了给定位置）；与默认 `letter_finished_page` 结果不同。
  - `test_letter_finished_page_falls_back_when_placement_boxes_none`：断言 `placement_boxes=None` 与不传参数结果字节一致（旧行为保持）。
- [ ] **Step 2: 运行确认 RED**：`uv run pytest tests/test_page_lettering.py::test_letter_finished_page_uses_provided_placement_boxes -q` → TypeError（无此参数）。
- [ ] **Step 3: 实现** `letter_finished_page` 新参数与分支。
- [ ] **Step 4: 运行确认 GREEN**：`uv run pytest tests/test_page_lettering.py -q` → 全绿（含旧测试无回归）。
- [ ] **Step 5: 提交** `git add core/comic/page_lettering.py tests/test_page_lettering.py && git commit -m "feat: letter_finished_page accepts vision placement boxes with fallback"`

### Task 3: 主循环接入异步视觉调用 + 传递坐标

**Files:**
- Modify: `core/pipelines/creative_comic.py`（`_letter_page_from_blank` 签名约 711 行；`_render_page_node` 调 `_letter_page_from_blank` 处约 2388 行；`_LETTER_SOURCES` 约 495 行）
- Test: `tests/test_stage_contract.py`（确认新 callee 声明）、必要时新增针对 `_letter_page_from_blank` 透传的单测

**Interfaces:**
- Consumes: Task 1 `vision_placement_boxes`, `placement_cache_key`；Task 2 `letter_finished_page(placement_boxes=...)`
- Produces: `_letter_page_from_blank(..., placement_boxes: list | None = None)`；主循环在 `await asyncio.to_thread(...)` 前 `await vision_placement_boxes(...)`

**实现要点：**
- `_letter_page_from_blank` 增可选参数 `placement_boxes`，在 `_letter()` 里传给 `letter_finished_page(..., placement_boxes=placement_boxes)`。
- `_render_page_node`（async）在调用 `_letter_page_from_blank` 前：`cache_dir = output_dir / "vision_placement"`；`placement_boxes = await vision_placement_boxes(blank_img, plan, _chunk, cache_dir, font_path=...)`（blank 用 `Path(existing.blank_local)` 打开）；把 `placement_boxes` 放进 `partial(...)`。视觉调用只在 letter 状态为 `miss`/需重 letter 时做（跟随现有 `if status != "current" or ...` 分支），current 时不调用，避免每次跑都烧钱。
- `font_path`：复用主循环已有的字体解析（参考现有 `letter_finished_page` 调用方如何取 font；若无则 `None`，走默认字体）。
- `_LETTER_SOURCES`：登记 `letter_finished_page` 新增直接调用方（Task 2 若有新 helper）；`vision_placement_boxes` 在 `_render_page_node`（render 节点、非 letter stage hash 的 callee）里调用，需按 `test_stage_contract` 结果决定是否登记；目标是 `stage_source_hash` 因改动变化 → 触发全 4 页重新叠字。
- 环境变量：`INKSTONE_VISION_PLACEMENT=0` 可关闭（默认开），关闭时 `placement_boxes=None` 全程回退。

- [ ] **Step 1: 写失败测试**：在 `tests/` 增一个测试，断言 `_letter_page_from_blank` 接受 `placement_boxes` 关键字并透传（可 monkeypatch `letter_finished_page` 记录收到的参数；用 tmp blank + 最小 plan）。
- [ ] **Step 2: 运行确认 RED**：`uv run pytest <new test> -q` → TypeError。
- [ ] **Step 3: 实现** 签名透传 + 主循环 await 调用 + `_LETTER_SOURCES` 登记 + 开关。
- [ ] **Step 4: 运行确认 GREEN**：`uv run pytest -q` 全绿；`uv run pytest tests/test_stage_contract.py -q` 全绿（补齐声明直到通过）。
- [ ] **Step 5: 提交** `git add ... && git commit -m "feat: wire multimodal vision placement into generate pipeline"`

### Task 4: 全量重生成落产物 + 验收

**Files:**
- 无新增代码；产物：`comic_out/vision_placement/*.json`（缓存）, `comic_out/pages/page_*.png`（重新叠字）, `comic_out/comic.pdf`

- [ ] **Step 1: 备份** `cp comic_out/state.json /tmp/state.pre_vision_wire.json`；记录 `render_fingerprint`（应为 `b83e15e507975a27`）。
- [ ] **Step 2: 格式化** `uvx ruff format .` + `uv run ruff check .` + `uv run pytest -q` 全绿。
- [ ] **Step 3: 重生成** `uv run inkstone generate --out comic_out --yes`（不设 continuity，默认 True，fingerprint 不变 → 不重画；letter stage_src 变 → 4 页重新叠字；视觉调用各 1 次）。
- [ ] **Step 4: 取证** timing 无 `page` 计数、`render_fingerprint` 仍 `b83e15e5`、4 张 blank 图 hash 不变、4 张 page PNG hash 全变、`vision_placement/` 有 4 个缓存 JSON。
- [ ] **Step 5: 亲眼验收** 提取 `comic_out/comic.pdf` 4 页，read_image 逐页确认：文字框贴合 AI 摆位（贴说话者/留白）、尺寸精致、底色融入环境、不堆叠不压脸；与已验收的第二版预览一致。
- [ ] **Step 6: 清理** 删 `/tmp/state.pre_vision_wire.json`；给下载链接；沉淀 longmemory；向用户报告（含诚实边界：模型对「对白/旁白」判断有随机性，已缓存固化本版结果）。

## Self-Review

- **Spec coverage:** 用户要求「真·多模态看整页图决定位置、全部 4 页重排、失败回退确定性选位、贴说话者/放留白、尺寸按文字算」→ Task1（看图+缓存+尺寸）、Task2（优先用坐标+回退）、Task3（接 generate+4 页）、Task4（落产物验收）全覆盖。
- **Placeholder scan:** 无 TBD/TODO；每步有具体命令与判据。
- **Type consistency:** `placement_boxes: list[tuple[int,int,int,int]] | None` 在 Task1 产出、Task2/Task3 消费一致；`fit_boxes_to_text(engine, items, model_boxes, page_size)` 签名与已实现模块一致；`resolve_lettering_jobs` 顺序一致。
