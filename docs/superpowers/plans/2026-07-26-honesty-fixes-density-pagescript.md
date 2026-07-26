# Honesty Fixes for Density & PageScript Prototype

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix product-mismatched honesty bugs in the already-shipped D1/D2 prototypes so they cannot be mistaken for release-quality long-form adaptation — without implementing P0 density contracts or P1 beat architecture.

**Architecture:** Keep density as an offline estimator and PageScript as optional migration metadata. Align A/B/C labels with the product brief, turn page-script generation **off by default** in `creative_comic`, stop treating policy-skipped pages as vacuous coverage success, and rewrite CLI/ROADMAP wording so prototypes are labeled as estimates/audit notes only.

**Tech Stack:** Python 3.10+, existing `core.density` / `core.comic.coverage` / `creative_comic` / `core.cli`, pytest, ruff.

## Global Constraints

- Do **not** implement density→fingerprint / planning budget (that is P0 new work).
- Do **not** introduce `SourceUnit` / `NarrativeBeat` / lettering (that is P1).
- Product density semantics (authoritative): **A = 主线概览 (sparser)** · **B = 章级完整 default** · **C = 近原著 (denser)**.
- PageScript must not claim to be an information gate; default pipeline must not spend chat quota on it.
- Policy-rejected / skipped pages must **not** be removed from coverage denominators as vacuous pass.
- Commits: one logical change per commit; Conventional Commits; English messages; only commit when the user asks (or after they approve this plan’s execution).
- Process: for any future code-change task, write/update a plan under `docs/superpowers/plans/` **before** editing product code.
- Verification before claiming done: `.venv/bin/ruff check .`, `.venv/bin/ruff format --check core/ tests/`, `.venv/bin/python -m pytest -q`.

## Out of scope (explicit)

| Item | Why deferred |
|------|----------------|
| Persist density on `ProjectState` + fingerprint | P0 “density contract” |
| Pass panel budget into storyboard prompts | P0 |
| Calibrate panels/chunk on a public sample | P0 (needs real run data) |
| SourceUnit / AdaptationPlan / beat storyboard | P1 |
| CBZ / chapter reader | P2 |
| Publishing local architecture/onboarding docs to git | Separate docs policy |

## File map

| File | Responsibility in this fix |
|------|----------------------------|
| `core/density.py` | Flip A/C panel counts + descriptions to match product |
| `tests/test_density.py` | Assert A sparsest, C densest; new descriptions |
| `tests/test_density_boundary.py` | Any A/C assumptions that break after flip |
| `core/pipelines/creative_comic.py` | Gate `plan_page_script` behind env; rename comments |
| `tests/test_creative_comic.py` | Default path: no page_script chat calls |
| `tests/test_d2_pipeline.py` | Opt-in env enables page_script path |
| `core/comic/coverage.py` | Skipped pages count as incomplete, not excluded |
| `tests/test_d2_coverage.py` | Invert skipped-pages expectation |
| `core/cli.py` | Honest help text for `plan` / `coverage` |
| `core/schemas.py` | Soften PageScript/Coverage docstrings (no “闸门”) |
| `docs/ROADMAP.md` | Status: prototypes on `main` but not product features |

---

### Task 1: Align density A/B/C with the product brief

**Files:**
- Modify: `core/density.py` (constants + `get_density_plan` descriptions)
- Modify: `tests/test_density.py`
- Modify: `tests/test_density_boundary.py` (only if A/C-specific assertions break)

**Interfaces:**
- Consumes: existing `DensityTier = Literal["A","B","C"]`, `get_density_plan`, `estimate`
- Produces: unchanged function signatures; new semantics:
  - `PANELS_PER_CHUNK_A = 3` (主线概览)
  - `PANELS_PER_CHUNK_B = 8` (章级完整 / 标准)
  - `PANELS_PER_CHUNK_C = 14` (近原著)
  - Descriptions: `"主线概览"`, `"章级完整"`, `"近原著"`

- [ ] **Step 1: Write the failing test**

In `tests/test_density.py`, replace `test_get_density_plan_constants` and monotonic assertion with:

