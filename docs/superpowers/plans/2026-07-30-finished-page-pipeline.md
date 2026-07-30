# Finished-Page Comic Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the default Inkstone output a sequence of finished A4-portrait manga pages (dynamic panels + in-image lettering) bound into an LTR flip PDF, with the existing per-panel + `LayoutEngine` path kept only as explicit fallback.

**Architecture:** Schema-first `ComicPagePlan` via forced tool call → pure `render_finished_page_prompt` → one image call per page → `generated.pages` → `ExportEngine` on `pages/`. Do not copy `codex-novel-to-comic-studio` files or agent workflows. Panel compose remains behind `render_mode=panel_compose` / `INKSTONE_RENDER_MODE=panel_compose`.

**Tech Stack:** Python ≥3.10, Pydantic v2, existing ChatProvider/ImageProvider, Pillow, pytest, ruff.

**Spec:** `docs/superpowers/specs/2026-07-30-finished-page-pipeline-design.md`

## Global Constraints

- English code, comments, commits (CONTRIBUTING).
- No new hard dependencies.
- Do not plagiarize the studio repo; borrow concepts only.
- Default product mode: `finished_page`. Existing panel-path tests must opt into `panel_compose`.
- No L3 face-swap on finished pages.
- Honest skips for content-policy rejects (`skipped_pages`).
- TDD: failing test → implement → pass → commit per task.
- Keep old `state.json` with only `generated.panels` loadable; do not force-migrate assets.

## File map

| File | Responsibility |
|---|---|
| `core/schemas.py` | `PagePanelSpec`, `ComicPagePlan`, `ComicPagePlanSet`, `GeneratedPage`; extend `GeneratedAssets`, `ProjectState`, `Stage` |
| `core/config.py` | `INKSTONE_RENDER_MODE`, `INKSTONE_PAGE_SIZE`, helpers |
| `core/comic/page_prompt.py` | **New.** Deterministic finished-page prompt render |
| `core/screenwriter.py` | `plan_comic_pages` + tool schema |
| `core/pipelines/creative_comic.py` | Mode branch, page cache/resume, fingerprints, export wiring |
| `core/comic/export.py` | Unchanged API if pages land as `page_NN.png`; verify ordering |
| `web/server.py` | Expose `pages_done` / mode in job/project JSON |
| `docs/ROADMAP.md`, `README.md` | Honesty + default mode notes |
| `tests/test_schemas_finished_page.py` | Schema/state round-trip |
| `tests/test_page_prompt.py` | Prompt renderer contracts |
| `tests/test_screenwriter_pages.py` | plan_comic_pages with fake chat |
| `tests/test_finished_page_pipeline.py` | Pipeline happy path + fallback flag |

---

### Task 1: Schemas and state fields

**Files:**
- Modify: `core/schemas.py`
- Create: `tests/test_schemas_finished_page.py`

**Interfaces:**
- Produces: `PagePanelSpec`, `ComicPagePlan`, `ComicPagePlanSet`, `GeneratedPage`, `RenderMode = Literal["finished_page", "panel_compose"]`
- Extends: `Stage` with `"page_plan"` | `"pages"`; `GeneratedAssets.pages`; `ProjectState` fields below

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_schemas_finished_page.py
from core.schemas import (
    ComicPagePlan,
    ComicPagePlanSet,
    GeneratedPage,
    PagePanelSpec,
    ProjectState,
)


def test_page_panel_spec_and_plan_round_trip():
    plan = ComicPagePlan.model_validate(
        {
            "page_id": "p0001",
            "purpose": "Establish the station and introduce 福贵",
            "layout_intent": "Wide establishing top; diagonal inset reaction bottom-right",
            "panels": [
                {
                    "panel_id": "1",
                    "role": "establishing",
                    "shape_hint": "wide",
                    "shot": "wide",
                    "action": "福贵 walks through dusk streets",
                    "characters": ["福贵"],
                    "setting_ref": "城中街道",
                    "caption": "傍晚，街灯初上。",
                    "dialogue": None,
                    "sfx": "沙沙",
                }
            ],
            "reference_characters": ["福贵"],
            "setting_refs": ["城中街道"],
        }
    )
    assert plan.page_id == "p0001"
    assert plan.panels[0].caption == "傍晚，街灯初上。"
    assert plan.panels[0].dialogue is None


