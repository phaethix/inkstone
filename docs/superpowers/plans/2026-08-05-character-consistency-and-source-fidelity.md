# 修复计划:角色一致性与原著忠实度

> 对应缺陷报告: [`2026-08-05-character-consistency-and-source-fidelity.md`](../../../.issue/2026-08-05-17_14-character-consistency-and-source-fidelity.md)
> 分支(待创建): `fix/character-consistency-and-source-fidelity`
> 目标:让 LLM 在提取/规划阶段必须引用原文证据,并保证 `face_lock` 不会被后续 reconcile 覆盖。

## 1. 修复目标

| ID | 目标 | 可量化验收 |
|----|------|-----------|
| G1 | 角色外观字段全部带原文证据 | `Appearance` 100% 携带 `evidence`;空 evidence 在 L1 注入时打 `unverified=true` 警告 |
| G2 | `face_lock` 一旦设定不可被后续 reconcile 覆盖 | 单元测试验证:`_upsert_canon` 第二次传入相同 `canon_id` 时,既有 `face_lock` 字段值不变 |
| G3 | 提示词强制要求逐字引用原文 | 测试验证 `SYSTEM_PROMPT` 文本包含 "verbatim" 与 "must quote" 关键词 |
| G4 | 反虚构校验:quoted text 必须在 source 中出现 | 测试验证:伪造 quote 触发 `unverified=true`;真实 quote 通过 |
| G5 | 不破坏现有测试套件 | `pytest tests/test_identity.py tests/test_screenwriter.py tests/test_visual_bible.py tests/test_consistency_l1.py` 全绿 |

## 2. 修改范围(总览)

```
core/schemas.py        — 新增 EvidenceQuote + Appearance.appearance_evidence
core/screenwriter.py   — SYSTEM_PROMPT 追加 evidence 强制要求
core/comic/identity.py — ensure_character_l1 追加证据注入 + 反虚构校验
core/comic/visual_bible.py — _upsert_canon: face_lock 只读约束
tests/test_schemas.py  — 新增 EvidenceQuote + Appearance 测试
tests/test_screenwriter.py — 验证 SYSTEM_PROMPT 包含 evidence 要求
tests/test_visual_bible.py — 验证 face_lock 不可被覆盖
tests/test_identity.py — 验证反虚构校验路径
```

**不在本次范围**(避免扩展):
- 多肖像锚定(PR-B)
- 全量别名检测(PR-C)
- L2/L3 渲染端改动
- CJK 字符叠加

## 3. 修改思路(按文件)

### 3.1 [`core/schemas.py`](../../../core/schemas.py:1)

**位置**:`Appearance` 类,line 390 附近。

**变更**:
1. 在 `Appearance` 上方新增 `EvidenceQuote` 数据结构:
   ```python
   class EvidenceQuote(BaseModel):
       field: str          # 对应 Appearance 的字段名,如 "hair"
       quote: str          # ≤ 25 字原文片段
       offset: int         # 在 source text 中的字符偏移

       @field_validator("quote")
       @classmethod
       def _len(cls, v: str) -> str:
           if len(v) > 25:
               raise ValueError("evidence quote must be ≤ 25 chars")
           return v
   ```
2. 在 `Appearance` 字段列表中追加:
   ```python
   appearance_evidence: list[EvidenceQuote] = Field(default_factory=list)
   ```

**兼容性**:`default_factory=list` 保证旧数据反序列化不报错。

### 3.2 [`core/screenwriter.py`](../../../core/screenwriter.py:1)

**位置**:`SYSTEM_PROMPT`,line 36 附近。

**变更**:在现有 prompt 末尾追加:
```
When describing character appearance (hair, outfit, body type,
distinguishing features), every claim MUST be backed by a verbatim
quote from the source text. Provide the exact substring (≤ 25
Chinese chars or ≤ 10 English words) and the offset. Do not invent
details that the text does not state. If the source is silent,
output an empty evidence list and mark the field as unverified.
```

### 3.3 [`core/comic/identity.py`](../../../core/comic/identity.py:1)

**位置**:`ensure_character_l1`,line 84。

**变更**:
1. 函数签名新增可选参数 `source_text: str | None = None`。
2. 在组装 L1 prompt 前,注入 `appearance_evidence` 列表(若 source_text 提供则用 `EvidenceQuote.quote` 做 `quote in source_text` 校验;校验失败的条目记录到 `unverified: list[str]`,并在 prompt 中以 `⚠ unverified` 标记)。
3. 若完全无 evidence,在 prompt 注释 `⚠ no source evidence; using generic placeholder`。

**依赖**:需要从上层 pipeline 传入 `source_text`(见 3.5 兼容性说明)。

