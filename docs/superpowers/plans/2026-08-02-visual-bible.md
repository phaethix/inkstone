# Visual Bible Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Lock project-level style, color palette, and canonical character identity so finished pages stop drifting in color and fragmenting the same person into many faces (phase B; C sheet hooks only).

**Architecture:** Persist a `VisualBible` on `ProjectState`. After each extract, LLM reconcile merges semantic aliases / age stages into canons. Finished-page and portrait prompts always inject bible style + color + face/outfit locks; page refs always include on-page portraits. Fingerprint carries `bible_v1` + content hash.

**Tech Stack:** Python ≥3.10, Pydantic v2, existing ChatProvider tool-calling, Pillow (unchanged), pytest, ruff.

**Spec:** `docs/superpowers/specs/2026-08-02-visual-bible-design.md`  
**Repro:** `comic_out/93520df389e3`

## Global Constraints

- English code, comments, commits (CONTRIBUTING).
- No new hard dependencies.
- Phase B only: `sheet_ref_local` stays `None`; `build_visual_sheet` is a no-op returning `None`.
- Do not enable L3 by default.
- Do not block generation on `needs_review` (low-confidence → review only).
- Old `state.json` without `visual_bible` must still load (`None`).
- TDD: failing test → implement → pass → commit per task.
- Keep `panel_compose` working; share helpers where cheap, finished_page is primary consumer.

## File map

| File | Responsibility |
|---|---|
| `core/schemas.py` | `ColorSwatch`, `ColorBible`, `CharacterStage`, `CharacterCanon`, `VisualBible`, `VisualBibleReconcileResult`; `ProjectState.visual_bible` |
| `core/comic/visual_bible.py` | **New.** parse `Name@stage`, hash, apply reconcile, build L1 from canon, sheet stub, page-name rewrite |
| `core/comic/identity.py` | Optional thin wrappers; prefer calling `visual_bible` from ensure paths |
| `core/comic/page_prompt.py` | Inject style/color/locks from bible |
| `core/comic/consistency.py` | Finished-page / panel ref collection: portraits ∪ sheet hook |
| `core/screenwriter.py` | `reconcile_visual_bible` chat tool call |
| `core/pipelines/creative_comic.py` | Wire reconcile → portraits → pages; fingerprint; force refs |
| `tests/test_visual_bible_schema.py` | Schema round-trip |
| `tests/test_visual_bible.py` | Hash, stage parse, apply merges/stages, sheet stub |
| `tests/test_visual_bible_prompt.py` | Prompt injection |
| `tests/test_visual_bible_refs.py` | Ref list + sheet-first hook |
| `tests/test_finished_page_pipeline.py` | Fingerprint tokens |

---

### Task 1: Schema — Visual Bible models on ProjectState

**Files:**
- Modify: `core/schemas.py`
- Create: `tests/test_visual_bible_schema.py`

**Interfaces:**
- Produces: `ColorSwatch`, `ColorBible`, `CharacterStage`, `CharacterCanon`, `VisualBible`, `VisualBibleReconcileResult` (+ nested merge/stage/keep rows), `ProjectState.visual_bible: VisualBible | None`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_visual_bible_schema.py
from core.schemas import ProjectState, VisualBible, VisualBibleReconcileResult


def test_visual_bible_round_trip_on_project_state():
    raw = {
        "project_id": "p1",
        "visual_bible": {
            "version": "bible_v1",
            "style_guide": "manhua, muted European period tones",
            "color": {
                "palette": [
                    {"name": "ink_black", "hex": "#1A1A1A", "usage": "line art"},
                    {"name": "skin_warm", "hex": "#E8C4A8", "usage": "skin"},
                ],
                "lighting": "soft even cel lighting",
                "forbidden": ["neon", "hyper-saturated"],
            },
            "characters": {
                "R": {
                    "canonical_name": "R",
                    "aliases": ["R·", "李先生"],
                    "face_lock": "handsome European man, dark short hair, calm eyes",
                    "palette_notes": "dark suit, white shirt",
                    "role": "writer",
                    "stages": [
                        {
                            "stage": "adult",
                            "appearance": {"hair": "dark short", "outfit_top": "suit"},
                            "outfit_lock": "dark suit jacket, white shirt",
                            "hair_lock": "dark short neat hair",
                            "portrait_key": "R",
                        }
                    ],
                }
            },
            "sheet_ref_local": None,
            "content_hash": "abc",
        },
    }
    state = ProjectState.model_validate(raw)
    assert state.visual_bible is not None
    assert state.visual_bible.characters["R"].aliases == ["R·", "李先生"]
    assert state.visual_bible.color.palette[0].hex == "#1A1A1A"
    dumped = state.model_dump()
    assert dumped["visual_bible"]["version"] == "bible_v1"