def test_comic_page_plan_set_and_generated_page_on_state():
    pageset = ComicPagePlanSet.model_validate(
        {"unit_id": "0", "pages": [{"page_id": "p0001", "purpose": "x", "layout_intent": "splash", "panels": []}]}
    )
    state = ProjectState(project_id="demo")
    state.page_cache["0"] = pageset
    state.generated.pages["p0001"] = GeneratedPage(
        local="pages/page_01.png",
        page_id="p0001",
        unit_index=0,
        page_index=0,
        mode="finished",
        caption="傍晚，街灯初上。",
    )
    state.pages_done.append("p0001")
    state.render_mode = "finished_page"
    state.stage = "pages"
    blob = state.model_dump_json()
    loaded = ProjectState.model_validate_json(blob)
    assert loaded.page_cache["0"].pages[0].page_id == "p0001"
    assert loaded.generated.pages["p0001"].mode == "finished"
    assert loaded.render_mode == "finished_page"
```

- [ ] **Step 2: Run tests — expect fail**

Run: `.venv/bin/python -m pytest tests/test_schemas_finished_page.py -v`  
Expected: import / attribute errors for missing types/fields.

- [ ] **Step 3: Implement schemas**

In `core/schemas.py`:

1. Extend `Stage`:
```python
Stage = Literal[
    "extract",
    "storyboard",
    "page_plan",
    "portraits",
    "panels",
    "pages",
    "layout",
    "export",
]
```

2. Add after `Panel` / near storyboard models (keep coercers consistent with `Panel`):

```python
RenderMode = Literal["finished_page", "panel_compose"]


class PagePanelSpec(BaseModel):
    model_config = ConfigDict(extra="ignore")
    panel_id: str
    role: str = "action"
    shape_hint: str = "rect"
    shot: str = ""
    action: str = ""
    characters: list[str] = Field(default_factory=list)
    setting_ref: str = ""
    dialogue: str | None = None
    caption: str | None = None
    sfx: str | None = None
    lettering_notes: str = ""
    # validators: coerce_str / coerce_str_list / coerce_dialogue like Panel


class ComicPagePlan(BaseModel):
    model_config = ConfigDict(extra="ignore")
    page_id: str
    purpose: str = ""
    layout_intent: str = ""
    panels: list[PagePanelSpec] = Field(default_factory=list)
    reference_characters: list[str] = Field(default_factory=list)
    setting_refs: list[str] = Field(default_factory=list)


class ComicPagePlanSet(BaseModel):
    model_config = ConfigDict(extra="ignore")
    unit_id: str = ""
    pages: list[ComicPagePlan] = Field(default_factory=list)


class GeneratedPage(BaseModel):
    model_config = ConfigDict(extra="ignore")
    local: str
    page_id: str = ""
    unit_index: int = 0
    page_index: int = 0
    mode: Literal["finished", "composed_fallback"] = "finished"
    dialogue: str | None = None
    caption: str | None = None
    sfx: str | None = None
```

3. Extend `GeneratedAssets`:
```python
pages: dict[str, GeneratedPage] = Field(default_factory=dict)
```

4. Extend `ProjectState`:
```python
render_mode: RenderMode = "finished_page"
page_cache: dict[str, ComicPagePlanSet] = Field(default_factory=dict)
pages_done: list[str] = Field(default_factory=list)
stale_pages: list[str] = Field(default_factory=list)
skipped_pages: list[str] = Field(default_factory=list)
```

Use the same `coerce_*` / `repair_fused_keys` patterns as `Panel` for resilience.

- [ ] **Step 4: Run tests — expect pass**

Run: `.venv/bin/python -m pytest tests/test_schemas_finished_page.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add core/schemas.py tests/test_schemas_finished_page.py
git commit -m "feat(schemas): add finished-page plan and GeneratedPage state"
```

---

### Task 2: Config for render mode and page size

**Files:**
- Modify: `core/config.py`
- Modify: `tests/test_config.py` (or create if missing helpers tests live elsewhere — prefer extend existing config tests)

**Interfaces:**
- Produces: `ENV_RENDER_MODE = "INKSTONE_RENDER_MODE"`, `render_mode() -> RenderMode`, `ENV_PAGE_SIZE`, `finished_page_size() -> str` default `"1024x1536"`

- [ ] **Step 1: Failing test**

```python
def test_render_mode_defaults_finished_page(monkeypatch):
    monkeypatch.delenv("INKSTONE_RENDER_MODE", raising=False)
    from core.config import render_mode
    assert render_mode() == "finished_page"


