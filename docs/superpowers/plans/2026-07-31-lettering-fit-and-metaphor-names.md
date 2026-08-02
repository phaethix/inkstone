# Lettering Fit + Metaphorical Name Identity — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Stop deferred-lettering chrome from filling oversized plan boxes (covering art), and stop image models from literalizing metaphorical Chinese names like 虎妞 into animals.

**Architecture:** Shrink-wrap dialogue/caption chrome to measured text within the plan box as a max/anchor; harden extract + portrait/L1 prompts so animal-character names stay human; bump render identity token so bad portraits soft-invalidate on re-run.

**Tech Stack:** Python ≥3.10, Pillow, existing LayoutEngine drawers, pytest.

**Issue note:** `.issue/2026-07-31-14_56-lettering-bubble-and-huniu.md`  
**Repro:** `comic_out/efb7cc24b763` page `page_c0009_p0014.png`, character 虎妞.

## Global Constraints

- English code, comments, commits.
- No new hard dependencies.
- Do not break `panel_compose` bubble sizing for its own cell layout (only change deferred overlay path / shared helpers carefully).
- TDD per task.
- Prefer re-letter from `blank_local` when only lettering version changes.

## File map

| File | Responsibility |
|---|---|
| `core/comic/page_lettering.py` | Shrink-wrap fitted boxes; `LETTERING_VERSION` |
| `core/comic/layout.py` | Optional: expose measure helpers if needed (prefer using `_bubble_height`) |
| `core/comic/identity.py` | `harden_human_identity_prompt` / name metaphor detection |
| `core/screenwriter.py` | Extract/system reminder for metaphorical names |
| `core/pipelines/creative_comic.py` | Portrait prompt harden; fingerprint `identity=metaphor_v1`; re-letter on version mismatch |
| `core/schemas.py` | `GeneratedPage.lettering_version` optional |
| `tests/test_page_lettering.py` | Shrink-wrap vs huge plan box |
| `tests/test_identity_metaphor.py` | 虎妞 / 凤姐 human harden |
| `tests/test_finished_page_pipeline.py` | Fingerprint / re-letter version if wired |

---

### Task 1: Shrink-wrap deferred lettering chrome

**Files:**
- Modify: `core/comic/page_lettering.py`
- Modify: `tests/test_page_lettering.py`

**Interfaces:**
- `LETTERING_VERSION = "deferred_v2"`
- `fit_lettering_box(engine, kind, text, anchor_xywh_px, page_wh) -> (x,y,w,h)` — chrome sized to text, clamped to anchor max and page caps (`max_w_frac=0.45`, `max_h_frac=0.28`)

- [ ] **Step 1: Failing test**

```python
def test_letter_finished_page_shrink_wraps_huge_plan_box():
    blank = Image.new("RGB", (200, 400), (200, 200, 200))
    plan = ComicPagePlan.model_validate(
        {
            "page_id": "p1",
            "panels": [
                {
                    "panel_id": "2",
                    "dialogue": "假如老头子消了气呢？",
                }
            ],
            "lettering_boxes": [
                {"kind": "dialogue", "panel_id": "2", "x": 0.1, "y": 0.5, "w": 0.8, "h": 0.4},
            ],
        }
    )
    out = letter_finished_page(blank, plan)
    # Count near-white pixels in bottom half — must be far less than filling the 0.8x0.4 box.
    bottom = out.crop((0, 200, 200, 400))
    whiteish = sum(1 for px in bottom.getdata() if px[0] > 240 and px[1] > 240 and px[2] > 240)
    # Full 0.8*200 x 0.4*400 = 160*160 = 25600 white if filled; shrink-wrap should be << that.
    assert whiteish < 8000
```

- [ ] **Step 2: Implement fit + use in `letter_finished_page`**

Algorithm:

1. Convert normalized anchor → pixel rect (existing).
2. Cap `max_w = min(anchor_w, int(page_w * 0.45))`, `max_h = min(anchor_h, int(page_h * 0.28))`.
3. `need_h = engine._bubble_height(text, max_w)` (caption uses same); `need_h = min(need_h, max_h)`.
4. Optionally tighten width: binary search / measure longest line after wrap at `max_w`, set `w = min(max_w, longest + 2*pad)`.
5. Place fitted box at top-left of anchor (keep `x,y` of anchor); clamp so it stays on page.
6. Draw with fitted rect, not full anchor.

- [ ] **Step 3: Tests pass + commit**

```bash
git commit -m "fix: shrink-wrap deferred lettering chrome to text size"
```

---

### Task 2: Metaphorical Chinese names stay human

**Files:**
- Modify: `core/comic/identity.py`
- Create: `tests/test_identity_metaphor.py`
- Modify: `core/screenwriter.py` (SYSTEM_PROMPT one sentence)
- Modify: `core/pipelines/creative_comic.py` (portrait + fingerprint)
- Modify: `core/comic/page_prompt.py` (optional: append human lock on character lines when metaphor name)

**Interfaces:**
- `name_suggests_animal_metaphor(name: str) -> bool` — true if name contains common metaphor animals (虎/龙/凤/豹/狼/狮/猴/蛇/…）AND is not already marked non-human in role
- `harden_human_identity_prompt(name: str, prompt: str) -> str` — append/ensure `human character (name is metaphorical; not an animal, no animal head)`

- [ ] **Step 1: Failing tests**

```python
def test_harden_huniu_prompt_forbids_tiger():
    out = harden_human_identity_prompt("虎妞", "虎妞, sturdy woman in traditional clothes")
    assert "not an animal" in out.lower() or "human" in out.lower()
    assert "tiger" in out.lower()  # explicit negation: "not a tiger" preferred

def test_ordinary_name_unchanged_enough():
    base = "middle-aged farmer in patched jacket"
    assert harden_human_identity_prompt("祥子", base) == base or "human" in harden_human_identity_prompt("祥子", base).lower()
```

Prefer: only harden when `name_suggests_animal_metaphor`; 祥子 unchanged.

- [ ] **Step 2: Wire**

- `SYSTEM_PROMPT`: metaphorical names (虎妞, 凤姐) are human unless source says otherwise; l1/portrait must say human, not animal.
- Portrait render: `prompt = harden_human_identity_prompt(name, asset.portrait_prompt or asset.l1_prompt)`
- `ensure_character_l1` or call site after ensure: harden `l1_prompt` when metaphor
- `_render_fingerprint`: add `"identity": "metaphor_v1"` always (or when finished_page / always — always is fine so panel_compose also refreshes bad portraits)

- [ ] **Step 3: Commit**

```bash
git commit -m "fix: keep metaphorical Chinese names human in identity prompts"
```

---

### Task 3: Re-letter on version bump; verify

**Files:**
- Modify: `core/schemas.py` — `GeneratedPage.lettering_version: str = ""`
- Modify: `core/pipelines/creative_comic.py` — after lettering, set version; if blank valid and version != LETTERING_VERSION, re-letter even when in pages_done? Prefer: treat outdated version as needs generation for lettering-only path without image.
- Modify tests
- Fingerprint: bump finished-page `lettering` to `deferred_v2` (with identity bump this soft-invalidates once — acceptable for repro project)

- [ ] Wire + test that shrink-wrapped lettering_version is stored
- [ ] Run focused pytest + ruff
- [ ] Commit docs pointer in ROADMAP one-liner optional

```bash
git commit -m "fix: bump deferred lettering version and persist on GeneratedPage"
```

---

## Self-review

- Spec coverage: huge box → Task 1; 虎妞 → Task 2; re-run path → Task 3.
- No CV bubble detection (still out of scope).
