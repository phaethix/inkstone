# Deferred Lettering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make finished-page comics letter with real CJK/Latin fonts (deferred overlay) and lock lettering language to the source before any image call — eliminating model-painted glyph distortion and CN/EN mix on the default path.

**Architecture:** Language-validate `ComicPagePlan` → blank-lettering image prompt → save blank art → `letter_finished_page` composites bubbles/text via shared LayoutEngine drawers → export lettered `pages/`. Planner emits normalized `lettering_boxes`; heuristics fill gaps.

**Tech Stack:** Python ≥3.10, Pydantic v2, Pillow, existing ChatProvider/ImageProvider, pytest, ruff.

**Spec:** `docs/superpowers/specs/2026-07-31-deferred-lettering-design.md`  
**Issue note (local):** `.issue/2026-07-31-10_14-deferred-lettering-cjk.md`

## Global Constraints

- English code, comments, commits (CONTRIBUTING).
- No new hard dependencies.
- Do not break `panel_compose` lettering behavior.
- Do not nest `page_cache` into `ChunkCache`.
- No CV bubble detection / OCR in this plan.
- TDD: failing test → implement → pass → commit per task.
- Old `state.json` without `blank_local` / `lettering_boxes` must still load.

## File map

| File | Responsibility |
|---|---|
| `core/schemas.py` | `LetteringBox`; extend `ComicPagePlan`, `GeneratedPage` |
| `core/comic/lettering_lang.py` | **New.** Source script detection + plan language validation / repair helpers |
| `core/comic/page_prompt.py` | Blank-lettering prompt mode (default) |
| `core/comic/page_lettering.py` | **New.** Overlay blank page + plan → lettered image |
| `core/comic/layout.py` | Share draw helpers with page_lettering (extract or call) |
| `core/screenwriter.py` | Stronger page-plan reminder; validate + one re-plan |
| `core/pipelines/creative_comic.py` | Blank save, overlay, fingerprint token, re-letter resume |
| `README.md`, `docs/ROADMAP.md` | Honesty: deferred lettering |
| `tests/test_lettering_lang.py` | Language lock unit tests |
| `tests/test_page_prompt.py` | Blank prompt contracts |
| `tests/test_page_lettering.py` | Overlay + heuristic boxes |
| `tests/test_schemas_finished_page.py` | Schema round-trip for boxes / blank_local |
| `tests/test_screenwriter_pages.py` | Re-plan on language mismatch |
| `tests/test_finished_page_pipeline.py` | End-to-end blank + lettered files |

---

### Task 1: Schema — LetteringBox + GeneratedPage.blank_local

**Files:**
- Modify: `core/schemas.py`
- Modify: `tests/test_schemas_finished_page.py`

**Interfaces:**
- Produces: `LetteringBox`, `ComicPagePlan.lettering_boxes`, `GeneratedPage.blank_local`, `GeneratedPage.mode` includes `"finished_lettered"`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_schemas_finished_page.py (additions)
from core.schemas import ComicPagePlan, GeneratedPage, LetteringBox, ProjectState


def test_lettering_box_and_plan_round_trip():
    plan = ComicPagePlan.model_validate(
        {
            "page_id": "p0001",
            "purpose": "establish",
            "layout_intent": "wide top",
            "panels": [
                {
                    "panel_id": "1",
                    "dialogue": "你好",
                    "action": "waves",
                }
            ],
            "lettering_boxes": [
                {
                    "kind": "dialogue",
                    "panel_id": "1",
                    "x": 0.1,
                    "y": 0.2,
                    "w": 0.4,
                    "h": 0.15,
                }
            ],
        }
    )
    assert plan.lettering_boxes[0].kind == "dialogue"
    assert LetteringBox.model_validate(plan.lettering_boxes[0].model_dump()).w == 0.4