def test_render_mode_panel_compose(monkeypatch):
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "panel_compose")
    from core.config import render_mode
    assert render_mode() == "panel_compose"


def test_finished_page_size_default(monkeypatch):
    monkeypatch.delenv("INKSTONE_PAGE_SIZE", raising=False)
    from core.config import finished_page_size
    assert finished_page_size() == "1024x1536"
```

- [ ] **Step 2: Run — expect fail**

- [ ] **Step 3: Implement**

```python
ENV_RENDER_MODE = "INKSTONE_RENDER_MODE"
ENV_PAGE_SIZE = "INKSTONE_PAGE_SIZE"


def render_mode() -> str:
    raw = _get(ENV_RENDER_MODE, "finished_page").strip().lower()
    if raw in {"panel_compose", "panel", "compose"}:
        return "panel_compose"
    return "finished_page"


def finished_page_size() -> str:
    raw = _get(ENV_PAGE_SIZE, "1024x1536").strip()
    return raw or "1024x1536"
```

- [ ] **Step 4: Tests pass**

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(config): add INKSTONE_RENDER_MODE and page size"
```

---

### Task 3: Deterministic finished-page prompt renderer

**Files:**
- Create: `core/comic/page_prompt.py`
- Create: `tests/test_page_prompt.py`

**Interfaces:**
- Consumes: `ComicPagePlan`, `CharacterAsset` dict, `Setting` dict, `style_guide: str`
- Produces: `render_finished_page_prompt(...) -> str`

- [ ] **Step 1: Failing tests**

```python
from core.comic.page_prompt import render_finished_page_prompt
from core.schemas import CharacterAsset, ComicPagePlan, Setting


def test_prompt_includes_layout_lettering_and_identity():
    plan = ComicPagePlan.model_validate(
        {
            "page_id": "p0001",
            "purpose": "Hook: map moves",
            "layout_intent": "Wide top archive; diagonal window attack; reaction close-up",
            "panels": [
                {
                    "panel_id": "1",
                    "role": "establishing",
                    "shape_hint": "wide",
                    "action": "Mira leans over glowing map",
                    "characters": ["Mira"],
                    "caption": "MIDNIGHT AT THE SKY ARCHIVE",
                    "dialogue": "This map is moving.",
                    "sfx": None,
                    "lettering_notes": "caption top-left; bubble near Mira, not on face",
                }
            ],
            "reference_characters": ["Mira"],
        }
    )
    chars = {"Mira": CharacterAsset(name="Mira", l1_prompt="young woman, dark hair, blue pendant")}
    text = render_finished_page_prompt(
        plan,
        characters_by_name=chars,
        settings_by_name={},
        style_guide="manhua comic style",
    )
    assert "A4 portrait" in text or "portrait comic page" in text.lower()
    assert "MIDNIGHT AT THE SKY ARCHIVE" in text
    assert "This map is moving." in text
    assert "young woman, dark hair" in text
    assert "2x2" not in text.lower()  # renderer must not collapse intent to grid slogan
    assert "Wide top archive" in text
```

- [ ] **Step 2: Run — expect fail (module missing)**

- [ ] **Step 3: Implement `core/comic/page_prompt.py`**

