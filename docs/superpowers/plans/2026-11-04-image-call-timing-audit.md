# 图像调用耗时结构化落盘 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 每次图像调用在项目的 `runs.jsonl` 留一条带 `duration_ms` 的 `kind=result` 记录，使性能调参可用数据验证，且不改变任何生成字节与续跑语义。

**Architecture:** 在 `core/comic/runs.py` 增加一个复用 `append_run` 的 `image_result(...)` 写函数；在 `core/pipelines/creative_comic.py` 三处 `generate_single_image` 调用（portrait / page / panel）用 `time.monotonic()` 包一层计时，成功与失败各记一条；不改 `core/perf.py`、不改 provider/api 层、不参与 CAS key 与 state。纯观测、旁路、失败不阻断主流程。

**Tech Stack:** Python 3.12、uv、pytest、ruff；现有 `core.comic.runs.append_run`（JSONL append + fsync）、`PerfCollector`（已存在，保持不动）、FakeChat/FakeImage 离线夹具。

## Global Constraints

- 写完代码后必须执行 `uvx ruff format .`（AGENTS.md 规则 1）。
- 不改 prompt、模型、参考图、`state.json` 的 CAS key、`h_env` 快照、任何生成字节。
- 不改断点续跑 / §8 不变式 / 预算 / gate / 进度语义。
- 不动 `core/perf.py`、`core/api/retry.py`、两个 provider、`ImageProvider` 抽象基类、FakeImage 夹具签名。
- 不采集 prompt / 错误正文；沿用 hash 与脱敏约定。
- 不碰工作区里 banner 与「统一 CLI / dotenv」那组他人未提交改动（含 `core/cli.py`、`core/banner.py`、`core/dotenv.py`、`tests/test_banner.py`、`tests/test_dotenv.py`、`web/server.py`、`pyproject.toml`、被删的 `scripts/*`、两份 `docs/...design.md`）。
- 不 `git commit`、不 `git push`（spec review 承诺；工作区有 banner 组测试不绿 + 他人重构）。
- 已知基线：当前全量 pytest 存在 1 个无关失败 `tests/test_comment_style.py::test_python_sources_have_no_section_banner_comments`（来自 banner 组）；验证时须区分「本改动无关」与「本改动引入」，用隔离/排除法证明，不得把该失败算到本任务头上。

## File Structure

- Modify `core/comic/runs.py`：新增 `image_result(...)`，唯一职责是把一条结果记录追加到 `runs.jsonl`。
- Modify `core/pipelines/creative_comic.py`：新增 `import time`；把 `image_result` 加入 `from core.comic.runs import ...`；三处调用点加计时包装。
- Create `tests/test_image_result.py`：新函数的单元测试 + 失败不阻断测试。
- Modify `tests/test_creative_comic.py`：更新 `test_creative_comic_generates_and_resumes` 里遍历全部行的断言（见 Task 4）。
- 计划文件：`docs/superpowers/plans/2026-11-04-image-call-timing-audit.md`（本文件）。

---

### Task 1: `image_result` 写函数

**Files:**
- Create: `tests/test_image_result.py`
- Modify: `core/comic/runs.py:42`（在 `image_intent` 之后、`image_failure` 之前插入）

**Interfaces:**
- Consumes: `core.comic.runs.append_run(output_dir: Path, record: dict) -> None`（已存在）；`_now_iso() -> str`（已存在）。
- Produces: `image_result(output_dir: Path, *, stage: str, key: str, duration_ms: int, status: str) -> None`。后续任务仅依赖此函数名与签名。

- [ ] **Step 1: 写失败测试**

Create `tests/test_image_result.py` with:

```python
"""Structured per-image-call timing records in runs.jsonl."""

import json
from pathlib import Path

from core.comic.runs import image_result


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_image_result_writes_a_structured_row(tmp_path):
    image_result(tmp_path, stage="render", key="c0000-p0000", duration_ms=42380, status="ok")
    rows = _rows(tmp_path / "runs.jsonl")
    assert len(rows) == 1
    assert rows[0]["kind"] == "result"
    assert rows[0]["stage"] == "render"
    assert rows[0]["key"] == "c0000-p0000"
    assert rows[0]["duration_ms"] == 42380
    assert rows[0]["status"] == "ok"
    assert rows[0]["ts"]


def test_image_result_writes_no_prompt_or_error_text(tmp_path):
    image_result(tmp_path, stage="portrait", key="沈知意", duration_ms=0, status="error")
    text = (tmp_path / "runs.jsonl").read_text(encoding="utf-8")
    assert "secret provider text" not in text
    assert "draw this" not in text
    row = json.loads(text.splitlines()[0])
    assert set(row) == {"kind", "stage", "key", "duration_ms", "status", "ts"}


def test_image_result_appends_without_overwriting(tmp_path):
    image_result(tmp_path, stage="render", key="p0", duration_ms=1, status="ok")
    image_result(tmp_path, stage="render", key="p1", duration_ms=2, status="error")
    rows = _rows(tmp_path / "runs.jsonl")
    assert [r["key"] for r in rows] == ["p0", "p1"]
    assert [r["status"] for r in rows] == ["ok", "error"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_image_result.py -v`
Expected: FAIL with `ImportError: cannot import name 'image_result'`.

- [ ] **Step 3: 最小实现**

Insert into `core/comic/runs.py` after `image_intent` (line 42 block ends ~line 54), before `image_failure`:

```python
def image_result(output_dir: Path, *, stage: str, key: str, duration_ms: int, status: str) -> None:
    """Record the wall-clock outcome of one image call (no prompt or error text)."""
    try:
        append_run(
            output_dir,
            {
                "kind": "result",
                "key": key,
                "stage": stage,
                "duration_ms": duration_ms,
                "status": status,
                "ts": _now_iso(),
            },
        )
    except OSError:
        logging.getLogger(__name__).warning("could not append image_result row", exc_info=True)
```

and add `import logging` at the top of `core/comic/runs.py` (after `import json`).

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_image_result.py -v`
Expected: 3 PASS.

- [ ] **Step 5: 格式化**

Run: `uvx ruff format .`

---

### Task 2: 采集失败不阻断主流程

**Files:**
- Modify: `tests/test_image_result.py`
- Modify: `core/comic/runs.py`（`image_result` 已含 try/except，本任务补测试断言）

**Interfaces:**
- Consumes: `image_result(...)`（Task 1）；`unittest.mock.patch`（stdlib）。
- Produces: 无新接口；保证 `image_result` 的异常隔离契约被测试锁定。

- [ ] **Step 1: 写失败测试**

Append to `tests/test_image_result.py`:

```python
from unittest.mock import patch


def test_image_result_swallows_append_errors(tmp_path):
    with patch("core.comic.runs.append_run", side_effect=OSError("disk full")):
        image_result(tmp_path, stage="render", key="p0", duration_ms=5, status="ok")
    assert not (tmp_path / "runs.jsonl").exists()
```

Also assert `duration_ms` is non-negative in the caller contract by adding to `test_image_result_writes_a_structured_row`:
```python
    assert rows[0]["duration_ms"] >= 0
```

- [ ] **Step 2: 跑测试确认通过（实现已在 Task 1 完成）**

Run: `uv run pytest tests/test_image_result.py -v`
Expected: 4 PASS. If `test_image_result_swallows_append_errors` fails because `append_run` is not guarded, re-check Task 1 Step 3's try/except is present before proceeding.

- [ ] **Step 3: 格式化**

Run: `uvx ruff format .`

---

### Task 3: 三处调用点埋点（portrait / page / panel）

**Files:**
- Modify: `core/pipelines/creative_comic.py:21-30`（import 区）、`:121`（runs import）、`:1899-1914`（portrait）、`:2513-2531`（page）、`:2918-2927`（panel）

**Interfaces:**
- Consumes: `image_result(output_dir, *, stage, key, duration_ms, status)`（Task 1）；`time.monotonic()`；现有 `image_intent` / `image_failure` / `perf.measure` / `generate_single_image`。
- Produces: 运行期 `runs.jsonl` 中每条 `intent` 后紧跟一条（成功或失败）`result`，`stage` 与 `key` 与对应 `intent` 一致。

- [ ] **Step 1: 加 import**

At `core/pipelines/creative_comic.py`: add `import time` directly after `import logging` (line 23). Change line 121 from
```python
from core.comic.runs import bind_audit, clear_audit, image_failure, image_intent
```
to
```python
from core.comic.runs import bind_audit, clear_audit, image_failure, image_intent, image_result
```

- [ ] **Step 2: portrait 埋点失败测试**

Create `tests/test_image_timing_pipeline.py`:

```python
"""Every image intent gets a result row with a sane duration."""