def test_generated_page_blank_local_and_lettered_mode():
    page = GeneratedPage(
        local="/tmp/pages/page_c0000_p0000.png",
        blank_local="/tmp/pages/blank/page_c0000_p0000.png",
        page_id="p0001",
        mode="finished_lettered",
    )
    state = ProjectState(project_id="t", generated={"pages": {"c0000:p0001": page}})
    loaded = ProjectState.model_validate_json(state.model_dump_json())
    assert loaded.generated.pages["c0000:p0001"].blank_local.endswith("blank/page_c0000_p0000.png")
    assert loaded.generated.pages["c0000:p0001"].mode == "finished_lettered"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_schemas_finished_page.py::test_lettering_box_and_plan_round_trip tests/test_schemas_finished_page.py::test_generated_page_blank_local_and_lettered_mode -v`  
Expected: FAIL (`LetteringBox` missing / mode reject / `blank_local` missing)

- [ ] **Step 3: Minimal schema implementation**

In `core/schemas.py`, near `PagePanelSpec`:

```python
class LetteringBox(BaseModel):
    """Normalized page rectangle for deferred lettering overlay."""

    model_config = ConfigDict(extra="ignore")

    kind: Literal["caption", "dialogue", "sfx"]
    panel_id: str
    x: float = 0.0
    y: float = 0.0
    w: float = 0.4
    h: float = 0.12

    @field_validator("panel_id", mode="before")
    @classmethod
    def _coerce_panel_id(cls, value: Any) -> Any:
        return coerce_str(value)

    @field_validator("x", "y", "w", "h", mode="before")
    @classmethod
    def _coerce_float(cls, value: Any) -> Any:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0
```

On `ComicPagePlan` add `lettering_boxes: list[LetteringBox] = Field(default_factory=list)`.  
Include `"lettering_boxes"` in any fused-key repair allowlists if present.  
On `GeneratedPage`:

```python
mode: Literal["finished", "finished_lettered", "composed_fallback"] = "finished"
blank_local: str | None = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_schemas_finished_page.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add core/schemas.py tests/test_schemas_finished_page.py
git commit -m "$(cat <<'EOF'
feat: add LetteringBox and finished_lettered page fields

EOF
)"
```

---

### Task 2: Language lock helpers

**Files:**
- Create: `core/comic/lettering_lang.py`
- Create: `tests/test_lettering_lang.py`

**Interfaces:**
- Produces:
  - `source_lettering_script(text: str) -> Literal["cjk", "latin", "mixed", "unknown"]`
  - `lettering_field_mismatches(plan: ComicPagePlan, script: str) -> list[tuple[str, str, str]]`  
    (panel_id, kind, sample)
  - `strip_mismatched_lettering(plan: ComicPagePlan, script: str) -> ComicPagePlan`  
    (returns copy with bad fields set to None; boxes for those kinds dropped)

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_lettering_lang.py
from core.comic.lettering_lang import (
    lettering_field_mismatches,
    source_lettering_script,
    strip_mismatched_lettering,
)
from core.schemas import ComicPagePlan


def test_source_lettering_script_chinese_novel():
    assert source_lettering_script("第一章\n福贵在村口看着夕阳。") == "cjk"


def test_source_lettering_script_english():
    assert source_lettering_script("Chapter one. Fugui stood at the gate.") == "latin"


def test_mismatches_english_dialogue_on_chinese_source():
    plan = ComicPagePlan.model_validate(
        {
            "page_id": "p1",
            "panels": [
                {"panel_id": "1", "dialogue": "Hello there", "caption": "傍晚，村口。"}
            ],
        }
    )
    bad = lettering_field_mismatches(plan, "cjk")
    assert ("1", "dialogue", "Hello there") in bad
    assert not any(k == "caption" for _, k, _ in bad)


def test_strip_mismatched_lettering_drops_english_on_cjk():
    plan = ComicPagePlan.model_validate(
        {
            "page_id": "p1",
            "panels": [{"panel_id": "1", "dialogue": "Hello", "caption": "傍晚"}],
            "lettering_boxes": [
                {"kind": "dialogue", "panel_id": "1", "x": 0.1, "y": 0.1, "w": 0.3, "h": 0.1},
                {"kind": "caption", "panel_id": "1", "x": 0.1, "y": 0.0, "w": 0.5, "h": 0.1},
            ],
        }
    )
    fixed = strip_mismatched_lettering(plan, "cjk")
    assert fixed.panels[0].dialogue is None
    assert fixed.panels[0].caption == "傍晚"
    assert all(b.kind != "dialogue" for b in fixed.lettering_boxes)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_lettering_lang.py -v`  
Expected: FAIL (import error)