```python
"""Deterministic finished-page image prompts from ComicPagePlan."""

from __future__ import annotations

from core.comic.identity import ensure_character_l1
from core.schemas import CharacterAsset, ComicPagePlan, Setting


def render_finished_page_prompt(
    plan: ComicPagePlan,
    *,
    characters_by_name: dict[str, CharacterAsset],
    settings_by_name: dict[str, Setting],
    style_guide: str = "",
) -> str:
    lines: list[str] = [
        "Finished readable manga/comic page, A4 portrait single image,",
        "dynamic panel layout with gutters (not a flat labeled grid collage),",
        "clean black ink line art, soft cel shading, flat colors,",
        "speech bubbles, caption boxes, and SFX lettered legibly in-image,",
        "do not cover faces, hands, or key action with text.",
    ]
    if style_guide:
        lines.append(f"Style: {style_guide}")
    lines.append(f"Page purpose: {plan.purpose}")
    lines.append(f"Layout intent: {plan.layout_intent}")
    for i, panel in enumerate(plan.panels, start=1):
        lines.append(
            f"Panel {i} ({panel.panel_id}): role={panel.role}, shape={panel.shape_hint}, "
            f"shot={panel.shot}, action={panel.action}"
        )
        if panel.setting_ref:
            setting = settings_by_name.get(panel.setting_ref)
            scene = getattr(setting, "scene_prompt", "") if setting else ""
            lines.append(f"  setting={panel.setting_ref}: {scene}".rstrip(": "))
        for name in panel.characters:
            asset = characters_by_name.get(name)
            if asset:
                ensure_character_l1(asset)
                if asset.l1_prompt:
                    lines.append(f"  character {name}: {asset.l1_prompt}")
        if panel.caption:
            lines.append(f"  CAPTION (exact): {panel.caption}")
        if panel.dialogue:
            lines.append(f"  DIALOGUE (exact): {panel.dialogue}")
        if panel.sfx:
            lines.append(f"  SFX (exact): {panel.sfx}")
        if panel.lettering_notes:
            lines.append(f"  lettering: {panel.lettering_notes}")
    return "\n".join(lines)
```

Tune wording so tests pass; keep function pure (no I/O).

- [ ] **Step 4: Tests pass**

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(page_prompt): render finished-page prompts from ComicPagePlan"
```

---

### Task 4: `plan_comic_pages` screenwriter tool

**Files:**
- Modify: `core/screenwriter.py`
- Create: `tests/test_screenwriter_pages.py`

**Interfaces:**
- Produces: `async def plan_comic_pages(text, elements, *, chat=None) -> ComicPagePlanSet`
- Tool name: `plan_comic_pages`

- [ ] **Step 1: Failing test with fake chat**

```python
import pytest
from core.schemas import StoryElements
from core.screenwriter import plan_comic_pages


class FakeChat:
    async def chat_function_call(self, messages, tools, tool_choice):
        assert tools[0]["function"]["name"] == "plan_comic_pages"
        return {
            "unit_id": "0",
            "pages": [
                {
                    "page_id": "p0001",
                    "purpose": "open on the street",
                    "layout_intent": "tall walking strip left; plaza wide right",
                    "panels": [
                        {
                            "panel_id": "1",
                            "role": "establishing",
                            "action": "walks",
                            "characters": ["福贵"],
                            "caption": "傍晚，街灯初上。",
                        }
                    ],
                    "reference_characters": ["福贵"],
                }
            ],
        }


@pytest.mark.asyncio
async def test_plan_comic_pages_validates_tool_payload():
    elements = StoryElements(characters=[], settings=[], style_guide="manhua")
    out = await plan_comic_pages("傍晚……", elements, chat=FakeChat())
    assert out.pages[0].page_id == "p0001"
    assert out.pages[0].panels[0].caption == "傍晚，街灯初上。"
```

- [ ] **Step 2: Run — expect fail**

- [ ] **Step 3: Implement**

Add imports for new schemas. Extend `SYSTEM_PROMPT` with one sentence: when planning finished pages, describe manga geometry (splash/inset/diagonal), never only `2x2`/`3x2`.

```python
PAGE_PLAN_TOOL = to_tool_schema(
    ComicPagePlanSet,
    "plan_comic_pages",
    "Plan finished comic pages for one text unit: per-page purpose, "
    "dynamic layout_intent, and panel specs with source-language lettering.",
)