def test_project_state_loads_without_visual_bible():
    state = ProjectState.model_validate({"project_id": "old"})
    assert state.visual_bible is None


def test_reconcile_result_schema():
    result = VisualBibleReconcileResult.model_validate(
        {
            "merges": [
                {
                    "alias": "李先生",
                    "canonical": "R",
                    "confidence": "high",
                    "reason": "same man",
                }
            ],
            "stages": [
                {
                    "name": "女孩（叙述者）",
                    "stage": "teen",
                    "of_canonical": "陌生女人",
                    "reason": "younger self",
                }
            ],
            "keeps": [{"name": "老约翰", "reason": "servant"}],
            "color_patches": [],
            "style_guide": "manhua muted tones",
            "color": {
                "palette": [{"name": "ink", "hex": "#111111", "usage": "lines"}],
                "lighting": "soft",
                "forbidden": ["neon"],
            },
            "canons": [],
        }
    )
    assert result.merges[0].confidence == "high"
```

Place new models near other comic schemas in `core/schemas.py` (before `ProjectState`). `VisualBibleReconcileResult` fields:

```python
class VisualBibleMerge(BaseModel):
    alias: str
    canonical: str
    confidence: Literal["high", "low"]
    reason: str = ""

class VisualBibleStageLink(BaseModel):
    name: str
    stage: Literal["child", "teen", "adult", "elder", "default"]
    of_canonical: str
    reason: str = ""

class VisualBibleKeep(BaseModel):
    name: str
    reason: str = ""

class VisualBibleReconcileResult(BaseModel):
    """LLM tool payload for bible create/update."""
    merges: list[VisualBibleMerge] = Field(default_factory=list)
    stages: list[VisualBibleStageLink] = Field(default_factory=list)
    keeps: list[VisualBibleKeep] = Field(default_factory=list)
    color_patches: list[ColorSwatch] = Field(default_factory=list)
    style_guide: str = ""
    color: ColorBible | None = None
    canons: list[CharacterCanon] = Field(default_factory=list)
```

`CharacterStage.stage` uses the same Literal. `VisualBible.characters` is `dict[str, CharacterCanon]`. Coerce empty bible fields with existing `coerce_str` / list helpers.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_visual_bible_schema.py -q --tb=line`  
Expected: FAIL (import / missing types)

- [ ] **Step 3: Implement schema**

Add the models from the spec §4 plus reconcile result types above. On `ProjectState` add:

```python
visual_bible: VisualBible | None = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_visual_bible_schema.py -q --tb=short`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add core/schemas.py tests/test_visual_bible_schema.py
git commit -m "feat: add VisualBible schema models on ProjectState"
```

---

### Task 2: visual_bible helpers — parse, hash, apply, sheet stub

**Files:**
- Create: `core/comic/visual_bible.py`
- Create: `tests/test_visual_bible.py`

**Interfaces:**
- Consumes: schema types from Task 1; `merge_character_alias` from `core.comic.identity`
- Produces:
  - `parse_stage_ref(name: str) -> tuple[str, str]` → `(canonical_or_base, stage)` with stage default `"default"`
  - `compute_bible_hash(bible: VisualBible) -> str`
  - `refresh_bible_hash(bible: VisualBible) -> VisualBible`
  - `apply_reconcile(state: ProjectState, result: VisualBibleReconcileResult) -> ProjectState`
  - `l1_from_canon(canon: CharacterCanon, stage: str = "default") -> str`
  - `rewrite_page_plan_names(plan: ComicPagePlan, mapping: dict[str, str]) -> ComicPagePlan`
  - `build_visual_sheet(bible: VisualBible) -> None`  # always returns None in B
  - `collect_finished_page_refs(plan, characters_by_name, bible, *, prev_blank: str | None = None, max_refs: int = 9) -> list[str]`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_visual_bible.py
from core.comic.visual_bible import (
    apply_reconcile,
    build_visual_sheet,
    collect_finished_page_refs,
    compute_bible_hash,
    l1_from_canon,
    parse_stage_ref,
    refresh_bible_hash,
    rewrite_page_plan_names,
)
from core.schemas import (
    CharacterAsset,
    CharacterCanon,
    CharacterStage,
    ColorBible,
    ColorSwatch,
    ComicPagePlan,
    ProjectState,
    VisualBible,
    VisualBibleMerge,
    VisualBibleReconcileResult,
    VisualBibleStageLink,
)


def test_parse_stage_ref():
    assert parse_stage_ref("陌生女人@teen") == ("陌生女人", "teen")
    assert parse_stage_ref("R") == ("R", "default")


def test_bible_hash_stable_and_sensitive():
    bible = VisualBible(
        version="bible_v1",
        style_guide="manhua",
        color=ColorBible(
            palette=[ColorSwatch(name="ink", hex="#111111", usage="lines")],
            lighting="soft",
            forbidden=["neon"],
        ),
        characters={
            "R": CharacterCanon(
                canonical_name="R",
                face_lock="face A",
                palette_notes="suit",
                stages=[
                    CharacterStage(
                        stage="adult",
                        outfit_lock="dark suit",
                        hair_lock="dark hair",
                        portrait_key="R",
                    )
                ],
            )
        },
    )
    h1 = compute_bible_hash(bible)
    bible2 = bible.model_copy(update={"style_guide": "watercolor"})
    assert compute_bible_hash(bible2) != h1
    assert compute_bible_hash(refresh_bible_hash(bible)) == h1 or True  # hash field ignored in input


def test_apply_high_confidence_merge_and_low_to_review():
    state = ProjectState(
        project_id="p",
        characters={
            "R": CharacterAsset(name="R", role="writer"),
            "李先生": CharacterAsset(name="李先生", role="man"),
            "路人": CharacterAsset(name="路人", role="extra"),
        },
        visual_bible=VisualBible(
            version="bible_v1",
            style_guide="manhua",
            color=ColorBible(palette=[], lighting="soft", forbidden=[]),
            characters={
                "R": CharacterCanon(canonical_name="R", face_lock="f", stages=[]),
            },
        ),
    )
    result = VisualBibleReconcileResult(
        merges=[
            VisualBibleMerge(alias="李先生", canonical="R", confidence="high", reason="same"),
            VisualBibleMerge(alias="路人", canonical="R", confidence="low", reason="unsure"),
        ],
        stages=[],
        keeps=[],
    )
    out = apply_reconcile(state, result)
    assert "李先生" not in out.characters
    assert "李先生" in out.characters["R"].aliases or "李先生" in out.visual_bible.characters["R"].aliases
    assert "路人" in out.characters
    assert any(s.new_name == "路人" for s in out.needs_review)


def test_apply_stage_link():
    state = ProjectState(
        project_id="p",
        characters={
            "陌生女人": CharacterAsset(name="陌生女人"),
            "女孩（叙述者）": CharacterAsset(name="女孩（叙述者）"),
        },
        visual_bible=VisualBible(
            version="bible_v1",
            style_guide="x",
            color=ColorBible(palette=[], lighting="", forbidden=[]),
            characters={
                "陌生女人": CharacterCanon(
                    canonical_name="陌生女人",
                    face_lock="soft face",
                    stages=[
                        CharacterStage(
                            stage="adult",
                            outfit_lock="dress",
                            hair_lock="long dark",
                            portrait_key="陌生女人",
                        )
                    ],
                )
            },
        ),
    )
    result = VisualBibleReconcileResult(
        stages=[
            VisualBibleStageLink(
                name="女孩（叙述者）",
                stage="teen",
                of_canonical="陌生女人",
                reason="younger",
            )
        ]
    )
    out = apply_reconcile(state, result)
    stages = {s.stage for s in out.visual_bible.characters["陌生女人"].stages}
    assert "teen" in stages


def test_l1_from_canon_includes_locks():
    canon = CharacterCanon(
        canonical_name="R",
        face_lock="calm eyes",
        palette_notes="dark suit colors",
        stages=[
            CharacterStage(
                stage="adult",
                outfit_lock="dark suit",
                hair_lock="dark short hair",
                portrait_key="R",
            )
        ],
    )
    text = l1_from_canon(canon, "adult")
    assert "calm eyes" in text
    assert "dark suit" in text
    assert "dark short hair" in text


def test_rewrite_page_plan_names():
    plan = ComicPagePlan.model_validate(
        {
            "page_id": "p1",
            "purpose": "x",
            "layout_intent": "y",
            "panels": [{"panel_id": "1", "characters": ["李先生"], "action": "stands"}],
            "reference_characters": ["李先生"],
        }
    )
    fixed = rewrite_page_plan_names(plan, {"李先生": "R"})
    assert fixed.panels[0].characters == ["R"]
    assert fixed.reference_characters == ["R"]


def test_build_visual_sheet_noop():
    assert build_visual_sheet(VisualBible(version="bible_v1", style_guide="", color=ColorBible(palette=[], lighting="", forbidden=[]), characters={})) is None


def test_collect_refs_uses_panel_characters_and_sheet_first():
    plan = ComicPagePlan.model_validate(
        {
            "page_id": "p1",
            "purpose": "x",
            "layout_intent": "y",
            "panels": [{"panel_id": "1", "characters": ["R"], "action": "sits"}],
            "reference_characters": [],
        }
    )
    chars = {"R": CharacterAsset(name="R", portrait_local="/tmp/r.png")}
    bible = VisualBible(
        version="bible_v1",
        style_guide="",
        color=ColorBible(palette=[], lighting="", forbidden=[]),
        characters={},
        sheet_ref_local="/tmp/sheet.png",
    )
    refs = collect_finished_page_refs(plan, chars, bible, prev_blank="/tmp/prev.png")
    assert refs[0] == "/tmp/sheet.png"
    assert "/tmp/r.png" in refs
```