```python
def test_get_density_plan_constants():
    a = get_density_plan("A")
    b = get_density_plan("B")
    c = get_density_plan("C")
    assert a.panels_per_chunk == PANELS_PER_CHUNK_A == 3
    assert b.panels_per_chunk == PANELS_PER_CHUNK_B == 8
    assert c.panels_per_chunk == PANELS_PER_CHUNK_C == 14
    assert a.description == "主线概览"
    assert b.description == "章级完整"
    assert c.description == "近原著"


def test_tier_a_b_c_panel_counts(tmp_path):
    book = _write(tmp_path, _BODY)
    est_a = estimate(book, density="A")
    est_b = estimate(book, density="B")
    est_c = estimate(book, density="C")
    assert est_a.chunks == est_b.chunks == est_c.chunks
    assert est_a.panels == est_a.chunks * PANELS_PER_CHUNK_A
    assert est_b.panels == est_b.chunks * PANELS_PER_CHUNK_B
    assert est_c.panels == est_c.chunks * PANELS_PER_CHUNK_C
    # Product order: A overview < B chapter-complete < C near-original
    assert est_a.panels < est_b.panels < est_c.panels
```

Also add a short module comment in the test file that A/B/C follow the product brief, not the old inverted prototype.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_density.py::test_get_density_plan_constants tests/test_density.py::test_tier_a_b_c_panel_counts -v`

Expected: FAIL (still A=14 / “主线完备” / `est_a > est_c`)

- [ ] **Step 3: Write minimal implementation**

In `core/density.py`:

```python
# Product brief alignment (not yet a generation contract — estimate only):
# A 主线概览 (sparser) → B 章级完整 (default) → C 近原著 (denser).
# Numbers remain uncalibrated experience values.
PANELS_PER_CHUNK_A = 3
PANELS_PER_CHUNK_B = 8
PANELS_PER_CHUNK_C = 14
```

Update `get_density_plan` mapping:

```python
mapping: dict[DensityTier, tuple[str, int]] = {
    "A": ("主线概览", PANELS_PER_CHUNK_A),
    "B": ("章级完整", PANELS_PER_CHUNK_B),
    "C": ("近原著", PANELS_PER_CHUNK_C),
}
```

Update the module docstring note that previously said A was “主线完备” / C “极简”.

In `estimate` docstring / CLI-facing warnings path: ensure existing tiny-file warnings still work. Optionally append a stable advisory when printing is handled in CLI (Task 4); do not invent new estimate fields here.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_density.py tests/test_density_boundary.py -q`

Expected: PASS. If `test_density_boundary` hardcodes A denser than C, flip those assertions in the same task.

- [ ] **Step 5: Commit** (when user asks)

```bash
git add core/density.py tests/test_density.py tests/test_density_boundary.py
git commit -m "$(cat <<'EOF'
fix(density): align A/B/C tiers with product brief

A is overview (sparser), C is near-original (denser); still estimate-only.
EOF
)"
```

---

### Task 2: Disable PageScript in the default generate path

**Files:**
- Modify: `core/pipelines/creative_comic.py` (page_script block ~651–668)
- Modify: `tests/test_creative_comic.py` (chat call count)
- Modify: `tests/test_d2_pipeline.py` (opt-in via env)

**Interfaces:**
- Consumes: existing `plan_page_script`, `ChunkCache.page_script`
- Produces: page-script generation only when `INKSTONE_PAGE_SCRIPT` is truthy (`1`/`true`/`yes`/`on`); default off

- [ ] **Step 1: Write the failing tests**

In `tests/test_creative_comic.py`, change the resume test expectation back to extract+storyboard only:

```python
assert chat.calls == 4  # 2 extract + 2 storyboard (page_script off by default)
```

In `tests/test_d2_pipeline.py`, at the start of tests that require page_script:

```python
monkeypatch.setenv("INKSTONE_PAGE_SCRIPT", "1")
```

Add a new test:

```python
def test_d2_pipeline_skips_page_script_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("INKSTONE_PAGE_SCRIPT", raising=False)
    # …same fakes/setup as write/resume test…
    # After generate: every chunk_cache[*].page_script is None
    # chat.calls == 4 for two chunks (no plan_page_script)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_creative_comic.py::test_creative_comic_generates_and_resumes tests/test_d2_pipeline.py -v`

Expected: FAIL until pipeline is gated (`chat.calls == 6` still, or new default-skip test fails).

- [ ] **Step 3: Write minimal implementation**

Near other env reads in `creative_comic` (alongside L3), add:

```python
def _page_script_enabled() -> bool:
    return os.environ.get("INKSTONE_PAGE_SCRIPT", "0").strip().lower() in {
        "1", "true", "yes", "on",
    }
```

Replace the unconditional page-script block with:

```python
# Optional legacy PageScript metadata (NOT a quality gate). Off by default.
if _page_script_enabled() and state.chunk_cache.get(key, ChunkCache()).page_script is None:
    try:
        with perf.measure("page_script"):
            ps = await plan_page_script(board, elements, chunk, chat=chat)
    except Exception as exc:  # noqa: BLE001
        if is_content_policy_rejection(exc):
            logger.warning(
                "chunk %s legacy page_script rejected by policy; recording empty script",
                ci,
            )
            ps = PageScript(
                chapter_id=board.chapter_id,
                pages=[],
                skipped_pages=list(range((len(board.panels) + 3) // 4)),
            )
        else:
            raise
    state.chunk_cache[key].page_script = ps
    state.save(state_path)
    _report("page_script", _pct())
```

Do not delete `plan_page_script` or schema fields — keep for migration / opt-in experiments.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_creative_comic.py tests/test_d2_pipeline.py -q`

Expected: PASS

- [ ] **Step 5: Commit** (when user asks)

```bash
git add core/pipelines/creative_comic.py tests/test_creative_comic.py tests/test_d2_pipeline.py
git commit -m "$(cat <<'EOF'
fix(pipeline): keep legacy page_script opt-in only

Default generate path no longer spends chat quota on post-storyboard metadata.
EOF
)"
```

---

### Task 3: Stop vacuous-pass for skipped coverage pages

**Files:**
- Modify: `core/comic/coverage.py`
- Modify: `tests/test_d2_coverage.py`
- Modify: `core/schemas.py` (docstrings on `skipped_pages` / `CoverageMetric` only)

**Interfaces:**
- Consumes: `PageScript.skipped_pages`, `compute_coverage_report(...)`
- Produces: same return type; skipped pages still contribute to denominators as **uncovered** (and appear in `below_threshold_pages`)

- [ ] **Step 1: Write the failing test**

Replace `test_skipped_pages_excluded_from_denominator` with:

```python
def test_skipped_pages_count_as_uncovered():
    ps = [
        PageScript(
            chapter_id="c1",
            pages=[
                PageScriptPage(
                    page_index=0,
                    required_information="x",
                    causal_links=[CausalLink(cause="a", effect="b")],
                    source_spans=[SourceSpan(start=0, end=3, text="方鸿渐")],
                )
            ],
            skipped_pages=[0],
        )
    ]
    report = compute_coverage_report(ps, SOURCE)
    # Still in the denominator; skipped ≠ success
    assert report.required_coverage.total == 1
    assert report.required_coverage.covered == 0
    assert report.required_coverage.coverage_ratio == 0.0
    assert report.required_coverage.passed is False
    assert any("p0" in k for k in report.below_threshold_pages)
```

Keep `SOURCE` as in the existing file (`方鸿渐…`).

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_d2_coverage.py::test_skipped_pages_count_as_uncovered -v`

Expected: FAIL (current code yields vacuous 1.0)

- [ ] **Step 3: Write minimal implementation**

In `compute_coverage_report`, remove the `continue` that skips denominator accounting for `pi in skipped`. Instead, when `pi in skipped`:

```python
if pi in skipped:
    req_total += 1
    # covered stays 0
    # still attribute failure
    below.append(key)
    # Do not score causal/span entries on skipped pages as successes;
    # if the page has causal/span lists, count them in totals with 0 covered,
    # OR treat the whole page as a single required failure only.
    # Prefer: page-level required failure + still iterate links/spans as uncovered.
    for link in page.causal_links:
        cau_total += 1
    for sp in page.source_spans:
        spa_total += 1
    continue
```

If a skipped page has empty `pages` (policy rejection path that stores `pages=[]` + `skipped_pages=[…]`), also count each skipped index as one uncovered required page even without a `PageScriptPage` object:

```python
# After iterating pages, for any skipped index with no page object:
for pi in sorted(skipped):
    if pi >= len(ps.pages):
        key = f"c{ci:04d}#{ps.chapter_id}#p{pi}"
        req_total += 1
        below.append(key)
```

Update module docstring: remove “排除出三项分母（vacuous 通过）”.

In `core/schemas.py`, change comments that say skipped pages are excluded / vacuous pass.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_d2_coverage.py -q`

Expected: PASS

- [ ] **Step 5: Commit** (when user asks)

```bash
git add core/comic/coverage.py tests/test_d2_coverage.py core/schemas.py
git commit -m "$(cat <<'EOF'
fix(coverage): treat policy-skipped pages as uncovered