async def plan_comic_pages(text: str, elements: StoryElements, *, chat=None) -> ComicPagePlanSet:
    chat = chat or get_chat_provider()
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"{sanitize_text(text)}\n\n"
                f"Known elements:\n{elements.model_dump_json()}\n\n"
                "Plan finished readable pages (not a flat 2x2 collage). "
                "Each page needs purpose, layout_intent, and panels with "
                "caption/dialogue/sfx in the source language."
            ),
        },
    ]
    args = await chat.chat_function_call(
        messages,
        [PAGE_PLAN_TOOL],
        _tool_choice("plan_comic_pages"),
    )
    return ComicPagePlanSet.model_validate(args)
```

- [ ] **Step 4: Tests pass** (use whatever asyncio marker the repo already uses; if sync-style tests dominate, mirror `tests/test_screenwriter.py` patterns)

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(screenwriter): add plan_comic_pages forced tool call"
```

---

### Task 5: Pipeline finished-page path + fingerprints

**Files:**
- Modify: `core/pipelines/creative_comic.py`
- Create: `tests/test_finished_page_pipeline.py`
- Modify: existing creative_comic tests to set `render_mode="panel_compose"` (or env) so defaults do not break them

**Interfaces:**
- Consumes: `plan_comic_pages`, `render_finished_page_prompt`, `render_mode()`, `finished_page_size()`
- Produces: pages under `output_dir/pages/page_XX.png`, `state.generated.pages`, `pages_done`
- Update `_render_fingerprint` to include `render_mode` and page size
- Bump `_PIPELINE_STATE_VERSION` if structure fingerprint must invalidate old caches (document in commit)

- [ ] **Step 1: Failing integration test**

```python
@pytest.mark.asyncio
async def test_finished_page_mode_writes_generated_pages(tmp_path, monkeypatch):
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "finished_page")
    # Fake chat returns elements + page plan; fake image returns solid PNG bytes via ImageOutput
    # Assert: (tmp_path / "pages").glob("page_*.png") non-empty
    # Assert: state.generated.pages and state.render_mode == "finished_page"
    # Assert: ExportEngine can build comic.pdf from pages dir (optional in this test)
```

Mirror fixtures from `tests/test_creative_comic.py` (FakeChat / FakeImage). Prefer minimal: one chunk, one page, one image call.

- [ ] **Step 2: Run — expect fail / still panel-only behavior**

- [ ] **Step 3: Implement pipeline branch**

Pseudocode inside `_creative_comic` after extract/portraits (finished mode skips `plan_storyboard` panel loop by default; still may keep storyboard optional later — **YAGNI: skip storyboard when finished_page**):

```python
mode = render_mode()
state.render_mode = mode  # persist

if mode == "finished_page":
    # for each chunk unit:
    #   cache hit page_cache[key] or plan_comic_pages(...)
    #   for each ComicPagePlan in set.pages:
    #     if page_id in pages_done and not stale: continue
    #     prompt = render_finished_page_prompt(...)
    #     refs = portrait paths for reference_characters
    #     out = await image.generate_single_image(prompt, refs, size=finished_page_size())
    #     save to pages/page_{n:02d}.png (global page counter)
    #     state.generated.pages[page_id] = GeneratedPage(...)
    #     pages_done.append(page_id); state.save
    # export: ExportEngine().export_pdf(pages_dir) without LayoutEngine collage
else:
    # existing storyboard → panels → LayoutEngine path
```

Handle `is_content_policy_rejection` → `skipped_pages`.  
No L3 on this path.  
Progress callbacks: stages `page_plan`, `pages`, `export`.  
Update `estimate_progress` to prefer `pages_done` when `render_mode=="finished_page"`.

- [ ] **Step 4: Opt existing tests into panel compose**

At top of panel-era tests or via fixture:
```python
monkeypatch.setenv("INKSTONE_RENDER_MODE", "panel_compose")
```
Or pass an explicit kwarg `render_mode=` on `creative_comic` if you add the parameter (preferred for tests):