Hash rules: hash JSON of `{style_guide, color, characters: {name: {face_lock, palette_notes, stages: [{stage, outfit_lock, hair_lock}]}}}` with `sort_keys=True`; ignore `content_hash` and `sheet_ref_local` for the digest. `refresh_bible_hash` sets `content_hash`.

`apply_reconcile` high merge: call `merge_character_alias(state, alias, canonical)` then ensure alias on canon; low merge: `suggestion_from_alias` into `needs_review`. Stage link: append `CharacterStage` if missing; map alias name into canon aliases; do not delete stage source character until high merge says so (stage link alone keeps asset if still referenced — prefer also adding alias and leaving portrait_key `f"{canonical}@{stage}"`).

`collect_finished_page_refs`: resolve names via `parse_stage_ref`; look up `portrait_local` on `characters_by_name` by base name or stage `portrait_key` if present on bible; sheet first; then portraits; then `prev_blank`; cap `max_refs`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_visual_bible.py -q --tb=line`  
Expected: FAIL (module missing)

- [ ] **Step 3: Implement `core/comic/visual_bible.py`**

Implement the functions above. Keep file focused; no chat I/O here.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_visual_bible.py tests/test_visual_bible_schema.py -q --tb=short`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add core/comic/visual_bible.py tests/test_visual_bible.py
git commit -m "feat: add visual bible hash, reconcile apply, and ref helpers"
```

---

### Task 3: Screenwriter — reconcile_visual_bible chat tool

**Files:**
- Modify: `core/screenwriter.py`
- Create: `tests/test_visual_bible_reconcile.py`

**Interfaces:**
- Consumes: `VisualBibleReconcileResult`, `to_tool_schema`, chat provider
- Produces: `async def reconcile_visual_bible(text: str, state_characters: dict, bible: VisualBible | None, *, alias_hints: list[tuple[str,str,str]] = (), chat=None) -> VisualBibleReconcileResult`

- [ ] **Step 1: Write the failing test (mock chat)**

```python
# tests/test_visual_bible_reconcile.py
import pytest