Skipped pages remain in the denominator so vacuous pass cannot hide gaps.
EOF
)"
```

---

### Task 4: Honest CLI + ROADMAP wording

**Files:**
- Modify: `core/cli.py` (`plan` / `coverage` help strings and printed banners)
- Modify: `docs/ROADMAP.md` (status table + guardrail; note prototypes are on `main`)
- Optional soft touch: `core/schemas.py` class docstrings already partly done in Task 3

**Interfaces:**
- Consumes: existing `_run_plan`, `_run_coverage`
- Produces: no new commands; clearer user-facing copy

- [ ] **Step 1: Write the failing test**

In `tests/test_d2_cli.py` (or `tests/test_density_boundary.py` argparse check), assert help text:

```python
def test_cli_help_marks_prototypes_honestly():
    from core.cli import _build_parser
    parser = _build_parser()
    plan_help = parser._subparsers._group_actions[0].choices["plan"].format_help()
    cov_help = parser._subparsers._group_actions[0].choices["coverage"].format_help()
    assert "estimate" in plan_help.lower() or "预估" in plan_help
    assert "not a quality gate" in cov_help.lower() or "非质量闸门" in cov_help or "原型" in cov_help
    dens = parser._subparsers._group_actions[0].choices["plan"].format_help()
    assert "主线概览" in dens or "A=" in dens or "overview" in dens.lower() or "概览" in dens
```

If argparse help plumbing is awkward, test the printed plan preamble string instead by capturing stdout from `_run_plan` and asserting it contains a calibration / estimate-only warning.

Also assert density help lists product order, e.g. help for `--density` becomes:

```text
A=主线概览(少格) B=章级完整(默认) C=近原著(多格); estimate only, does not control generate
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_d2_cli.py -k honest -v` (or the new test name)

Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

Update `core/cli.py`:

- `plan` help: `Offline density/cost/duration estimate (does not control generate).`
- `--density` help: product-aligned Chinese/English short form above.
- After printing estimate numbers, always print one warning line:
  `Note: uncalibrated estimate only; generate ignores --density until the density contract lands.`
- `coverage` help: `Legacy PageScript field report (prototype; not a readability/quality gate).`
- When no `page_script` in state, message should say enable `INKSTONE_PAGE_SCRIPT=1` on generate if they need legacy metadata — not that coverage “failed the gate”.

Update `docs/ROADMAP.md` status rows:

| Density estimate (D1) | Prototype on `main` | CLI estimator only; A/B/C labels match product brief; does not constrain generate |
| Old PageScript / coverage (D2) | Prototype on `main` — do not treat as gate | Opt-in via `INKSTONE_PAGE_SCRIPT=1`; coverage never vacuous-passes skips |

Update Local prototype → “Prototype on main” definition note if needed so “Local prototype” is not falsely used for committed code.

- [ ] **Step 4: Run tests + lint**

Run:

```bash
.venv/bin/python -m pytest tests/test_d2_cli.py tests/test_density.py tests/test_d2_coverage.py tests/test_creative_comic.py tests/test_d2_pipeline.py -q
.venv/bin/ruff check core/density.py core/comic/coverage.py core/pipelines/creative_comic.py core/cli.py core/schemas.py tests/
.venv/bin/ruff format --check core/density.py core/comic/coverage.py core/pipelines/creative_comic.py core/cli.py core/schemas.py tests/test_density.py tests/test_d2_coverage.py tests/test_d2_cli.py tests/test_creative_comic.py tests/test_d2_pipeline.py
```

Expected: all green

- [ ] **Step 5: Commit** (when user asks)

```bash
git add core/cli.py docs/ROADMAP.md tests/test_d2_cli.py
git commit -m "$(cat <<'EOF'
docs: label density and PageScript as estimate/prototype only

EOF
)"
```

---

### Task 5: Full verification

**Files:** none new

- [ ] **Step 1: Full test suite**

Run: `.venv/bin/python -m pytest -q`

Expected: PASS (or only pre-existing skips)

- [ ] **Step 2: Ruff**

Run: `.venv/bin/ruff check .` and `.venv/bin/ruff format --check core/ tests/ examples/ utils/ web/ scripts/`

Expected: clean (match CI scope; do not reformat historical markdown plans)

- [ ] **Step 3: Self-check against this plan**

Confirm each in-scope bug is closed:

1. A sparsest / C densest with product names
2. Default generate does not call `plan_page_script`
3. Skipped pages fail coverage instead of vacuous pass
4. CLI/ROADMAP do not call D2 a quality gate

Confirm out-of-scope items were **not** implemented.

---

## Self-review

1. **Spec coverage:** Review mismatches (inverted A/B/C, default PageScript spend, vacuous skip, dishonest wording) each map to Tasks 1–4. Density contract / SourceUnit explicitly out of scope.
2. **Placeholder scan:** No TBD steps; tests and code snippets are concrete.
3. **Type consistency:** Env flag name `INKSTONE_PAGE_SCRIPT` used uniformly; panel constants names unchanged; coverage return type unchanged.