```python
async def creative_comic(..., render_mode: str | None = None):
    mode = render_mode or config_render_mode()
```

- [ ] **Step 5: Full related pytest pass**

Run:  
`.venv/bin/python -m pytest tests/test_finished_page_pipeline.py tests/test_creative_comic.py tests/test_schemas_finished_page.py -q`

- [ ] **Step 6: Commit**

```bash
git commit -m "feat(pipeline): default finished-page generation with resume"
```

---

### Task 6: Fallback `panel_compose` from page plans (optional bridge) + export honesty

**Files:**
- Modify: `core/pipelines/creative_comic.py`
- Modify: `tests/test_finished_page_pipeline.py`
- Modify: `docs/ROADMAP.md`, `README.md` (short honesty notes)
- Modify: `web/server.py` project/job payload to include `pages_done`, `render_mode`

**Interfaces:**
- When finished-page image fails after one stricter retry **or** mode is `panel_compose` with only `page_cache` present: convert `PagePanelSpec` → temporary `Panel` list → existing panel renderer → `LayoutEngine` → mark pages `mode="composed_fallback"` if synthesizing page records

Keep v1 fallback simple:

- `panel_compose` mode: **keep current storyboard path** (already works).
- Finished-page failure: record skip or retry once; do **not** auto-build a full compose bridge in v1 unless cheap — spec allows fallback, but YAGNI says: env switch to `panel_compose` for recovery is enough for v1 if auto-bridge is large.

**v1 decision (lock):**  
- Auto compose-from-`PagePanelSpec` is **out of v1** if it balloons.  
- v1 fallback = set `INKSTONE_RENDER_MODE=panel_compose` and re-run (storyboard path).  
- Document this in README.  
- Still implement one stricter finished-page re-prompt on generic failure before skip/raise per existing error classes.

- [ ] **Step 1: Test stricter retry called once** (mock image fail then succeed)

- [ ] **Step 2: Implement retry-once + README/ROADMAP notes**

- [ ] **Step 3: Web JSON fields**

```python
"render_mode": state.render_mode,
"pages_done": list(state.pages_done),
"skipped_pages": list(state.skipped_pages),
```

- [ ] **Step 4: pytest + ruff**

```bash
.venv/bin/ruff check core tests
.venv/bin/ruff format --check core tests
.venv/bin/python -m pytest -q
```

- [ ] **Step 5: Commit**

```bash
git commit -m "feat: finished-page retry, web progress fields, docs honesty"
```

---

### Task 7: Verify against spec + smoke

- [ ] **Step 1: Spec coverage checklist**

Confirm each spec bullet has a task artifact:

| Spec item | Task |
|---|---|
| ComicPagePlan schemas / state | T1 |
| render_mode config | T2 |
| Deterministic prompt render | T3 |
| plan_comic_pages tool | T4 |
| Finished-page pipeline + resume | T5 |
| Fingerprints include mode/size | T5 |
| Export pages → PDF | T5 (ExportEngine reuse) |
| Fallback honesty / panel_compose | T6 |
| No L3 on pages | T5 |
| Web progress | T6 |
| README honesty | T6 |

- [ ] **Step 2: Full CI-equivalent locally**

```bash
.venv/bin/ruff check .
.venv/bin/ruff format --check core tests web utils scripts
.venv/bin/python -m pytest -q
```

- [ ] **Step 3: Final commit if any stragglers; do not push unless asked**

---

## Plan self-review

1. **Spec coverage:** All §1–§3 requirements mapped; auto `PagePanelSpec→compose` deferred explicitly as YAGNI with documented env fallback (matches “explicit fallback”, avoids half-copied studio compose).
2. **Placeholders:** None intentionally left; implementers must not invent studio file trees.
3. **Type consistency:** `ComicPagePlan` / `GeneratedPage` / `render_mode` names stable across tasks.
4. **Risk:** Agnes may not honor `1024x1536` — if provider rejects, catch and fall back to `1024x1024` once inside image call wrapper (add in Task 5 if tests with fake provider do not cover; real probe optional).