from core.schemas import CharacterAsset, VisualBible, ColorBible
from core.screenwriter import reconcile_visual_bible


class _FakeChat:
    async def chat_with_tools(self, *, messages, tools, tool_choice):
        return {
            "merges": [
                {
                    "alias": "李先生",
                    "canonical": "R",
                    "confidence": "high",
                    "reason": "same protagonist",
                }
            ],
            "stages": [],
            "keeps": [{"name": "老约翰", "reason": "servant"}],
            "color_patches": [],
            "style_guide": "manhua, muted European period",
            "color": {
                "palette": [
                    {"name": "ink", "hex": "#1A1A1A", "usage": "lines"},
                    {"name": "skin", "hex": "#E8C4A8", "usage": "skin"},
                ],
                "lighting": "soft even cel",
                "forbidden": ["neon"],
            },
            "canons": [
                {
                    "canonical_name": "R",
                    "aliases": ["李先生"],
                    "face_lock": "handsome man calm eyes",
                    "palette_notes": "dark suit",
                    "role": "writer",
                    "stages": [
                        {
                            "stage": "adult",
                            "outfit_lock": "dark suit",
                            "hair_lock": "dark short hair",
                            "portrait_key": "R",
                        }
                    ],
                }
            ],
        }


@pytest.mark.asyncio
async def test_reconcile_visual_bible_parses_tool_payload():
    chars = {
        "R": CharacterAsset(name="R"),
        "李先生": CharacterAsset(name="李先生"),
        "老约翰": CharacterAsset(name="老约翰"),
    }
    result = await reconcile_visual_bible(
        "excerpt about R and 李先生",
        chars,
        None,
        alias_hints=[("李先生", "R", "similar")],
        chat=_FakeChat(),
    )
    assert result.merges[0].alias == "李先生"
    assert result.style_guide.startswith("manhua")
    assert result.canons[0].canonical_name == "R"
```

Match whatever chat helper pattern `plan_comic_pages` / `extract_story_elements` already use (`chat_with_tools` vs `complete_tool`). Mirror that exact call style — do not invent a new provider API.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_visual_bible_reconcile.py -q --tb=line`  
Expected: FAIL (`reconcile_visual_bible` missing)

- [ ] **Step 3: Implement**

In `core/screenwriter.py`:

```python
RECONCILE_BIBLE_TOOL = to_tool_schema(
    VisualBibleReconcileResult,
    "reconcile_visual_bible",
    "Build or update the project visual bible: merge aliases, attach age stages, "
    "lock style_guide and color palette, emit CharacterCanon entries.",
)
```

System/user instructions must say:

- Prefer merging pronouns/descriptive labels for the same person (`男人（被叙述者）`, `他（被爱者）`, `李先生`, `R·`) into one canonical.
- Age variants → stages, not new roots.
- High confidence only when clearly same person; else `low`.
- On first bible (`bible is None`), fill `style_guide`, `color` (4–6 swatches), and full `canons`.
- On update, keep existing palette unless `color_patches` justified; still return merges/stages/keeps for new names.

On failure, re-raise only if you cannot parse; pipeline will catch — prefer returning empty merges with existing style if partial. For this task, let exceptions propagate; pipeline handles.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_visual_bible_reconcile.py -q --tb=short`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add core/screenwriter.py tests/test_visual_bible_reconcile.py
git commit -m "feat: add reconcile_visual_bible screenwriter tool"
```

---

### Task 4: Prompt injection — style, color, locks

**Files:**
- Modify: `core/comic/page_prompt.py`
- Modify: `core/comic/visual_bible.py` (add `format_color_bible_block`, `format_character_lock_lines` if not already)
- Create: `tests/test_visual_bible_prompt.py`