- [ ] **Step 3: Implement `core/comic/lettering_lang.py`**

```python
"""Detect source lettering script and validate ComicPagePlan lettering fields."""

from __future__ import annotations

import re
from typing import Literal

from core.comic.fonts import text_requires_cjk
from core.schemas import ComicPagePlan, LetteringBox, PagePanelSpec

Script = Literal["cjk", "latin", "mixed", "unknown"]

_LETTER_RE = re.compile(r"[A-Za-z\u00C0-\u024F\u4E00-\u9FFF\u3040-\u30FF\uAC00-\uD7AF]")


def source_lettering_script(text: str) -> Script:
    letters = _LETTER_RE.findall(text or "")
    if not letters:
        return "unknown"
    cjk = sum(1 for ch in letters if text_requires_cjk(ch))
    latin = len(letters) - cjk
    if cjk and not latin:
        return "cjk"
    if latin and not cjk:
        return "latin"
    ratio = cjk / len(letters)
    if ratio >= 0.15:
        return "cjk"
    if ratio <= 0.05:
        return "latin"
    return "mixed"


def _field_mismatch(text: str | None, script: Script) -> bool:
    if not text or not _LETTER_RE.search(text):
        return False
    has_cjk = text_requires_cjk(text)
    if script == "cjk":
        return not has_cjk
    if script == "latin":
        return has_cjk and sum(1 for ch in text if text_requires_cjk(ch)) >= max(
            1, len(_LETTER_RE.findall(text)) // 2
        )
    return False


def lettering_field_mismatches(
    plan: ComicPagePlan, script: Script
) -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []
    if script not in ("cjk", "latin"):
        return out
    for panel in plan.panels:
        for kind in ("caption", "dialogue", "sfx"):
            val = getattr(panel, kind)
            if _field_mismatch(val, script):
                out.append((panel.panel_id, kind, val or ""))
    return out


def strip_mismatched_lettering(plan: ComicPagePlan, script: Script) -> ComicPagePlan:
    bad = {(p, k) for p, k, _ in lettering_field_mismatches(plan, script)}
    panels: list[PagePanelSpec] = []
    for panel in plan.panels:
        data = panel.model_dump()
        for kind in ("caption", "dialogue", "sfx"):
            if (panel.panel_id, kind) in bad:
                data[kind] = None
        panels.append(PagePanelSpec.model_validate(data))
    boxes = [
        b
        for b in plan.lettering_boxes
        if (b.panel_id, b.kind) not in bad
    ]
    return plan.model_copy(update={"panels": panels, "lettering_boxes": boxes})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_lettering_lang.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add core/comic/lettering_lang.py tests/test_lettering_lang.py
git commit -m "$(cat <<'EOF'
feat: add finished-page lettering language lock helpers

EOF
)"
```

---

### Task 3: Blank finished-page prompt

**Files:**
- Modify: `core/comic/page_prompt.py`
- Modify: `tests/test_page_prompt.py`

**Interfaces:**
- Change: `render_finished_page_prompt(..., lettering: Literal["deferred", "in_image"] = "deferred")`
- Deferred mode: no exact CAPTION/DIALOGUE/SFX glyph lines; forbid readable text; may mention empty-bubble geometry from boxes/notes

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_page_prompt.py — replace/extend existing expectations
from core.comic.page_prompt import render_finished_page_prompt
from core.schemas import CharacterAsset, ComicPagePlan, Setting


def test_deferred_prompt_omits_glyph_strings_and_forbids_readable_text():
    plan = ComicPagePlan.model_validate(
        {
            "page_id": "p0001",
            "purpose": "establish",
            "layout_intent": "wide top",
            "panels": [
                {
                    "panel_id": "1",
                    "action": "福贵 walks",
                    "characters": ["福贵"],
                    "dialogue": "你好",
                    "caption": "傍晚",
                    "lettering_notes": "bubble near face — leave empty",
                }
            ],
            "lettering_boxes": [
                {"kind": "dialogue", "panel_id": "1", "x": 0.2, "y": 0.3, "w": 0.3, "h": 0.1}
            ],
        }
    )
    chars = {"福贵": CharacterAsset(name="福贵", l1_prompt="middle-aged farmer")}
    text = render_finished_page_prompt(plan, characters_by_name=chars, settings_by_name={})
    assert "CAPTION (exact):" not in text
    assert "DIALOGUE (exact):" not in text
    assert "你好" not in text
    assert "傍晚" not in text
    assert "no readable" in text.lower() or "empty speech" in text.lower()
    assert "empty" in text.lower()


