# 整页参考图去污（根治多余人物 / 构图复制）设计

日期：2026-11-06
状态：待用户 review（brainstorming User Review Gate）；按用户一贯习惯不 commit

## 1. 背景与根因（已取证）

第 4 页（c0001-p0001）红圈处画错了：右下角蹲着捧乐谱的女生是剧情里不存在的人，且「四条胳膊两双手」。本轮用 systematic-debugging 追完数据流，根因是三层叠加，主因在渲染层的参考图装配，与规划层 / prompt 文字无关。

- **主因（架构级）**：`core/comic/visual_bible.py` 的 `collect_finished_page_refs()`（约 1990 行）把「上一页完整成品大图 `prev_blank`」作为 tier-3 i2i 参考喂给模型：`if panel_continuity and len(refs) < max_refs and prev_blank: _add(prev_blank)`。调用点 `core/pipelines/creative_comic.py:2476` 把 `prev_blank=prev_blank_path` 与 `panel_continuity=image_config.panel_continuity` 一并传入。
- **实证**：P4 的前一页 P3（c0001-p0000）已经是完全相同的「渡口 / 灯塔 / 雨夜 / 三人 / 右下捧乐谱女生 / 四只手」构图；P4 几乎逐像素复制 P3（缩略灰度均值 P3=85.6、P4=85.0）。整页大图把构图、人数、动作、场景整体照抄并逐页传染，错误一旦出现就累积放大。
- **次因 1**：`core/comic/voice.py` 的 `TIMELINE_PRESENT_LINE` 硬编码 `cool static reading-frame atmosphere; focus on the letter-reader now`，这是为书信体小说写的通用模板，对《渡口》这种非书信故事也每页无条件注入，诱导「读书 / 读信的人」与书本道具。
- **次因 2（后果，非独立根因）**：第二章分镜（plan c0001 P2）写的是「客栈内景 / 油灯 / 两人 / 托下巴」，成图却是第一章「渡口灯塔」——因为 prev_blank 复制，第二章没切场景。
- **规划层无责**：用纯函数重建 P4 的 `render_finished_page_prompt`，输出只声明 2 个角色（沈知意 / 少年）、带 `CRITICAL distinct characters: never render two look-alike copies`、无任何乐谱 / 看书动作。两张角色肖像是干净的单人半身像，不含捧书姿势。

**前三轮修复为何无效（≥3 次失败 → 架构问题）**：`0d1a5f7` 及工作区里「每页每角色只声明一次 / distinct characters」全是 prompt 文字补丁，压不过一张强 i2i 整页参考图，修错了层；`INKSTONE_PANEL_CONTINUITY=0` 是临时 shell 变量、未固化，`core/config.py:335` 默认就是「开」，当前 shell 里根本没有该变量，新 shell 一跑又回到开。「用整页成品图当参考 + 靠文字补丁对抗参考图」这套模式本身不稳，应停止第四次局部补丁。

## 2. 目标

- finished-page 渲染不再被上一页整页成品图条件化；P4 及同类页重渲染后回到「按 plan 构图、恰好 2 人、场景正确、无乐谱 / 四只手 / 多余女生」。
- 去掉 voice present 模板对非书信故事的强「读信人」诱导。
- 改装配后 render key 变化，自动触发重渲染，不需手动清缓存。

## 3. 非目标（相对上一轮口述方案 2 的收窄，需用户知情）

- **不做「精简负向书本词」**：上一轮口述方案 2 含此项，取证后取消。理由：(a) `tests/test_page_negative_prompt.py::test_negative_prompt_covers_diegetic_props` 硬断言 `newspaper / book pages / signposts / posters` 在负向里，系真实「报纸标题 WIENER MORGENPOST 乱码」bug 的修复，删则回归；(b) 书本只是多余人物手里的道具、非根因；(c) 这些负向词防的是「可读文字」，不是防「画书」。
- **不动 `collect_finished_page_refs` 函数体、不动 `test_the_previous_blank_is_gated_by_panel_continuity`**：它是公共 API，tier-3 能力保留；本修复只在 finished-page 调用点恒定关闭该通道。panel_compose 旧路径不动。
- **不动 `voice_timeline_plan_instructions`**（规划层，实测 P4 plan 正确、未被带偏，YAGNI）。
- **不改 `INKSTONE_PANEL_CONTINUITY` 默认值、不改 `config.py`**：本修复在调用点固化、与环境变量解耦；panel_compose 仍读原开关。
- **不引入渲染后视觉 QC 门**（方案 3 内容，新增视觉调用与成本，YAGNI，本轮不做）。
- **不动 prompt 的角色声明 / `CRITICAL distinct characters`**（已提交 `0d1a5f7`，保留）。
- 不碰工作区既有未提交改动（vision_placement 等）；不 commit。

## 4. 设计

### 4.1 主修：finished-page 调用点恒定关闭 prev_blank 通道

`core/pipelines/creative_comic.py` 的 finished-page 渲染分支（约 2459-2494）：

- 删除 `prev_blank_path = previous_page_blank(...)`（本分支不再需要上一页图）。
- `collect_finished_page_refs(...)`：去掉 `prev_blank=prev_blank_path`；`panel_continuity` 固定为 `False`，不再取 `image_config.panel_continuity`。
- `finished_page_ref_policy(panel_continuity=False)` 同步固定。
- 加注释说明：独立构图的漫画页不应被上一页像素整体条件化（会复制人数 / 动作 / 场景）。
- 效果：`finished_page_ref_policy` 的 digest 因 `panel_continuity` True→False 改变 → `render_page_action_key` 变 → P4 等缓存页失效 → resume 时按新参考图（`sheet_ref_local`=None + 沈知意 / 少年两张权威肖像）重渲染，整页大图不再进入。