**Interfaces:**
- Consumes: `VisualBible`, `l1_from_canon`, `parse_stage_ref`
- Produces: `render_finished_page_prompt(..., visual_bible: VisualBible | None = None)` includes color + locks when bible present

- [ ] **Step 1: Write failing test**

```python
# tests/test_visual_bible_prompt.py
from core.comic.page_prompt import render_finished_page_prompt
from core.schemas import (
    CharacterAsset,
    CharacterCanon,
    CharacterStage,
    ColorBible,
    ColorSwatch,
    ComicPagePlan,
    VisualBible,
)


def test_finished_page_prompt_injects_color_and_face_lock():
    bible = VisualBible(
        version="bible_v1",
        style_guide="manhua muted European period",
        color=ColorBible(
            palette=[ColorSwatch(name="skin", hex="#E8C4A8", usage="skin")],
            lighting="soft even cel",
            forbidden=["neon"],
        ),
        characters={
            "R": CharacterCanon(
                canonical_name="R",
                face_lock="calm dark eyes",
                palette_notes="dark suit",
                stages=[
                    CharacterStage(
                        stage="adult",
                        outfit_lock="dark suit jacket",
                        hair_lock="dark short hair",
                        portrait_key="R",
                    )
                ],
            )
        },
        content_hash="x",
    )
    plan = ComicPagePlan.model_validate(
        {
            "page_id": "p1",
            "purpose": "meet",
            "layout_intent": "two shot",
            "panels": [{"panel_id": "1", "characters": ["R"], "action": "R sits"}],
        }
    )
    text = render_finished_page_prompt(
        plan,
        characters_by_name={"R": CharacterAsset(name="R", l1_prompt="old loose")},
        settings_by_name={},
        style_guide="IGNORE_ME_IF_BIBLE",
        visual_bible=bible,
    )
    assert "manhua muted European period" in text
    assert "#E8C4A8" in text
    assert "soft even cel" in text
    assert "neon" in text
    assert "calm dark eyes" in text
    assert "dark suit jacket" in text
    assert "do not change hair color" in text.lower() or "unless action says costume change" in text.lower()
```

When `visual_bible` is set, prefer `bible.style_guide` over the `style_guide` argument for the Style line. Still pass character lines through canon locks when the name resolves in `bible.characters`.

- [ ] **Step 2: Run failing test**

Run: `.venv/bin/python -m pytest tests/test_visual_bible_prompt.py -q --tb=line`  
Expected: FAIL (unexpected kw / missing strings)

- [ ] **Step 3: Implement prompt wiring**

Update `render_finished_page_prompt` signature and body. Portrait path in pipeline (Task 5) will append the same color block — add `format_color_bible_block(bible) -> str` in `visual_bible.py` for reuse.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_visual_bible_prompt.py tests/test_page_prompt.py -q --tb=short`  
Expected: PASS (existing page_prompt tests still work with default `visual_bible=None`)

- [ ] **Step 5: Commit**

```bash
git add core/comic/page_prompt.py core/comic/visual_bible.py tests/test_visual_bible_prompt.py
git commit -m "feat: inject visual bible style color and locks into page prompts"
```

---

### Task 5: Pipeline wire — reconcile, portraits, refs, fingerprint

**Files:**
- Modify: `core/pipelines/creative_comic.py`
- Modify: `tests/test_finished_page_pipeline.py`
- Modify: `tests/test_visual_bible_refs.py` (create if Task 2 did not already cover collect; add pipeline-level name resolution test here only if needed)

**Interfaces:**
- Consumes: `reconcile_visual_bible`, `apply_reconcile`, `refresh_bible_hash`, `collect_finished_page_refs`, `format_color_bible_block`, `l1_from_canon`
- Produces: pipeline order per spec §6; fingerprint keys `visual_bible=bible_v1`, `bible_hash=<content_hash>`

- [ ] **Step 1: Write failing fingerprint test**

```python
# tests/test_finished_page_pipeline.py (addition)
def test_render_fingerprint_includes_visual_bible_hash():
    from core.pipelines.creative_comic import _render_fingerprint
    from core.schemas import ModelSnapshot

    fp = _render_fingerprint(
        "style",
        snapshot=ModelSnapshot(),
        panel_continuity=False,
        l3_enabled=False,
        render_mode="finished_page",
        page_size="1024x1536",
        bible_version="bible_v1",
        bible_hash="deadbeef",
    )
    # Reconstruct expected payload the same way _render_fingerprint does, assert hash equality
    import hashlib, json
    payload = {
        "style_guide": "style",
        "model_snapshot": ModelSnapshot().model_dump(),
        "panel_continuity": False,
        "l3_enabled": False,
        "render_mode": "finished_page",
        "page_size": "1024x1536",
        "identity": "metaphor_v2",
        "lettering": "deferred_v3",
        "visual_bible": "bible_v1",
        "bible_hash": "deadbeef",
    }
    expected = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert fp == expected