def test_in_image_lettering_mode_still_includes_exact_strings():
    plan = ComicPagePlan.model_validate(
        {
            "page_id": "p0001",
            "purpose": "x",
            "layout_intent": "y",
            "panels": [{"panel_id": "1", "dialogue": "你好"}],
        }
    )
    text = render_finished_page_prompt(
        plan, characters_by_name={}, settings_by_name={}, lettering="in_image"
    )
    assert "DIALOGUE (exact): 你好" in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_page_prompt.py -v`  
Expected: FAIL (deferred still embeds exact strings / missing kwargs)

- [ ] **Step 3: Implement blank prompt**

Update `render_finished_page_prompt` signature and body:

```python
def render_finished_page_prompt(
    plan: ComicPagePlan,
    *,
    characters_by_name: dict[str, CharacterAsset],
    settings_by_name: dict[str, Setting],
    style_guide: str = "",
    strict: bool = False,
    lettering: str = "deferred",
) -> str:
    lines: list[str] = [
        "Finished readable manga/comic page, A4 portrait single image,",
        "dynamic panel layout with gutters (not a flat labeled grid collage),",
        "clean black ink line art, soft cel shading, flat colors,",
    ]
    if lettering == "deferred":
        lines.extend(
            [
                "empty speech bubbles and caption bars as chrome only — leave interiors blank,",
                "do not render any readable text, letters, or glyphs (no Latin, no CJK),",
                "do not cover faces, hands, or key action with chrome.",
            ]
        )
        if strict:
            lines.append(
                "STRICT: zero readable characters anywhere; high-contrast empty bubbles only."
            )
    else:
        lines.extend(
            [
                "speech bubbles, caption boxes, and SFX lettered legibly in-image,",
                "do not cover faces, hands, or key action with text.",
            ]
        )
        if strict:
            lines.append(
                "STRICT: render every CAPTION, DIALOGUE, and SFX string exactly as "
                "specified; high-contrast legible lettering; do not omit any text."
            )
    # ... style / purpose / panels ...
    # For deferred: skip exact CAPTION/DIALOGUE/SFX lines; emit geometry hints from boxes/notes.
    # For in_image: keep existing exact lines.
```

Update any existing `test_prompt_includes_layout_lettering_and_identity` to pass `lettering="in_image"` **or** assert deferred contracts instead — prefer updating the main test to deferred defaults and keep one in_image regression test.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_page_prompt.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add core/comic/page_prompt.py tests/test_page_prompt.py
git commit -m "$(cat <<'EOF'
feat: default finished-page prompts to blank lettering

EOF
)"
```

---

### Task 4: Overlay module `page_lettering`

**Files:**
- Create: `core/comic/page_lettering.py`
- Create: `tests/test_page_lettering.py`
- Modify: `core/comic/layout.py` (only if needed to reuse drawers without copy-paste — prefer constructing a tiny `LayoutEngine` and calling existing `_draw_*`)

**Interfaces:**
- Produces: `letter_finished_page(blank: Image.Image, plan: ComicPagePlan, *, font_path: str | None = None) -> Image.Image`
- Produces: `resolve_lettering_jobs(plan: ComicPagePlan) -> list[tuple[str, str, str, tuple[float,float,float,float]]]`  
  `(panel_id, kind, text, (x,y,w,h))`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_page_lettering.py
from PIL import Image

from core.comic.page_lettering import letter_finished_page, resolve_lettering_jobs
from core.schemas import ComicPagePlan


def test_resolve_uses_boxes_then_heuristics():
    plan = ComicPagePlan.model_validate(
        {
            "page_id": "p1",
            "panels": [
                {"panel_id": "1", "dialogue": "你好", "caption": "旁白"},
                {"panel_id": "2", "sfx": "砰"},
            ],
            "lettering_boxes": [
                {"kind": "dialogue", "panel_id": "1", "x": 0.1, "y": 0.2, "w": 0.3, "h": 0.1},
            ],
        }
    )
    jobs = resolve_lettering_jobs(plan)
    kinds = {(p, k) for p, k, _, _ in jobs}
    assert ("1", "dialogue") in kinds
    assert ("1", "caption") in kinds  # heuristic
    assert ("2", "sfx") in kinds
    dialogue = next(b for p, k, t, b in jobs if k == "dialogue")
    assert dialogue == (0.1, 0.2, 0.3, 0.1)