import asyncio
import json

from core.pipelines.creative_comic import creative_comic
from tests.test_creative_comic import FakeChat, FakeImage
from tests.test_finished_page_pipeline import _fake_export_pdf
from unittest.mock import patch


def _rows(path):
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()]


@patch("core.comic.export.ExportEngine.export_pdf", _fake_export_pdf)
def test_every_image_intent_has_a_result(tmp_path):
    src = "第一章\n方鸿渐在甲板上。\n第二章\n方鸿渐在读书。"
    asyncio.run(creative_comic(src, output_dir=str(tmp_path), chat=FakeChat(), image=FakeImage()))
    rows = _rows(tmp_path / "runs.jsonl")
    intents = [r for r in rows if r["kind"] == "intent" and r["stage"] in {"portrait", "render"}]
    results = [r for r in rows if r["kind"] == "result"]
    assert intents, "expected image intents"
    assert len(results) == len(intents)
    assert all(r["status"] == "ok" for r in results)
    assert all(isinstance(r["duration_ms"], int) and r["duration_ms"] >= 0 for r in results)
    assert {(r["stage"], r["key"]) for r in results} == {(r["stage"], r["key"]) for r in intents}
```

- [ ] **Step 3: 跑测试确认失败**

Run: `uv run pytest tests/test_image_timing_pipeline.py -v`
Expected: FAIL (`len(results)` is 0, `len(intents)` is 3).

- [ ] **Step 4: portrait 实现**

Replace the portrait try/except block (lines ~1903-1914):

```python
                    try:
                        with perf.measure("portrait"):
                            out = await image.generate_single_image(
                                sent,
                                reference_image_paths=refs,
                                size="1024x1024",
                            )
                    except Exception as exc:  # noqa: BLE001 — log the class, then keep the portrait error
                        image_failure(output_dir, stage="portrait", key=name, error=exc)
                        raise
```

with:

```python
                    t0 = time.monotonic()
                    try:
                        with perf.measure("portrait"):
                            out = await image.generate_single_image(
                                sent,
                                reference_image_paths=refs,
                                size="1024x1024",
                            )
                    except Exception as exc:  # noqa: BLE001 — log the class, then keep the portrait error
                        image_result(
                            output_dir,
                            stage="portrait",
                            key=name,
                            duration_ms=round((time.monotonic() - t0) * 1000),
                            status="error",
                        )
                        image_failure(output_dir, stage="portrait", key=name, error=exc)
                        raise
                    else:
                        image_result(
                            output_dir,
                            stage="portrait",
                            key=name,
                            duration_ms=round((time.monotonic() - t0) * 1000),
                            status="ok",
                        )
```

- [ ] **Step 5: panel 实现**

Replace the panel try/except block (lines ~2920-2927):

```python
try:
    with perf.measure("panel"):
        out = await image.generate_single_image(prompt, reference_image_paths=refs, size=panel.size)
except Exception as exc:  # noqa: BLE001 — log the class, then keep the panel error
    image_failure(output_dir, stage="render", key=state_key, error=exc)
    raise
```

with:

```python
t0 = time.monotonic()
try:
    with perf.measure("panel"):
        out = await image.generate_single_image(prompt, reference_image_paths=refs, size=panel.size)
except Exception as exc:  # noqa: BLE001 — log the class, then keep the panel error
    image_result(
        output_dir,
        stage="render",
        key=state_key,
        duration_ms=round((time.monotonic() - t0) * 1000),
        status="error",
    )
    image_failure(output_dir, stage="render", key=state_key, error=exc)
    raise
else:
    image_result(
        output_dir,
        stage="render",
        key=state_key,
        duration_ms=round((time.monotonic() - t0) * 1000),
        status="ok",
    )
```

- [ ] **Step 6: page 实现**

Replace the page try/except opening (lines ~2519-2531):

```python
                        try:
                            async with image_semaphore:
                                with perf.measure("page"):
                                    out = await image.generate_single_image(
                                        prompt,
                                        reference_image_paths=refs,
                                        size=active_size,
                                        negative_prompt=negative_prompt,
                                    )
                            break
                        except Exception as exc:  # noqa: BLE001 — preserve policy skip behavior
                            image_failure(output_dir, stage="render", key=state_key, error=exc)