### 3.4 [`core/comic/visual_bible.py`](../../../core/comic/visual_bible.py:892)

**位置**:`_upsert_canon`,line 892–925。

**变更**:在写入 `face_lock` 之前加入保护:
```python
existing = self.characters.get(canon_id)
if existing and existing.face_lock and incoming.face_lock is None:
    # preserve locked value; incoming did not explicitly set
    incoming.face_lock = existing.face_lock
elif existing and existing.face_lock and incoming.face_lock and \
     not _same_face_descriptor(existing.face_lock, incoming.face_lock):
    # incoming tried to mutate a locked face — keep original
    incoming.face_lock = existing.face_lock
```

其中 `_same_face_descriptor` 比较 normalized 字符串;首次设定或显式解锁(`incoming.face_lock is None` 之外的明确 None)放行。

### 3.5 兼容性说明

- `ensure_character_l1` 新参数 `source_text` 默认为 `None`,旧调用点零影响。
- LLM 偶尔漏填 `appearance_evidence` 不再崩溃,只标记 `unverified`。
- `face_lock` 只读约束是单向的:已锁定的不能被覆盖;未锁定的正常填充。

## 4. 验证方式

### 4.1 静态验证

```bash
python -m ruff check core/schemas.py core/screenwriter.py core/comic/identity.py core/comic/visual_bible.py
python -m mypy core/schemas.py core/comic/identity.py core/comic/visual_bible.py  # 如项目启用
```

### 4.2 单元测试(必跑)

```bash
cd /Users/huanyuli/github.com/inkstone
python -m pytest \
  tests/test_schemas.py \
  tests/test_screenwriter.py \
  tests/test_visual_bible.py \
  tests/test_identity.py \
  tests/test_consistency_l1.py \
  -v
```

新增用例清单:
| 文件 | 用例 | 断言 |
|------|------|------|
| `test_schemas.py` | `test_appearance_evidence_default_empty` | `Appearance().appearance_evidence == []` |
| `test_schemas.py` | `test_evidence_quote_too_long` | 26 字 quote 抛 `ValidationError` |
| `test_screenwriter.py` | `test_system_prompt_requires_evidence` | `SYSTEM_PROMPT` 包含 "verbatim" 与 "evidence" |
| `test_visual_bible.py` | `test_face_lock_not_overwritten` | 第二次 `_upsert_canon` 改 `face_lock` 失败,值保持首次 |
| `test_identity.py` | `test_unverified_mark_for_fabricated_quote` | 假 quote 被标记 `unverified` |

### 4.3 端到端冒烟(可选,需 API)

```bash
python examples/first_panel.py  # 或 examples/generate_comic.py
# 观察:
#   - state.json 中 appearance_evidence 字段非空
#   - 重新跑 reconcile,face_lock 不变
```

## 5. 回滚策略

所有变更集中在 4 个文件,且均为增量式:
- `schemas.py` 新增字段 `default_factory=list`,删除字段即恢复
- `screenwriter.py` prompt 追加段可整体删除
- `identity.py` 新增可选参数,删除函数内 if 分支即可
- `visual_bible.py` 改动是局部 if 守卫,删除后行为回到现状

无需迁移脚本;旧 state.json 自动 `default_factory=list` 兼容。

## 6. 执行顺序

1. 新建分支: `git checkout -b fix/character-consistency-and-source-fidelity`
2. 修改 [`core/schemas.py`](../../../core/schemas.py) → 新增 `EvidenceQuote` 与 `appearance_evidence`
3. 修改 [`core/screenwriter.py`](core/screenwriter.py) → SYSTEM_PROMPT 追加
4. 修改 [`core/comic/visual_bible.py`](core/comic/visual_bible.py) → `_upsert_canon` 加守卫
5. 修改 [`core/comic/identity.py`](core/comic/identity.py) → evidence 注入 + 校验
6. 新增/调整测试用例
7. 跑测试套件 → 全绿
8. `git add` + commit(若用户授权)

## 7. 风险登记

| 风险 | 等级 | 缓解 |
|------|------|------|
| LLM 漏填 evidence 导致所有角色 `unverified` | 中 | prompt 明确说明空 evidence 合法;不强制 fail |
| `face_lock` 守卫阻挡了显式解锁意图 | 低 | 守卫仅在已有 `face_lock` 时生效;首次设定不受影响 |
| 反虚构校验在分词场景误判 | 中 | 使用 `quote in source_text` 简单子串匹配,误判率低;若失败回退到 `unverified=true` 而非 raise |

---

**完成标准**:步骤 2 文档已落盘;修复范围/顺序/验证方式明确;下一步为新建分支(步骤 3)。