def test_letter_finished_page_draws_nonzero_ink():
    blank = Image.new("RGB", (200, 300), (240, 240, 240))
    plan = ComicPagePlan.model_validate(
        {
            "page_id": "p1",
            "panels": [{"panel_id": "1", "dialogue": "你好世界"}],
            "lettering_boxes": [
                {"kind": "dialogue", "panel_id": "1", "x": 0.1, "y": 0.1, "w": 0.6, "h": 0.2},
            ],
        }
    )
    out = letter_finished_page(blank, plan)
    assert out.size == blank.size
    # Bubble fill / text should change some pixels vs flat blank.
    assert list(out.getdata()) != list(blank.getdata())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_page_lettering.py -v`  
Expected: FAIL (import error)

- [ ] **Step 3: Implement overlay**

```python
# core/comic/page_lettering.py
"""Deferred lettering: composite plan text onto blank finished-page art."""

from __future__ import annotations

from PIL import Image, ImageDraw

from core.comic.layout import LayoutEngine
from core.schemas import ComicPagePlan

Kind = str  # "caption" | "dialogue" | "sfx"


def resolve_lettering_jobs(
    plan: ComicPagePlan,
) -> list[tuple[str, str, str, tuple[float, float, float, float]]]:
    box_map = {(b.panel_id, b.kind): (b.x, b.y, b.w, b.h) for b in plan.lettering_boxes}
    n = max(1, len(plan.panels))
    jobs: list[tuple[str, str, str, tuple[float, float, float, float]]] = []
    for i, panel in enumerate(plan.panels):
        band_y0 = i / n
        band_h = 1.0 / n
        for kind in ("caption", "dialogue", "sfx"):
            text = getattr(panel, kind)
            if not text:
                continue
            key = (panel.panel_id, kind)
            if key in box_map:
                box = _clamp_box(*box_map[key])
            else:
                box = _heuristic_box(kind, band_y0, band_h)
            jobs.append((panel.panel_id, kind, text, box))
    return jobs


def _clamp_box(x: float, y: float, w: float, h: float) -> tuple[float, float, float, float]:
    x = min(max(x, 0.0), 0.95)
    y = min(max(y, 0.0), 0.95)
    w = min(max(w, 0.08), 1.0 - x)
    h = min(max(h, 0.05), 1.0 - y)
    return (x, y, w, h)


def _heuristic_box(kind: str, band_y0: float, band_h: float) -> tuple[float, float, float, float]:
    if kind == "caption":
        return _clamp_box(0.1, band_y0 + 0.02 * band_h, 0.8, 0.22 * band_h)
    if kind == "sfx":
        return _clamp_box(0.55, band_y0 + 0.05 * band_h, 0.35, 0.2 * band_h)
    return _clamp_box(0.15, band_y0 + 0.55 * band_h, 0.7, 0.28 * band_h)


def letter_finished_page(
    blank: Image.Image,
    plan: ComicPagePlan,
    *,
    font_path: str | None = None,
) -> Image.Image:
    img = blank.convert("RGB").copy()
    draw = ImageDraw.Draw(img)
    engine = LayoutEngine(font_path=font_path)
    w, h = img.size
    for _pid, kind, text, (nx, ny, nw, nh) in resolve_lettering_jobs(plan):
        box = (int(nx * w), int(ny * h), max(8, int(nw * w)), max(8, int(nh * h)))
        if kind == "caption":
            engine._draw_caption(draw, box, text)
        elif kind == "sfx":
            engine._draw_sfx(draw, box, text)
        else:
            engine._draw_bubble(draw, box, text)
    return img
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_page_lettering.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add core/comic/page_lettering.py tests/test_page_lettering.py
git commit -m "$(cat <<'EOF'
feat: overlay deferred lettering onto blank finished pages