```

with:

```python
                        t0 = time.monotonic()
                        try:
                            async with image_semaphore:
                                with perf.measure("page"):
                                    out = await image.generate_single_image(
                                        prompt,
                                        reference_image_paths=refs,
                                        size=active_size,
                                        negative_prompt=negative_prompt,
                                    )
                        except Exception as exc:  # noqa: BLE001 — preserve policy skip behavior
                            image_result(
                                output_dir,
                                stage="render",
                                key=state_key,
                                duration_ms=round((time.monotonic() - t0) * 1000),
                                status="error",
                            )
                            image_failure(output_dir, stage="render", key=state_key, error=exc)
                        else:
                            image_result(
                                output_dir,
                                stage="render",
                                key=state_key,
                                duration_ms=round((time.monotonic() - t0) * 1000),
                                status="ok",
                            )
                            break
```

IMPORTANT: the `try` block's trailing `break` must move into the `else` branch (shown above); the rest of the `except` body (content-policy skip, tombstones, `continue`) stays unchanged after the new `image_result(...)` call. Each `while True` iteration (size fallback / negative_prompt retry) records its own `result`, which is the intended behavior.

- [ ] **Step 7: 跑测试确认通过**

Run: `uv run pytest tests/test_image_timing_pipeline.py -v`
Expected: PASS.

- [ ] **Step 8: 格式化**

Run: `uvx ruff format .`

---

### Task 4: 更新受影响的既有断言 + 全量验证

**Files:**
- Modify: `tests/test_creative_comic.py:109-131`（`test_creative_comic_generates_and_resumes`）
- Verify-only: `tests/test_d2_pipeline.py`, `tests/test_finished_page_pipeline.py`, `tests/test_gate.py`, `tests/test_budget.py`, `tests/test_shared_state.py`, `core/comic/review_packet.py`, `core/comic/ledger.py`

**Interfaces:**
- Consumes: Task 1-3 的实现。
- Produces: 全量测试除已知 banner 用例外全绿；新增 `result` 行不破坏按 `kind` 过滤的消费方。

- [ ] **Step 1: 更新遍历全部行的断言**

In `tests/test_creative_comic.py::test_creative_comic_generates_and_resumes`, change:
```python
    intents = [json.loads(line)["stage"] for line in logged.splitlines()]
```
to:
```python
    intents = [
        json.loads(line)["stage"]
        for line in logged.splitlines()
        if json.loads(line)["kind"] == "intent"
    ]
