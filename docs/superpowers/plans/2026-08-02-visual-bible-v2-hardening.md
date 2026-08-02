# Visual Bible v2 Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden Visual Bible so projects like 9910 stop wrong merges, English-prose identities, empty locks, character-sheet page insets, and missing panel character refs.

**Architecture:** Add detectors/guards/sanitize/lock-fill/backfill in `visual_bible.py`; strengthen reconcile prompts; harden finished-page prompt; bump fingerprint to `bible_v2` and sanitize on resume.

**Tech Stack:** Python ≥3.10, Pydantic v2, existing chat tools, pytest, ruff.

**Spec:** `docs/superpowers/specs/2026-08-02-visual-bible-v2-hardening-design.md`

## Global Constraints

- English code, comments, commits.
- No new hard dependencies.
- Phase C sheet still unused.
- Do not enable L3 by default.
- Old state without bible still loads; sanitize is no-op when bible is None except illegal CharacterAsset cleanup may still run.
- TDD per task; commit per task.
- Prefer extending `core/comic/visual_bible.py` over new modules unless file exceeds ~600 lines meaningfully.

## File map

| File | Responsibility |
|---|---|
| `core/comic/visual_bible.py` | Illegal names, role guard, lock fill, sanitize, backfill_panel_characters |
| `core/screenwriter.py` | Reconcile prompt rules for v2 |
| `core/comic/page_prompt.py` | Anti-sheet + period wardrobe lines |
| `core/pipelines/creative_comic.py` | Sanitize on resume; backfill before render; `bible_v2` fingerprint |
| `tests/test_visual_bible_v2.py` | New unit tests |
| `tests/test_visual_bible_prompt.py` / `test_finished_page_pipeline.py` | Prompt + fingerprint updates |

---

### Task 1: Illegal names + role-incompatible merge guard + lock fill

**Files:**
- Modify: `core/comic/visual_bible.py`
- Create: `tests/test_visual_bible_v2.py`

**Interfaces:**
- `is_illegal_character_name(name: str) -> bool`
- `roles_incompatible(role_a: str, role_b: str) -> bool`
- `normalize_face_lock(text: str) -> str` — strip outfit words
- `ensure_stage_locks(stage: CharacterStage, *, canon_face: str, style_hint: str = "") -> CharacterStage`
- `ensure_canon_locks(canon: CharacterCanon, style_hint: str = "") -> CharacterCanon`
- Gate high merges in `apply_reconcile` via `roles_incompatible`; demote to needs_review
- Reject illegal portrait_key → `f"{canonical}@{stage}"`

- [ ] **Step 1: Failing tests**