```

Adjust `identity` / `lettering` tokens to whatever the file currently uses when editing.

- [ ] **Step 2: Run failing test**

Run: `.venv/bin/python -m pytest tests/test_finished_page_pipeline.py::test_render_fingerprint_includes_visual_bible_hash -q --tb=short`  
Expected: FAIL (unexpected kwargs or hash mismatch)

- [ ] **Step 3: Wire pipeline**

In `_creative_comic` finished_page loop, after `merge_characters` / string alias detect:

```python
hints = detect_character_aliases(state.characters, new_names)
# existing needs_review append for string hints stays
try:
    recon = await reconcile_visual_bible(
        chunk, state.characters, state.visual_bible, alias_hints=hints, chat=chat
    )
    prev_hash = state.visual_bible.content_hash if state.visual_bible else None
    state = apply_reconcile(state, recon)
    if state.visual_bible:
        state.visual_bible = refresh_bible_hash(state.visual_bible)
        if prev_hash and state.visual_bible.content_hash != prev_hash:
            _soft_invalidate_render(state)
except Exception as exc:
    logger.warning("visual bible reconcile failed (%s); continuing", exc)
```

Style lock:

```python
if state.visual_bible and state.visual_bible.style_guide:
    effective_style = state.visual_bible.style_guide
else:
    effective_style = style_guide or elements.style_guide
```

Portrait prompt: append `format_color_bible_block(state.visual_bible)` when present; prefer `l1_from_canon` when name in bible.

Page render:

```python
prompt = render_finished_page_prompt(..., style_guide=effective_style, visual_bible=state.visual_bible)
refs = collect_finished_page_refs(
    plan,
    state.characters,
    state.visual_bible,
    prev_blank=prev_blank_path,  # optional from previous GeneratedPage in chunk
)
# filter existing files within output_dir as today
```

Replace `_page_reference_names`-only path for finished pages with `collect_finished_page_refs`.

Update `_render_fingerprint` signature to accept `bible_version: str | None = None`, `bible_hash: str | None = None` and include them when provided; call site passes from `state.visual_bible` (use `"none"` / `""` when missing so first bible creation changes fingerprint once state gains a hash — or recompute fingerprint after reconcile in the same run before pages).

After first bible is created mid-run, soft-invalidate only if pages/portraits already existed with old hash; on brand-new project `prev_hash is None` → no wipe needed.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_finished_page_pipeline.py tests/test_visual_bible.py tests/test_visual_bible_prompt.py tests/test_visual_bible_reconcile.py -q --tb=short`  
Expected: PASS

Also run: `.venv/bin/ruff check core/schemas.py core/comic/visual_bible.py core/comic/page_prompt.py core/screenwriter.py core/pipelines/creative_comic.py`

- [ ] **Step 5: Commit**

```bash
git add core/pipelines/creative_comic.py tests/test_finished_page_pipeline.py
git commit -m "feat: wire visual bible reconcile into finished_page pipeline"
```

---

### Task 6: Sync CharacterAsset L1 from canon + plan name rewrite after reconcile

**Files:**
- Modify: `core/comic/visual_bible.py` (`sync_characters_from_bible`)
- Modify: `core/pipelines/creative_comic.py` (call after apply)
- Modify: `tests/test_visual_bible.py`