EOF
)"
```

---

### Task 5: Screenwriter — language reminder + one re-plan

**Files:**
- Modify: `core/screenwriter.py`
- Modify: `tests/test_screenwriter_pages.py` (create if missing)

**Interfaces:**
- Change: `plan_comic_pages` applies `source_lettering_script`, mismatch → one retry with hard reminder → `strip_mismatched_lettering`
- Tool description mentions `lettering_boxes` normalized rects

- [ ] **Step 1: Write the failing test**

```python
# tests/test_screenwriter_pages.py
import asyncio

from core.api import ChatProvider
from core.schemas import StoryElements
from core.screenwriter import plan_comic_pages


class FlipLangChat(ChatProvider):
    def __init__(self):
        self.calls = 0

    async def chat_function_call(self, messages, tools, tool_choice, **kw):
        self.calls += 1
        if self.calls == 1:
            return {
                "unit_id": "1",
                "pages": [
                    {
                        "page_id": "p1",
                        "purpose": "x",
                        "layout_intent": "wide",
                        "panels": [{"panel_id": "1", "dialogue": "Hello friend", "action": "stands"}],
                    }
                ],
            }
        return {
            "unit_id": "1",
            "pages": [
                {
                    "page_id": "p1",
                    "purpose": "x",
                    "layout_intent": "wide",
                    "panels": [{"panel_id": "1", "dialogue": "你好啊", "action": "stands"}],
                    "lettering_boxes": [
                        {"kind": "dialogue", "panel_id": "1", "x": 0.2, "y": 0.3, "w": 0.4, "h": 0.15}
                    ],
                }
            ],
        }


def test_plan_comic_pages_retries_once_on_language_mismatch():
    elements = StoryElements.model_validate(
        {"characters": [{"name": "福贵", "l1_prompt": "farmer"}], "settings": [], "style_guide": "manhua"}
    )
    chat = FlipLangChat()
    pageset = asyncio.run(plan_comic_pages("第一章\n福贵在村口。", elements, chat=chat))
    assert chat.calls == 2
    assert pageset.pages[0].panels[0].dialogue == "你好啊"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_screenwriter_pages.py::test_plan_comic_pages_retries_once_on_language_mismatch -v`  
Expected: FAIL (`calls == 1`)

- [ ] **Step 3: Wire validation into `plan_comic_pages`**

```python
async def plan_comic_pages(text: str, elements: StoryElements, *, chat=None) -> ComicPagePlanSet:
    chat = chat or get_chat_provider()
    script = source_lettering_script(text)
    lang_reminder = (
        "Reminder: caption / dialogue / sfx must match the source language "
        "(if the excerpt is Chinese, lettering must be Chinese — never English translation). "
        "Also emit lettering_boxes: normalized 0-1 page rectangles (kind, panel_id, x, y, w, h) "
        "for every non-null lettering field."
    )
    user = (
        f"{sanitize_text(text)}\n\n"
        f"Known elements:\n{elements.model_dump_json()}\n\n"
        "Plan finished readable pages (not a flat 2x2 collage). "
        "Each page needs purpose, layout_intent, panels, and lettering_boxes. "
        f"{lang_reminder}"
    )
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]
    args = await chat.chat_function_call(messages, [PAGE_PLAN_TOOL], _tool_choice("plan_comic_pages"))
    pageset = ComicPagePlanSet.model_validate(args)

    def _any_mismatch(ps: ComicPagePlanSet) -> bool:
        return any(lettering_field_mismatches(p, script) for p in ps.pages)

    if script in ("cjk", "latin") and _any_mismatch(pageset):
        logger.warning("plan_comic_pages language mismatch; retrying once (script=%s)", script)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": user + "\n\nCRITICAL: previous plan mixed languages. Fix lettering now.",
            },
        ]
        args = await chat.chat_function_call(
            messages, [PAGE_PLAN_TOOL], _tool_choice("plan_comic_pages")
        )
        pageset = ComicPagePlanSet.model_validate(args)

    if script in ("cjk", "latin"):
        pageset = pageset.model_copy(
            update={
                "pages": [strip_mismatched_lettering(p, script) for p in pageset.pages],
            }
        )
    return pageset