```python
# tests/test_visual_bible_v2.py
from core.comic.visual_bible import (
    apply_reconcile,
    is_illegal_character_name,
    roles_incompatible,
    ensure_canon_locks,
)
from core.schemas import (
    CharacterAsset,
    CharacterCanon,
    CharacterStage,
    ColorBible,
    ProjectState,
    VisualBible,
    VisualBibleMerge,
    VisualBibleReconcileResult,
)


def test_illegal_english_prose_name():
    assert is_illegal_character_name(
        "41-year-old Viennese novelist, athletic elegant build, glossy dark hair"
    )
    assert not is_illegal_character_name("R（小说家）")
    assert not is_illegal_character_name("老约翰")


def test_roles_incompatible_mother_daughter_and_count_novelist():
    assert roles_incompatible("女主角母亲，寡妇", "女主角，信件叙述者")
    assert roles_incompatible("帝国伯爵，情人", "著名小说家")
    assert not roles_incompatible("著名小说家", "男主角作家")


def test_apply_reconcile_demotes_incompatible_high_merge():
    state = ProjectState(
        project_id="p",
        characters={
            "R（小说家）": CharacterAsset(name="R（小说家）", role="著名小说家"),
            "帝国伯爵": CharacterAsset(name="帝国伯爵", role="帝国伯爵，年长情人"),
        },
        visual_bible=VisualBible(
            version="bible_v1",
            style_guide="period vienna",
            color=ColorBible(palette=[], lighting="", forbidden=[]),
            characters={
                "R（小说家）": CharacterCanon(
                    canonical_name="R（小说家）",
                    role="著名小说家",
                    face_lock="handsome face",
                    stages=[
                        CharacterStage(
                            stage="adult",
                            outfit_lock="suit",
                            hair_lock="dark hair",
                            portrait_key="R（小说家）",
                        )
                    ],
                )
            },
        ),
    )
    out = apply_reconcile(
        state,
        VisualBibleReconcileResult(
            merges=[
                VisualBibleMerge(
                    alias="帝国伯爵",
                    canonical="R（小说家）",
                    confidence="high",
                    reason="wrong",
                )
            ]
        ),
    )
    assert "帝国伯爵" in out.characters
    assert any(s.new_name == "帝国伯爵" for s in out.needs_review)


def test_ensure_canon_locks_fills_empty_and_strips_outfit_from_face():
    canon = CharacterCanon(
        canonical_name="R",
        face_lock="handsome face, wearing athletic hoodie",
        stages=[
            CharacterStage(stage="default", outfit_lock="", hair_lock="", portrait_key="R")
        ],
    )
    fixed = ensure_canon_locks(canon, style_hint="early 20th century Vienna")
    assert "hoodie" not in fixed.face_lock.lower()
    assert fixed.stages[0].hair_lock
    assert fixed.stages[0].outfit_lock
```

- [ ] **Step 2: Run fail → implement → pass → commit**

```bash
.venv/bin/python -m pytest tests/test_visual_bible_v2.py -q --tb=short
git add core/comic/visual_bible.py tests/test_visual_bible_v2.py
git commit -m "feat: guard visual bible merges and require identity locks"
```

---

### Task 2: sanitize_visual_bible_state + illegal asset cleanup

**Files:**
- Modify: `core/comic/visual_bible.py`
- Modify: `tests/test_visual_bible_v2.py`

**Interfaces:**
- `sanitize_visual_bible_state(state: ProjectState) -> bool`
  - Fold/drop illegal-named characters
  - Drop incompatible aliases from canons and CharacterAsset.aliases
  - `ensure_canon_locks` on all canons
  - Set `version="bible_v2"`, `refresh_bible_hash`
  - Return True if any mutation

- [ ] **Step 1: Test**

```python
def test_sanitize_removes_prose_character_and_bad_alias():
    prose = "41-year-old Viennese novelist, athletic elegant build, glossy dark hair"
    state = ProjectState(
        project_id="p",
        characters={
            "R（小说家）": CharacterAsset(
                name="R（小说家）",
                role="小说家",
                aliases=["帝国伯爵", prose],
            ),
            prose: CharacterAsset(name=prose, role="小说家"),
            "帝国伯爵": CharacterAsset(name="帝国伯爵", role="伯爵情人"),
        },
        visual_bible=VisualBible(
            version="bible_v1",
            style_guide="vienna",
            color=ColorBible(palette=[], lighting="", forbidden=[]),
            characters={
                "R（小说家）": CharacterCanon(
                    canonical_name="R（小说家）",
                    role="小说家",
                    aliases=["帝国伯爵", prose],
                    face_lock="face",
                    stages=[
                        CharacterStage(
                            stage="adult",
                            hair_lock="",
                            outfit_lock="",
                            portrait_key=prose,
                        )
                    ],
                )
            },
        ),
    )
    from core.comic.visual_bible import sanitize_visual_bible_state

    assert sanitize_visual_bible_state(state) is True
    assert prose not in state.characters
    assert state.visual_bible.version == "bible_v2"
    assert "帝国伯爵" not in state.visual_bible.characters["R（小说家）"].aliases
    assert state.visual_bible.characters["R（小说家）"].stages[0].portrait_key.startswith("R")
```

- [ ] **Step 2: Implement → pass → commit**

```bash
git commit -m "feat: sanitize polluted visual bible state to bible_v2"
```

---