**备选实现（不推荐）**：在函数层彻底移除 tier-3，需同步改 `collect_finished_page_refs` 签名 / 函数体、`finished_page_ref_policy` 的 selector、删 `test_the_previous_blank_is_gated_by_panel_continuity`，并评估 panel_compose 是否受影响。改动更大、波及既有契约，故选「调用点关闭」。

### 4.2 次修：voice present 模板中性化（最小版）

`core/comic/voice.py` 的 `TIMELINE_PRESENT_LINE`：改为不含 `letter-reader / reading-frame` 的中性舞台描述，保留 `present` 字样（保 `test_timeline_prompt_lines_and_page_injection`）。

- 采用最小版（改 1 行常量），不做「按 bible 条件注入」的精细版（那要改 `timeline_prompt_lines` 签名 + 3 处调用点 + 测试，YAGNI）。
- 书信体氛围在规划层 `voice_timeline_plan_instructions` 仍有 `letter_reader` 提示，不受影响。
- 改后 present 页 prompt 文本变化，也会推动 render key 变化（叠加生效，无害）。

### 4.3 数据流 / 错误处理

- 不改变 provider 调用、CAS、`h_env`、resume、gate、budget 语义。
- `previous_page_blank` 函数保留（其它路径仍用；`test_identity_lockdown` 测它，不动）。
- 负向 prompt、`DIEGETIC_TEXT_LINE`、角色锁、lettering、vision_placement 全部保持现状。

## 5. 改动面

- `core/pipelines/creative_comic.py`（1 处分支，约 6-10 行）
- `core/comic/voice.py`（1 行常量）
- `tests/`（新增 / 改 2-3 个断言，见 §6）
- 本 spec

不动：`visual_bible.py`、`page_prompt.py`、`page_negative_prompt.py`、`config.py`、`env_snapshot.py`、`cas.py`、provider 层、prompt 角色锁、工作区既有未提交改动。

## 6. 测试（TDD 先行）

- 新 / 加强：`collect_finished_page_refs(plan, chars, bible, prev_blank="/tmp/prev.png", panel_continuity=False)` 的 refs 不含 prev_blank、仍含两张角色权威肖像（`test_the_previous_blank_is_gated_by_panel_continuity` 已覆盖 off 分支，补一条显式断言肖像仍在）。
- 新：一条「调用点回归」——断言 `creative_comic.py` 该 finished-page 分支的 `collect_finished_page_refs` / `finished_page_ref_policy` 不再引用 `image_config.panel_continuity`、不再传 `prev_blank`，防止日后被改回。
- 新 / 改 voice：`test_timeline_prompt_lines_and_page_injection` 增加断言 present 行不含 `letter-reader` / `reading-frame`；保留 `present` 与 past `memory` 断言。
- 回归：`test_visual_bible.py`（refs / policy 全组）、`test_page_negative_prompt.py`（确认书本词未被删、防护仍在）、`test_voice_timeline.py`、`test_finished_page_pipeline.py`、`test_vision*`、`test_identity_lockdown.py`。
- 全量 `uv run pytest` 退出码 0；按 AGENTS.md 执行 `uvx ruff format .`（工作区有并行未提交改动，格式化范围尽量只限本任务文件，避免波及）+ `ruff check`。
- 真实验收（用户既有偏好）：离线 resume 确认 P4 render key 变化、确实重渲染；真实重渲染 4 页，肉眼确认「两人 / 客栈内景 / 无乐谱 / 无四只手 / 无多余女生」，重导出纯图 `comic.pdf` 并给下载链接。

诚实说明：`_render_page_node` 无轻量单测夹具，主修正确性主要靠离线跑一次生成 + 真实验收；单测守住函数层契约与「调用点不被回改」。

## 7. 风险与权衡

- 主修靠 `panel_continuity` 常量 `False` 改变 key 触发重渲染；若 `stale_pages` / manifest 另有缓存命中分支，需确认 resume 确实重画而非复用 —— 实现时用一次离线 resume 验证 render.page 新 key。
- voice 文案变化会改所有 present 页的 prompt，可能让更多页重渲染、多花 API 配额；本就要治本，属可接受代价，但如实告知。
- 移除 prev_blank 后，跨页「风格 / 光线延续」只靠 style_guide + color bible + 角色肖像维持；对独立构图的漫画页是正确取舍，连续镜头感会略降，属有意权衡。
- 免费 flash 模型多角色仍非 100% 稳：本修复消除「整页复制」这一最强污染源，但不承诺根治模型偶发畸形（那是方案 3 视觉 QC 的兜底范围，本轮不做）。
- 工作区混有前几轮未提交改动，格式化 / 全量测试可能受其影响，逐项核对而非笼统归咎。

## 8. 实施顺序（计划要点；review 通过后由 writing-plans 展开为正式分步计划）

1. TDD：先写 / 改失败测试（voice 断言 + refs 契约 + 调用点回归）。
2. 改 `voice.py` 一行常量 → 绿。
3. 改 `creative_comic.py` finished-page 分支 → 绿。
4. 全量 `pytest` + `ruff` + `format`（限定本任务文件）。
5. 离线 resume 确认 P4 render key 变化、确实重渲染。
6. 真实重渲染 + 肉眼验收 P4 + 重导出纯图 `comic.pdf`。
7. 不 commit，结论里问用户是否提交。