```
The subsequent exact list assertion stays unchanged (9 intents). The resume assertion `assert (tmp_path / "runs.jsonl").read_text(...) == logged` stays unchanged and still holds: a resume that generates nothing writes no new intent or result rows.

- [ ] **Step 2: 核对消费方不受影响（只读确认，不改代码）**

Confirm by reading: `core/comic/review_packet.py:170` filters `kind in {"gate","stop","rebuild"}`; `core/comic/ledger.py:117` checks `kind == "stop"`; `tests/test_finished_page_pipeline.py:363` and `tests/test_d2_pipeline.py` filter `kind == "intent"`. None match `result`. `tests/test_gate.py`, `tests/test_budget.py`, `tests/test_shared_state.py` operate on isolated gate/budget logs that never run `creative_comic`, so their `len(...)` assertions are unaffected.

- [ ] **Step 3: 全量 pytest（排除已知 banner 失败）**

Run: `uv run pytest -q --deselect tests/test_comment_style.py::test_python_sources_have_no_section_banner_comments`
Expected: exit code 0, all remaining tests PASS.

- [ ] **Step 4: 证明 banner 失败与本改动无关**

Run: `uv run pytest tests/test_comment_style.py::test_python_sources_have_no_section_banner_comments -q`
Expected: FAIL with the same `core/banner.py:25` / `:45` offenders (pre-existing, from the untouched banner group). This is the known baseline; do not fix it.

- [ ] **Step 5: ruff check + 格式化确认**

Run: `uv run ruff check .` (expect `All checks passed!`), then `uvx ruff format .`, then `uv run pytest -q --deselect tests/test_comment_style.py::test_python_sources_have_no_section_banner_comments` again to confirm format did not break anything.

- [ ] **Step 6: 确认未误改他人文件**

Run: `git status --short`
Expected: only `core/comic/runs.py`, `core/pipelines/creative_comic.py`, `tests/test_image_result.py`, `tests/test_image_timing_pipeline.py`, `tests/test_creative_comic.py`, `docs/superpowers/specs/2026-11-04-image-call-timing-audit-design.md`, `docs/superpowers/plans/2026-11-04-image-call-timing-audit.md` appear as new/changed for this task, PLUS the pre-existing banner/reconstruction group. No `git add`/`git commit`.

---

### Task 5: 真实生成验收

**Files:**
- Verify-only: `comic_out/live-timing-audit/runs.jsonl` (new output dir), `comic_out/live-timing-audit/pages/*.png`, `comic_out/live-timing-audit/comic.pdf`

**Interfaces:**
- Consumes: Task 1-4 的实现；官方 `./scripts/start.sh` + `.env` 的真实 `AGNES_API_KEY`（若 `scripts/start.sh` 因他人重构被删，改用 `uv run python -m core.cli generate ...` 并显式 `set -a; source .env; set +a`，因为统一入口 `core.cli.main()` 会 `load_dotenv()`）。
- Produces: 一份真实成片，其 `runs.jsonl` 含 `result` 行，且 `structure` 五项全过。

- [ ] **Step 1: 真实生成（用户既有偏好：不只 mock）**

Run (真实联网、消耗免费档配额、约十几分钟):
```bash
./scripts/start.sh generate examples/scene1.txt --out comic_out/live-timing-audit --density A --format page --yes 2>&1
```
If `scripts/start.sh` is missing, run:
```bash
set -a; source .env; set +a; uv run python -m core.cli generate examples/scene1.txt --out comic_out/live-timing-audit --density A --format page --yes 2>&1
```
Expected: exit code 0.

- [ ] **Step 2: 核验 result 行**

Run:
```bash
uv run python -c "
import json
from pathlib import Path
rows=[json.loads(l) for l in open('comic_out/live-timing-audit/runs.jsonl') if l.strip()]
res=[r for r in rows if r['kind']=='result']
ints=[r for r in rows if r['kind']=='intent' and r['stage'] in {'portrait','render'}]
print('intents',len(ints),'results',len(res))
print('all non-negative',all(isinstance(r['duration_ms'],int) and r['duration_ms']>=0 for r in res))
print('statuses',sorted({r['status'] for r in res}))
print('max duration_ms',max((r['duration_ms'] for r in res),default=0))
"
```
Expected: `results == intents`, `all non-negative True`, statuses subset of `{"ok","error"}`, `max duration_ms` in the tens-of-thousands (matches the observed 40-70s per image).

- [ ] **Step 3: 核验成片与结构**

Run: `uv run inkstone structure --out comic_out/live-timing-audit`
Expected: 五项 `pass`、`chapter: validated`、exit code 0; also confirm the PDF/pages exist via a short PIL/pypdf check.

- [ ] **Step 4: 结论**

Report: result rows appear for every real image call, durations are plausible, artifacts valid, no regression in structure. Note that `retry_count` is intentionally NOT yet captured (deferred per spec §7).

---

## Self-Review

**Spec coverage:** §4.1 `image_result` 字段 -> Task 1；§4.2 三处计时落点 + 失败分支 + page while 循环 -> Task 3；§4.3 不进 CAS/续跑 -> 由"不改 state/h_env"约束 + Task 4 的 resume 断言覆盖；§4.4 错误隔离 -> Task 2；§6 TDD/格式化/全量/真实验收 -> Task 1-5；§7 retry_count 留后续 -> Global Constraints + Task 5 Step 4 显式说明。全部覆盖。

**Placeholder scan:** 无 TBD/TODO/"appropriate handling"；每个 code step 含完整代码块；page 的 `while True` 与 `break` 移动有明确指令。通过。

**Type consistency:** `image_result(output_dir, *, stage, key, duration_ms: int, status: str)` 在 Task 1 定义，Task 3 三处调用参数名与顺序完全一致；`status` 取值 `ok`/`error` 在 Task 1 测试、Task 3 实现、Task 5 核验三处一致。通过。