```

Also bump `SYSTEM_PROMPT` with one CRITICAL sentence on never mixing lettering languages (keep art-direction English allowance).

Update `PAGE_PLAN_TOOL` description to mention `lettering_boxes`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_screenwriter_pages.py tests/test_lettering_lang.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add core/screenwriter.py tests/test_screenwriter_pages.py
git commit -m "$(cat <<'EOF'
feat: lock finished-page plan lettering language with one retry

EOF
)"
```

---

### Task 6: Pipeline — blank save, overlay, fingerprint, resume re-letter

**Files:**
- Modify: `core/pipelines/creative_comic.py`
- Modify: `tests/test_finished_page_pipeline.py`

**Interfaces:**
- After successful blank image save: run `letter_finished_page`, write lettered `local`, set `blank_local`, `mode="finished_lettered"`
- `render_fingerprint` includes `lettering=deferred_v1`
- If page needs generation but `blank_local` exists and file present → skip image API, re-letter only
- Prompt calls use default deferred lettering (no glyph strings)

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_finished_page_pipeline.py additions
class RecordingImage(FakeImage):
    def __init__(self):
        super().__init__()
        self.prompts: list[str] = []

    async def generate_single_image(self, prompt, reference_image_paths=None, size=None, **kw):
        self.prompts.append(prompt)
        return await super().generate_single_image(prompt, reference_image_paths, size, **kw)


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_finished_page_writes_blank_and_lettered(tmp_path, monkeypatch):
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "finished_page")
    img = RecordingImage()
    proj = asyncio.run(
        creative_comic("第一章\n福贵在村口。", output_dir=str(tmp_path), chat=FakeChat(), image=img)
    )
    assert any("no readable" in p.lower() or "empty speech" in p.lower() for p in img.prompts)
    assert all("DIALOGUE (exact):" not in p for p in img.prompts)
    page_key = next(iter(proj.state.generated.pages))
    gp = proj.state.generated.pages[page_key]
    assert gp.mode == "finished_lettered"
    assert gp.blank_local and Path(gp.blank_local).exists()
    assert Path(gp.local).exists()
    assert Path(gp.blank_local).read_bytes() != Path(gp.local).read_bytes()


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_finished_page_reletters_from_blank_without_new_image(tmp_path, monkeypatch):
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "finished_page")
    img = RecordingImage()
    out = str(tmp_path)
    asyncio.run(creative_comic("第一章\n福贵在村口。", output_dir=out, chat=FakeChat(), image=img))
    first_prompts = len(img.prompts)
    # Drop lettered file + pages_done entry but keep blank → resume should re-letter only.
    state = ProjectState.load(tmp_path / "state.json")
    key = next(iter(state.generated.pages))
    Path(state.generated.pages[key].local).unlink()
    state.pages_done = [k for k in state.pages_done if k != key]
    # keep blank_local file and generated entry blank path
    state.generated.pages[key].local = str(tmp_path / "pages" / "missing.png")
    state.save(tmp_path / "state.json")
    img2 = RecordingImage()
    proj2 = asyncio.run(creative_comic("第一章\n福贵在村口。", output_dir=out, chat=FakeChat(), image=img2))
    assert img2.prompts == []  # no new page image calls (portrait may still 0 if cached)
    assert Path(proj2.state.generated.pages[key].local).exists()
```

Adjust FakeChat so plans include Chinese dialogue (already does via captions). Ensure page plan from FakeChat includes at least one Chinese lettering field so overlay changes pixels.

Update `FakeChat.plan_comic_pages` return to include `dialogue`/`caption` Chinese strings if not already.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_finished_page_pipeline.py::test_finished_page_writes_blank_and_lettered -v`  
Expected: FAIL (`mode != finished_lettered` / no blank_local)

- [ ] **Step 3: Pipeline wiring**

In finished-page loop after image success:

```python
blank_dir = pages_dir / "blank"
blank_dir.mkdir(parents=True, exist_ok=True)
blank_path = _page_asset_path(blank_dir, ci, page_index)
await asyncio.to_thread(out.save, str(blank_path))
from PIL import Image as PILImage
from core.comic.page_lettering import letter_finished_page

blank_img = await asyncio.to_thread(PILImage.open, str(blank_path))
lettered = await asyncio.to_thread(letter_finished_page, blank_img, plan)
local = _page_asset_path(pages_dir, ci, page_index)
await asyncio.to_thread(lettered.save, str(local))
state.generated.pages[state_key] = GeneratedPage(
    local=str(local),
    blank_local=str(blank_path),
    page_id=page_id,
    unit_index=ci,
    page_index=page_index,
    mode="finished_lettered",
    # snapshot lettering fields as today if applicable
)
```

