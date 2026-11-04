# 图像调用耗时结构化落盘（可观测性）设计

日期：2026-11-04
状态：待用户 review（brainstorming User Review Gate）

## 1. 背景与现状（取证修正）

- `core/perf.py` 的 `PerfCollector` 已存在；`core/pipelines/creative_comic.py` 对 segment / extract / portrait / page / page_plan / storyboard / page_script / panel / layout / export 用 `perf.measure` 做阶段级墙钟计时，末尾 `perf.log_summary()` 打印到 stdout 并以 DEBUG 记日志。
- 因此「完全没有观测」并不准确：阶段级计时已有。真实缺口是三点：
  1. 计时只在 stdout/日志，不落盘、非机器可读、无法跨 run 对比；
  2. label 是聚合的（"page" / "panel"），无法与具体 `stage` / `key` 的单次调用关联；
  3. 成功但经历过 503/429 重试的调用，其重试代价没有记录（`core/api/error_collector.py` 只在失败时把 `retry_count` 写到 `logs/errors.jsonl`）。
- `runs.jsonl`（`core/comic/runs.py`）是 append-only 审计日志，逐条记 intent / failure / gate，带 `prompt_hash` 与 `ts`，不写 prompt 与错误正文；resume 只读 manifest 与 state.json，不回放它。

## 2. 目标

每次图像调用在 `runs.jsonl` 留一条结构化结果，使「调并发 / 调限速 / 换 provider 到底有没有用」可用数据验证。第一批只做最小闭环（YAGNI）。

## 3. 非目标

- 不改 prompt、模型、参考图、`state.json` 的 CAS key、`h_env` 快照、任何生成字节。
- 不改断点续跑 / §8 不变式 / 预算 / gate / 进度语义。
- 不动 `perf.py` 的现有阶段计时（避免回归；该文件目前无测试）。
- 不改 `retryable_post`、两个 provider、`ImageProvider` 抽象、FakeImage 夹具的签名。
- 不采集 prompt / 错误正文，沿用 hash 与脱敏约定。
- 不做自适应并发、规划/渲染流水化、跨项目 CAS（后续轮次）。
- 不碰工作区里 banner 与「统一 CLI / dotenv」那组他人未提交改动；不 commit。

## 4. 设计（最小版）

### 4.1 新记录类型

`core/comic/runs.py` 新增 `image_result(output_dir, *, stage, key, duration_ms, status)`，复用现有 `append_run`（已带 flush + fsync），写一条：

```json
{"kind":"result","stage":"render","key":"c0000-p0000","duration_ms":42380,"status":"ok","ts":"..."}
```

只写数值与已有字段，不写正文。新增 `kind` 不得影响现有读取方（gate / ledger / budget / review_packet 按字段过滤各自 kind）；实现时用 TDD + 全量测试核对。

### 4.2 计时落点

`core/pipelines/creative_comic.py` 三处图像调用（portrait ≈1899 / page ≈2513 / panel ≈2918），在 `image_intent` 之后用 `time.monotonic()` 包住 `generate_single_image`：

- 成功：`image_result(..., status="ok", duration_ms=round(elapsed*1000))`。
- 失败：现有 `image_failure` 保持不变，另记一条 `status="error"` 的 result（含 duration），然后照常 `raise` 或走既有 policy-skip 分支。
- `perf.measure` 保持原样不动，两处计时并存（monotonic 开销可忽略）。
- page 处是 `while True` 重试循环（size fallback / negative_prompt 重试）：按每次实际 provider 调用各记一条 result，不合并，以便看清重试代价。
- `duration_ms` 含限流等待、503 退避、上游排队，正是用户感知的墙钟时间，符合回答「为什么慢」的目的。

### 4.3 不进 CAS / 续跑

`core/comic/env_snapshot.py` 已把运行时旋钮列为「不改变生成字节」。本改动为纯观测，不参与 prompt / state / h_env，故 CAS key 不变、续跑命中不变。

### 4.4 错误处理

`append_run` 可能因 OSError 抛错；`image_result` 内部 try/except 兜底，采集失败只记 warning，绝不影响生成主流程（与 `error_collector` 的 catch-all 一致）。

## 5. 改动面

3 个文件：`core/comic/runs.py`（+1 函数）、`core/pipelines/creative_comic.py`（三处对称埋点）、`tests/`（TDD 用例，沿用 `tests/test_runs.py` 风格或新增 `test_image_result.py`）。不动 provider/api 层、不动 perf.py、不动 banner 与他人重构。

## 6. 测试（TDD 先行）

- `image_result` 写出字段完整、`duration_ms` 非负、`status` 取值、不写 prompt/正文；复用 `test_runs.py` 的脱敏断言风格。
- `append_run` 抛 OSError 时 `image_result` 吞掉异常、不影响主流程。
- 新增 `kind="result"` 后，gate / ledger / budget / review_packet 读取 runs.jsonl 的行为不变（全量 pytest 覆盖）。
- 三处埋点：用现有 FakeImage 夹具跑一次离线生成，断言每个 intent 都有对应 result、`duration_ms >= 0`。
- 按 AGENTS.md 写完执行 `uvx ruff format .`；全量 `pytest` 与 `ruff check` 退出码 0。
- 真实验收（用户既有偏好）：同一输入真实生成一次，确认 `runs.jsonl` 出现 result 行、成片与 `structure` 五项不受影响。

## 7. 后续增强（本轮不做）

- `retry_count` / 实际 HTTP attempts 落盘：需 `retryable_post` 透出 attempt 数、经 `generate_single_image` 的 `observe` 回调回吐，涉及 retry.py + 两个 provider + 抽象基类 + FakeImage 夹具（约 6–7 文件），风险中；待最小版验证后再开。
- 基于 result 数据做自适应并发 / 按 503 动态升降 `INKSTONE_IMAGE_CONCURRENCY`。

## 8. 风险与权衡

- 新增 kind 可能被未知的 runs.jsonl 消费方误判：用全量测试 + 关键词检索核对所有读取点。
- 三处埋点结构不同（尤其 page 的 while 循环）：逐处 TDD，避免漏记 / 重复记。
- `duration_ms` 为墙钟时间，含退避与限流，跨机器 / 跨网络不可直接比较；用于同环境前后对比。