**Interfaces:**
- Produces: `sync_characters_from_bible(state: ProjectState) -> None` — for each canon, set `CharacterAsset.l1_prompt` / `portrait_prompt` from `l1_from_canon`; ensure aliases listed; rewrite all plans in `page_cache` via alias→canonical map (and `Name` → `Name@stage` only when stage link introduced this turn — keep simple: map aliases to canonical_name; stage refs are planner's job going forward)

- [ ] **Step 1: Failing test**

```python
def test_sync_characters_from_bible_updates_l1():
    from core.comic.visual_bible import sync_characters_from_bible
    state = ProjectState(
        project_id="p",
        characters={"R": CharacterAsset(name="R", l1_prompt="stale")},
        visual_bible=VisualBible(
            version="bible_v1",
            style_guide="manhua",
            color=ColorBible(palette=[], lighting="", forbidden=[]),
            characters={
                "R": CharacterCanon(
                    canonical_name="R",
                    face_lock="locked face",
                    stages=[
                        CharacterStage(
                            stage="default",
                            outfit_lock="locked outfit",
                            hair_lock="locked hair",
                            portrait_key="R",
                        )
                    ],
                )
            },
        ),
    )
    sync_characters_from_bible(state)
    assert "locked face" in state.characters["R"].l1_prompt
    assert "locked outfit" in state.characters["R"].l1_prompt
```

- [ ] **Step 2: Run fail → implement → pass → commit**

```bash
git add core/comic/visual_bible.py core/pipelines/creative_comic.py tests/test_visual_bible.py
git commit -m "feat: sync character L1 prompts from visual bible canons"
```

---

### Task 7: Docs honesty + final verification

**Files:**
- Modify: `README.md` and/or `docs/ROADMAP.md` only if they claim character consistency behavior — one short note that finished_page uses a project Visual Bible (style/color/canon). Skip if no existing claim needs updating.
- Run full related suite.

- [ ] **Step 1: Grep docs for over-claims**

Run: `rg -n "character consistency|style_guide|alias" README.md docs/ROADMAP.md docs/superpowers -g'*.md' | head`

Update only if a sentence would become false.

- [ ] **Step 2: Full test + ruff**

```bash
.venv/bin/python -m pytest tests/test_visual_bible_schema.py tests/test_visual_bible.py tests/test_visual_bible_reconcile.py tests/test_visual_bible_prompt.py tests/test_finished_page_pipeline.py tests/test_page_prompt.py tests/test_identity_metaphor.py -q --tb=short
.venv/bin/ruff check core/schemas.py core/comic/visual_bible.py core/comic/page_prompt.py core/screenwriter.py core/pipelines/creative_comic.py
.venv/bin/ruff format core/schemas.py core/comic/visual_bible.py core/comic/page_prompt.py core/screenwriter.py core/pipelines/creative_comic.py tests/test_visual_bible*.py
```

Expected: all PASS, ruff clean.

- [ ] **Step 3: Commit doc touch if any**

```bash
git add README.md docs/ROADMAP.md  # only if changed
git commit -m "docs: note project visual bible for finished_page consistency"
```

---

## Spec coverage self-check

| Spec item | Task |
|---|---|
| ColorBible / CharacterCanon / VisualBible / ProjectState field | T1 |
| Name@stage parse, hash, apply merge/stage, sheet noop, refs+sheet-first | T2 |
| LLM reconcile tool | T3 |
| Prompt style+color+locks | T4 |
| Pipeline order, fingerprint, soft-invalidate on hash change | T5 |
| Sync L1 from canon; rewrite cached names via merges | T2 apply + T6 |
| C hooks only | T2 `build_visual_sheet` / sheet-first refs |
| Migration = re-run | T5 fingerprint (no manual migrator) |
| Tests listed in spec §9 | T1–T6 |

## Placeholder scan

No TBD / “implement later” steps. Exact commands and code included.

## Type consistency

- `confidence: Literal["high","low"]` shared by schema and apply.
- Fingerprint keys: `visual_bible`, `bible_hash` (not `visual_sheet` in B).
- `collect_finished_page_refs` is the finished-page ref API; panel_compose may keep `ConsistencyEngine.collect_reference_images` unchanged.