### Task 3: Panel character backfill

**Files:**
- Modify: `core/comic/visual_bible.py`
- Modify: `tests/test_visual_bible_v2.py`

**Interfaces:**
- `backfill_panel_characters(plan: ComicPagePlan, known_names: Iterable[str]) -> ComicPagePlan`
- Known names = all character keys + bible canonicals + aliases

- [ ] **Step 1: Test**

```python
def test_backfill_panel_characters_from_action_and_refs():
    from core.comic.visual_bible import backfill_panel_characters
    from core.schemas import ComicPagePlan

    plan = ComicPagePlan.model_validate(
        {
            "page_id": "p1",
            "purpose": "海滩",
            "layout_intent": "三格",
            "panels": [
                {
                    "panel_id": "1",
                    "characters": [],
                    "action": "女人牵着金发男孩在海滩散步",
                }
            ],
            "reference_characters": ["陌生女人（信中叙述者）", "死去的儿子"],
        }
    )
    fixed = backfill_panel_characters(
        plan, ["陌生女人（信中叙述者）", "死去的儿子", "R（小说家）"]
    )
    assert "陌生女人（信中叙述者）" in fixed.panels[0].characters
    assert "死去的儿子" in fixed.panels[0].characters
```

Matching: exact substring of known name in action, plus always include page refs when panel empty.

- [ ] **Step 2: Implement → pass → commit**

```bash
git commit -m "feat: backfill empty panel characters from refs and action"
```

---

### Task 4: Prompt + screenwriter + pipeline wire + fingerprint bible_v2

**Files:**
- Modify: `core/comic/page_prompt.py`
- Modify: `core/screenwriter.py`
- Modify: `core/pipelines/creative_comic.py`
- Modify: `tests/test_visual_bible_prompt.py`, `tests/test_finished_page_pipeline.py`, `tests/test_visual_bible_v2.py`

**Behavior:**
- Page prompt when bible: anti-sheet line + period wardrobe line + anti multi-age collage line
- Reconcile system/user: never invent English prose names; never high-merge incompatible roles; always fill locks; portrait_key short form only
- Pipeline: after load / before pages loop, `if sanitize_visual_bible_state(state): soft-invalidate + save`
- Before render each plan: `backfill_panel_characters` then existing rewrite
- Fingerprint token `bible_v2` when bible.version startswith bible_v2 OR always emit `bible_v2` once code ships (prefer: use `state.visual_bible.version if bible else omit`, and sanitize sets bible_v2)

- [ ] **Step 1: Tests**

```python
def test_prompt_forbids_character_sheets_and_modern_athleisure():
    # bible present → assert "turnaround" or "design sheet" forbidden language
    # and "hoodie" / period wardrobe line present
    ...


def test_render_fingerprint_uses_bible_v2_token():
    # _render_fingerprint(..., bible_version="bible_v2", bible_hash="abc")
    ...
```

- [ ] **Step 2: Implement all wiring → run full related suite → commit**

```bash
.venv/bin/python -m pytest tests/test_visual_bible_v2.py tests/test_visual_bible.py tests/test_visual_bible_prompt.py tests/test_visual_bible_reconcile.py tests/test_finished_page_pipeline.py tests/test_page_prompt.py -q --tb=short
.venv/bin/ruff check core/comic/visual_bible.py core/comic/page_prompt.py core/screenwriter.py core/pipelines/creative_comic.py
git commit -m "feat: wire bible_v2 sanitize, backfill, and page prompt locks"
```

---

### Task 5: Final verification

- [ ] Run full related pytest + ruff; fix regressions
- [ ] Commit only if needed
- [ ] Note in report: re-run `9910f82a3873` to regenerate

## Spec coverage

| Spec | Task |
|---|---|
| Illegal names | T1–T2 |
| Merge guard | T1 |
| Lock requirements | T1–T2 |
| Sanitize resume | T2 + T4 |
| Panel backfill | T3–T4 |
| Prompt anti-sheet/period | T4 |
| Fingerprint bible_v2 | T4 |