Before image call, if `_page_needs_generation` and existing `blank_local` is valid:

```python
existing = state.generated.pages.get(state_key)
if existing and existing.blank_local and _is_within(existing.blank_local, output_dir) and Path(existing.blank_local).is_file():
    blank_img = await asyncio.to_thread(PILImage.open, existing.blank_local)
    lettered = await asyncio.to_thread(letter_finished_page, blank_img, plan)
    local = _page_asset_path(pages_dir, ci, page_index)
    await asyncio.to_thread(lettered.save, str(local))
    # update GeneratedPage local/mode; mark done; continue
```

Add `lettering=deferred_v1` into whatever string builds `render_fingerprint` (find existing helper in `creative_comic.py` / config fingerprint and append).

Stop passing in-image exact strings (default prompt already deferred).

- [ ] **Step 4: Run finished-page tests**

Run: `.venv/bin/python -m pytest tests/test_finished_page_pipeline.py tests/test_page_prompt.py tests/test_page_lettering.py -v`  
Expected: PASS (update any assertions that required `DIALOGUE (exact)` in prompts)

- [ ] **Step 5: Commit**

```bash
git add core/pipelines/creative_comic.py tests/test_finished_page_pipeline.py
git commit -m "$(cat <<'EOF'
feat: wire deferred lettering into finished-page pipeline

EOF
)"
```

---

### Task 7: Docs honesty + full verification

**Files:**
- Modify: `README.md`
- Modify: `docs/ROADMAP.md`
- Modify: `docs/README.md` only if specs index needs a link

- [ ] **Step 1: Update README finished-page blurb**

Replace in-image lettering claims with:

> Finished-page mode generates whole-page art with empty lettering chrome; Inkstone overlays caption/dialogue/sfx using real fonts (CJK-capable). This avoids model-painted glyph distortion. Set `INKSTONE_RENDER_MODE=panel_compose` for the legacy per-panel path.

Document that `pages/blank/` holds unlettered art for resume re-lettering.

- [ ] **Step 2: ROADMAP checkbox**

Mark deferred lettering for finished pages as done / in progress note with link to the spec.

- [ ] **Step 3: Full test + lint**

Run:

```bash
.venv/bin/python -m pytest tests/test_schemas_finished_page.py tests/test_lettering_lang.py tests/test_page_prompt.py tests/test_page_lettering.py tests/test_screenwriter_pages.py tests/test_finished_page_pipeline.py -q
.venv/bin/ruff check core/schemas.py core/comic/lettering_lang.py core/comic/page_prompt.py core/comic/page_lettering.py core/screenwriter.py core/pipelines/creative_comic.py
.venv/bin/ruff format --check core/comic/lettering_lang.py core/comic/page_lettering.py
```

Expected: all PASS / clean

- [ ] **Step 4: Commit**

```bash
git add README.md docs/ROADMAP.md docs/superpowers/specs/2026-07-31-deferred-lettering-design.md docs/superpowers/plans/2026-07-31-deferred-lettering.md
git commit -m "$(cat <<'EOF'
docs: document deferred lettering for finished pages

EOF
)"
```

---

## Plan self-review

1. **Spec coverage:** Language lock → Task 2+5; blank prompt → Task 3; boxes schema → Task 1; overlay + heuristics → Task 4; pipeline/resume/fingerprint → Task 6; honesty docs → Task 7. CV/OCR explicitly absent.
2. **Placeholders:** None intentional; concrete tests and code in each task.
3. **Type consistency:** `LetteringBox`, `finished_lettered`, `blank_local`, `letter_finished_page`, `source_lettering_script` names align across tasks.

## Execution handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-31-deferred-lettering.md`. Two execution options:

1. **Subagent-Driven (recommended)** — fresh subagent per task, review between tasks  
2. **Inline Execution** — execute tasks in this session with checkpoints  

Which approach?
