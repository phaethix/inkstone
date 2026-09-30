# Phase 0 First Half Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the two zero-quota items from §9's first batch — the chunk-sorting defect fix (phase 0e) and the `h_env` environment snapshot with a test-enforced variable registry (phase 0a) — without touching any billable code path.

**Architecture:** Both tasks are additive and confined to the current key-derivation layer. Task 1 replaces one `sorted()` call with a numeric-aware key so the anti-templating window reads the right chunks; Task 2 makes every environment variable the app reads a declared constant in `core/config.py`, then hashes the byte-affecting subset into a single `h_env` digest with a registry test that fails whenever an unregistered switch is added.

**Tech Stack:** Python >= 3.10, pytest, ruff (line-length 100), pydantic v2, Pillow.

## Global Constraints

These apply to every task; each task's requirements implicitly include them.

- Python floor is `>= 3.10` (from `pyproject.toml` `requires-python`). No `match`, no `X | Y` in `isinstance`, no 3.11+ stdlib.
- Ruff: `line-length = 100`, `select = ["E", "F", "I", "UP", "B"]`. Import order is enforced (`I`); first-party is `["core", "utils"]`.
- No new dependencies. The runtime set is exactly `requests`, `tenacity`, `Pillow`, `numpy`, `pydantic`, `json-repair`, `tqdm`, `pypdf`.
- Run tests with the repo venv: `.venv/bin/python -m pytest <path> -v`. Pytest config lives in `pyproject.toml` (`testpaths=["tests"]`, `pythonpath=["."]`, `addopts="-q"`), so bare `tests/...` paths work.
- Lint gate before every commit: `.venv/bin/python -m ruff check core tests` and `.venv/bin/python -m ruff format --check core tests`.
- Identity follows the **resolved** value, never the config string (§8 invariant 6b). A font path can resolve to different bytes on different machines; a size can silently fall back to `1024x1024`.
- Runtime-only knobs (concurrency, rate limits, retries, deadlines) must **not** enter any key — they cannot change bytes, and including them causes over-invalidation, which is itself a defect under constraint 2 (§5).
- Credentials must never influence a key or appear in a log line (§5).
- Commit one task at a time. Only `git add` that task's files: the working tree already has an unrelated uncommitted `docs/ROADMAP.md` change and an untracked `codex-novel-to-comic-studio/` directory, which must stay out of every commit.

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `core/pipelines/creative_comic.py` | Pipeline orchestration; owns `_recent_layout_intents` (line 171) | Modify: numeric-aware chunk ordering |
| `core/config.py` | Single source of truth for every `os.environ` read | Modify: promote all env-var literals to `ENV_*` constants; add auto-collected `ALL_ENV_VARS` |
| `core/comic/fonts.py` | CJK font resolution chain | Modify: add `resolved_font_digest()` |
| `core/comic/env_snapshot.py` | The `h_env` digest and its registry | Create |
| `tests/test_recent_layout_intents.py` | Guards numeric chunk ordering | Create |
| `tests/test_config.py` | Config regression tests | Modify: add declaration-integrity tests |
| `tests/test_env_snapshot.py` | Guards the `h_env` registry and byte-affecting scope | Create |

`env_snapshot.py` is a new module rather than an addition to `creative_comic.py` because `creative_comic.py` is already 1706 lines and the snapshot has no dependency on the pipeline. `config.py` keeps the variable names because it is the module the registry test scans.

---

### Task 1: Order recent layout intents by numeric chunk index (§9 phase 0e)

`state.page_cache` is keyed by `str(chunk_index)`. Plain `sorted()` is therefore lexicographic, ranking `"10"` before `"2"`. Past ten chunks the "last 8 layout intents" window reads the **wrong chunks**, silently widening the anti-templating window to the whole book. This is also the ordering fix §5 requires in the same change that bounds the historical dependency.

**Files:**
- Modify: `core/pipelines/creative_comic.py:171-182`
- Test: `tests/test_recent_layout_intents.py` (create)

**Interfaces:**
- Consumes: `ProjectState.page_cache: dict[str, ComicPagePlanSet]` (keys are `str(chunk_index)`); `ComicPagePlanSet.pages[*].layout_intent: str`
- Produces: `_chunk_sort_key(cache_key: str) -> tuple[int, int, str]`; `_recent_layout_intents(state: ProjectState, *, limit: int = 8) -> list[str]` (signature unchanged)

- [ ] **Step 1: Write the failing test**

Create `tests/test_recent_layout_intents.py`:

```python
"""Regression: the recent-layout window must order chunks numerically.

``state.page_cache`` is keyed by ``str(chunk_index)``, so plain ``sorted()`` is
lexicographic and ranks ``"10"`` before ``"2"``. Past ten chunks the "last 8
layout intents" window therefore reads the wrong chunks, which silently widens
the anti-templating window to the whole book (§9 phase 0e).
"""

from core.pipelines.creative_comic import _recent_layout_intents
from core.schemas import ComicPagePlan, ComicPagePlanSet, ProjectState


def _state_with_intents(chunk_indexes):
    """Build a ProjectState whose page_cache holds one intent per chunk."""
    state = ProjectState(project_id="p")
    for ci in chunk_indexes:
        state.page_cache[str(ci)] = ComicPagePlanSet(
            unit_id=f"u{ci}",
            pages=[ComicPagePlan(page_id=f"p{ci}", layout_intent=f"intent-{ci}")],
        )
    return state


def test_recent_layout_intents_orders_chunks_numerically():
    state = _state_with_intents([0, 1, 2, 9, 10, 11])

    assert _recent_layout_intents(state, limit=3) == [
        "intent-9",
        "intent-10",
        "intent-11",
    ]


def test_recent_layout_intents_keeps_non_numeric_keys_last():
    state = _state_with_intents([0, 1, 2, 10])
    state.page_cache["not-a-chunk"] = ComicPagePlanSet(
        unit_id="ux",
        pages=[ComicPagePlan(page_id="px", layout_intent="intent-weird")],
    )

    assert _recent_layout_intents(state, limit=2) == ["intent-10", "intent-weird"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_recent_layout_intents.py -v`

Expected: both tests FAIL. The first returns `["intent-11", "intent-2", "intent-9"]`; the second returns `["intent-2", "intent-weird"]`. Lexicographic ordering puts `"10"` and `"11"` before `"2"` and `"9"`.

- [ ] **Step 3: Write the minimal implementation**

In `core/pipelines/creative_comic.py`, insert `_chunk_sort_key` immediately before `_recent_layout_intents` and change the `sorted()` call. The current body is:

```python
def _recent_layout_intents(state: ProjectState, *, limit: int = 8) -> list[str]:
    """Collect recent page layout_intent strings from cached page plans."""
    intents: list[str] = []
    for cache_key in sorted(state.page_cache.keys()):
        pageset = state.page_cache.get(cache_key)
        if pageset is None:
            continue
        for plan in pageset.pages:
            intent = (plan.layout_intent or "").strip()
            if intent:
                intents.append(intent)
    return intents[-limit:]
```

Replace it with:

```python
def _chunk_sort_key(cache_key: str) -> tuple[int, int, str]:
    """Order ``page_cache`` keys by resolved chunk index, then lexicographically.

    Keys are ``str(chunk_index)``, so plain ``sorted()`` is lexicographic and
    ranks ``"10"`` before ``"2"``. Past ten chunks the "last 8 layout intents"
    window would read the wrong chunks, which is a §9 phase 0e defect: the
    anti-templating window silently becomes the whole book. Non-numeric keys
    (only reachable if a caller writes one) sort last instead of crashing.
    """
    try:
        return (0, int(cache_key), "")
    except ValueError:
        return (1, 0, cache_key)


def _recent_layout_intents(state: ProjectState, *, limit: int = 8) -> list[str]:
    """Collect recent page layout_intent strings from cached page plans."""
    intents: list[str] = []
    for cache_key in sorted(state.page_cache.keys(), key=_chunk_sort_key):
        pageset = state.page_cache.get(cache_key)
        if pageset is None:
            continue
        for plan in pageset.pages:
            intent = (plan.layout_intent or "").strip()
            if intent:
                intents.append(intent)
    return intents[-limit:]
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_recent_layout_intents.py -v`

Expected: 2 passed.

- [ ] **Step 5: Run the neighbouring suites to confirm no regression**

Run: `.venv/bin/python -m pytest tests/test_layout_diversity.py tests/test_creative_comic.py tests/test_visual_bible.py -q`

Expected: all pass. `_recent_layout_intents` feeds `recent_layouts` in two call sites (lines 1201, 1216); ordering changes are observable only past ten chunks, so short-book fixtures are unaffected.

- [ ] **Step 6: Lint and commit**

```bash
.venv/bin/python -m ruff check core tests
.venv/bin/python -m ruff format --check core tests
git add core/pipelines/creative_comic.py tests/test_recent_layout_intents.py
git commit -m "fix: order recent layout intents by numeric chunk index"
```

---

### Task 2: Declare every environment variable centrally (§9 phase 0a, prerequisite)

`core/config.py` already declares eleven `ENV_*` constants, but `ImageConfig` and `ChatConfig` still read eighteen variables as bare string literals. A test cannot enumerate variables that exist only as literals, so the registry that Task 3 needs (`ALL_ENV_VARS`) is unbuildable until they are promoted. This task makes "add an output-affecting switch" a lint-visible act instead of a silent omission.

**Files:**
- Modify: `core/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `core.config.ALL_ENV_VARS: frozenset[str]` — every environment variable name `core/config.py` reads, collected from the `ENV_*` constants; plus the new constants `ENV_PROVIDER`, `ENV_AGNES_API_KEY`, `ENV_AGNES_IMAGE_I2I_MODEL`, `ENV_AGNES_CHAT_MODEL`, `ENV_OPENAI_COMPAT_BASE_URL`, `ENV_OPENAI_COMPAT_API_KEY`, `ENV_OPENAI_COMPAT_MODEL_T2I`, `ENV_OPENAI_COMPAT_MODEL_I2I`, `ENV_OPENAI_COMPAT_CHAT_BASE_URL`, `ENV_OPENAI_COMPAT_CHAT_API_KEY`, `ENV_OPENAI_COMPAT_CHAT_MODEL`, `ENV_AGNES_IMAGE_MAX_RETRIES`, `ENV_AGNES_IMAGE_RETRY_BASE_DELAY`, `ENV_INKSTONE_IMAGE_CONCURRENCY`, `ENV_INKSTONE_PANEL_CONTINUITY`, `ENV_AGNES_RATE_LIMIT`, `ENV_AGNES_IMAGE_2K_RPM`, `ENV_AGNES_IMAGE_3K_RPM`
- Consumes: nothing from Task 1

- [ ] **Step 1: Write the failing test**

Append to `tests/test_config.py`:

```python
import re
from pathlib import Path

import core.config

# Every shape that reads an environment variable inside core/config.py.
_ENV_READ_PATTERNS = (
    re.compile(r'_get\(\s*"([A-Za-z0-9_]+)"'),
    re.compile(r'env_int\(\s*"([A-Za-z0-9_]+)"'),
    re.compile(r'env_float\(\s*"([A-Za-z0-9_]+)"'),
    re.compile(r'env_bool\(\s*"([A-Za-z0-9_]+)"'),
    re.compile(r"os\.environ\.get\(\s*\"([A-Za-z0-9_]+)\""),
)


def _env_names_read_as_string_literals() -> set[str]:
    """Env var names reached through a bare string literal, if any remain."""
    source = Path(core.config.__file__).read_text(encoding="utf-8")
    found: set[str] = set()
    for pattern in _ENV_READ_PATTERNS:
        found.update(pattern.findall(source))
    return found


def test_config_reads_env_names_through_constants_only():
    """A bare literal is invisible to the registry, so it must not exist."""
    literals = _env_names_read_as_string_literals()
    assert literals == set(), (
        "core/config.py reads env vars by string literal; declare an ENV_* "
        f"constant and reference it instead: {sorted(literals)}"
    )


def test_all_env_vars_covers_every_declared_constant():
    declared = {
        value
        for name, value in vars(core.config).items()
        if name.startswith("ENV_") and isinstance(value, str)
    }
    assert declared, "no ENV_* constants found; the registry test would pass vacuously"
    assert set(core.config.ALL_ENV_VARS) == declared
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_config.py -v`

Expected: `test_config_reads_env_names_through_constants_only` FAILS listing the eighteen literals. `test_all_env_vars_covers_every_declared_constant` FAILS with `AttributeError: module 'core.config' has no attribute 'ALL_ENV_VARS'`.

- [ ] **Step 3: Promote the literals to constants**

In `core/config.py`, extend the existing constants block. Keep the current eleven lines untouched and append:

```python
ENV_PROVIDER = "PROVIDER"
ENV_AGNES_API_KEY = "AGNES_API_KEY"
ENV_AGNES_IMAGE_I2I_MODEL = "AGNES_IMAGE_I2I_MODEL"
ENV_AGNES_CHAT_MODEL = "AGNES_CHAT_MODEL"
ENV_AGNES_IMAGE_MAX_RETRIES = "AGNES_IMAGE_MAX_RETRIES"
ENV_AGNES_IMAGE_RETRY_BASE_DELAY = "AGNES_IMAGE_RETRY_BASE_DELAY"
ENV_AGNES_RATE_LIMIT = "AGNES_RATE_LIMIT"
ENV_AGNES_IMAGE_2K_RPM = "AGNES_IMAGE_2K_RPM"
ENV_AGNES_IMAGE_3K_RPM = "AGNES_IMAGE_3K_RPM"
ENV_INKSTONE_IMAGE_CONCURRENCY = "INKSTONE_IMAGE_CONCURRENCY"
ENV_INKSTONE_PANEL_CONTINUITY = "INKSTONE_PANEL_CONTINUITY"
ENV_OPENAI_COMPAT_BASE_URL = "OPENAI_COMPAT_BASE_URL"
ENV_OPENAI_COMPAT_API_KEY = "OPENAI_COMPAT_API_KEY"
ENV_OPENAI_COMPAT_MODEL_T2I = "OPENAI_COMPAT_MODEL_T2I"
ENV_OPENAI_COMPAT_MODEL_I2I = "OPENAI_COMPAT_MODEL_I2I"
ENV_OPENAI_COMPAT_CHAT_BASE_URL = "OPENAI_COMPAT_CHAT_BASE_URL"
ENV_OPENAI_COMPAT_CHAT_API_KEY = "OPENAI_COMPAT_CHAT_API_KEY"
ENV_OPENAI_COMPAT_CHAT_MODEL = "OPENAI_COMPAT_CHAT_MODEL"
```

Then replace each literal at its read site with the constant. The four rate-limit helpers and both config classes change; for example `agnes_rate_limit_rpm` becomes:

```python
def agnes_rate_limit_rpm() -> int:
    return env_int(ENV_AGNES_RATE_LIMIT, 20)
```

and `agnes_image_2k_rpm` / `agnes_image_3k_rpm` follow the same shape. `ImageConfig.__init__` becomes:

```python
def __init__(self) -> None:
    self.provider = _get(ENV_PROVIDER, "agnes").lower()
    self.agnes_api_key = _get(ENV_AGNES_API_KEY)
    self.agnes_i2i_model = _get(ENV_AGNES_IMAGE_I2I_MODEL)
    self.openai_compat_base_url = _get(ENV_OPENAI_COMPAT_BASE_URL)
    self.openai_compat_api_key = _get(ENV_OPENAI_COMPAT_API_KEY)
    self.openai_compat_model_t2i = _get(
        ENV_OPENAI_COMPAT_MODEL_T2I,
        "gemini-2.0-flash-exp-image-generation",
    )
    self.openai_compat_model_i2i = _get(ENV_OPENAI_COMPAT_MODEL_I2I)
    self.image_max_retries = env_int(ENV_AGNES_IMAGE_MAX_RETRIES, 5)
    self.image_retry_base_delay = env_float(ENV_AGNES_IMAGE_RETRY_BASE_DELAY, 5.0)
    self.image_concurrency = max(1, env_int(ENV_INKSTONE_IMAGE_CONCURRENCY, 3, minimum=1))
    self.panel_continuity = _get(ENV_INKSTONE_PANEL_CONTINUITY, "1").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
```

and `ChatConfig.__init__` becomes:

```python
    def __init__(self) -> None:
        self.provider = _get(ENV_PROVIDER, "agnes").lower()
        self.agnes_api_key = _get(ENV_AGNES_API_KEY)
        self.agnes_chat_model = _get(ENV_AGNES_CHAT_MODEL, "agnes-3.0-flash")
        self.openai_compat_chat_base_url = _get(ENV_OPENAI_COMPAT_CHAT_BASE_URL)
        self.openai_compat_chat_api_key = _get(ENV_OPENAI_COMPAT_CHAT_API_KEY)
        self.openai_compat_chat_model = _get(ENV_OPENAI_COMPAT_CHAT_MODEL)
```

Finally, append the auto-collected registry at the end of the module:

```python
# Every environment variable this module reads, collected from the ENV_*
# constants above. Collected by name prefix rather than listed by hand so a new
# constant cannot be forgotten; tests/test_env_snapshot.py asserts that the
# h_env registry partitions this set exactly.
ALL_ENV_VARS: frozenset[str] = frozenset(
    value
    for name, value in list(globals().items())
    if name.startswith("ENV_") and isinstance(value, str)
)
```

The `list(...)` wrapper matters: mutating `globals()` during iteration is not allowed, and taking a snapshot keeps the comprehension safe across CPython versions.

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_config.py -v`

Expected: 5 passed (3 pre-existing plus the 2 new).

- [ ] **Step 5: Run the config and provider suites to confirm no behaviour change**

Run: `.venv/bin/python -m pytest tests/test_config.py tests/test_providers.py tests/test_chat_provider.py -q`

Expected: all pass. These constants are pure renames; `monkeypatch.setenv("AGNES_RATE_LIMIT", ...)` still works because the *value* is unchanged.

- [ ] **Step 6: Lint and commit**

```bash
.venv/bin/python -m ruff check core tests
.venv/bin/python -m ruff format --check core tests
git add core/config.py tests/test_config.py
git commit -m "refactor: declare every environment variable as a named constant"
```

---

### Task 3: Hash the byte-affecting environment into `h_env` (§9 phase 0a)

`h_env` covers what changes generated bytes but lives outside the Python source: the resolved font's **bytes**, the resolved output size, provider identity, and the registered output-affecting switches. Today **no key anywhere contains font content** — a machine whose `INKSTONE_FONT_PATH` points at a different file produces different lettering under the same fingerprint. This task delivers the digest and the registry, not the wiring: attaching it to `letter` / `export` keys belongs to phase 2, where those keys gain manifests.

**Files:**
- Create: `core/comic/env_snapshot.py`
- Modify: `core/comic/fonts.py`
- Test: `tests/test_env_snapshot.py` (create)

**Interfaces:**
- Consumes: `core.config.ALL_ENV_VARS` (Task 2); `core.config.ImageConfig`, `ChatConfig`, `finished_page_size()`, `l3_enabled()`, `page_script_enabled()`, `render_mode()`; `core.comic.fonts._find_cjk_font`, `text_requires_cjk`, `DEFAULT_CJK_FONT_SIZE`
- Produces: `core.comic.env_snapshot.h_env(*, text: str, resolved_size: str | None = None, font_path: str | None = None) -> str`; `H_ENV_REGISTERED: frozenset[str]`; `H_ENV_EXCLUDED: frozenset[str]`; `core.comic.fonts.resolved_font_digest(text: str, *, size: int = DEFAULT_CJK_FONT_SIZE, font_path: str | None = None) -> str`

**Known boundary, deliberately out of scope:** `core/comic/export.py` reads `INKSTONE_PDF_BATCH` directly. It is not in `core/config.py`, so the registry cannot see it. The knob only batches PDF assembly in memory and cannot change the PDF's pages, so it belongs in the excluded category — but it must be *moved* into `core/config.py` before its exclusion can be asserted. That move is scheduled with the `export` key in phase 2, and this plan records the gap rather than silently leaving it.

- [ ] **Step 1: Write the failing test**

Create `tests/test_env_snapshot.py`:

```python
"""Guards for the h_env digest and its registry (§5, §9 phase 0a).

Two properties are load-bearing. First, the registry must partition every
environment variable the app reads, so adding an output-affecting switch
without registering it fails a test. Second, the digest must follow *resolved*
values rather than config strings, and must ignore both runtime-only knobs and
credentials.
"""

import core.comic.fonts as fonts
from core.comic.env_snapshot import H_ENV_EXCLUDED, H_ENV_REGISTERED, h_env
from core.comic.fonts import resolved_font_digest
from core.config import ALL_ENV_VARS


def test_registry_partitions_every_declared_env_var():
    assert H_ENV_REGISTERED | H_ENV_EXCLUDED == set(ALL_ENV_VARS)
    assert H_ENV_REGISTERED & H_ENV_EXCLUDED == set()


def test_every_registered_var_is_read_by_config():
    """A registered name that nothing reads is a typo, not a registration."""
    assert H_ENV_REGISTERED <= set(ALL_ENV_VARS)


def test_h_env_follows_resolved_size_not_config_string(monkeypatch):
    """§8 invariant 6b: a size fallback must change the key."""
    monkeypatch.setenv("INKSTONE_PAGE_SIZE", "1024x1536")

    declared = h_env(text="plain")
    fallen_back = h_env(text="plain", resolved_size="1024x1024")

    assert declared != fallen_back


def test_h_env_ignores_runtime_only_switches(monkeypatch):
    """Concurrency, throttling and retries cannot change bytes."""
    baseline = h_env(text="plain")
    monkeypatch.setenv("INKSTONE_IMAGE_CONCURRENCY", "7")
    monkeypatch.setenv("AGNES_IMAGE_3K_RPM", "3")
    monkeypatch.setenv("AGNES_IMAGE_MAX_RETRIES", "1")
    monkeypatch.setenv("AGNES_IMAGE_RETRY_BASE_DELAY", "0.1")
    monkeypatch.setenv("INKSTONE_RUN_DEADLINE_HOURS", "2")
    monkeypatch.setenv("INKSTONE_WEBTOON_WARN_MB", "1")

    assert h_env(text="plain") == baseline


def test_h_env_changes_when_a_registered_switch_changes(monkeypatch):
    baseline = h_env(text="plain")
    monkeypatch.setenv("INKSTONE_PANEL_CONTINUITY", "0")

    assert h_env(text="plain") != baseline


def test_h_env_never_contains_credentials(monkeypatch):
    """Secrets must not influence the key, so rotating one cannot invalidate work."""
    monkeypatch.setenv("AGNES_API_KEY", "secret-a")
    first = h_env(text="plain")
    monkeypatch.setenv("AGNES_API_KEY", "secret-b")

    assert h_env(text="plain") == first


def test_font_path_string_does_not_enter_h_env(monkeypatch):
    """The path string is not identity; the resolved bytes are."""
    monkeypatch.setenv("INKSTONE_FONT_PATH", "/nonexistent/a.ttf")
    first = h_env(text="汉字")

    monkeypatch.setenv("INKSTONE_FONT_PATH", "/nonexistent/b.ttf")

    assert h_env(text="汉字") == first


def test_resolved_font_digest_hashes_bytes_not_path(tmp_path, monkeypatch):
    """Same call shape, different bytes behind it => different digest."""
    first_path = tmp_path / "first.ttf"
    first_path.write_bytes(b"synthetic-font-bytes-a")
    second_path = tmp_path / "second.ttf"
    second_path.write_bytes(b"synthetic-font-bytes-b")

    class _FakeFont:
        """Stand-in for a resolved FreeType font; the digest only reads the file."""

    monkeypatch.setattr(fonts, "_find_cjk_font", lambda size, path: (_FakeFont(), str(path)))

    first = resolved_font_digest("汉字", font_path=str(first_path))
    second = resolved_font_digest("汉字", font_path=str(second_path))

    assert first != second


def test_resolved_font_digest_is_stable_for_builtin(monkeypatch):
    """Non-CJK text uses Pillow's builtin font and has no file to hash."""
    monkeypatch.setattr(fonts, "_find_cjk_font", lambda size, path: None)

    assert resolved_font_digest("plain ascii") == resolved_font_digest("other ascii")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_env_snapshot.py -v`

Expected: collection error — `ModuleNotFoundError: No module named 'core.comic.env_snapshot'`.

- [ ] **Step 3: Add the font byte digest**

At the end of `core/comic/fonts.py`, after `resolve_font` and before `reset_caches`, add:

```python
def resolved_font_digest(
    text: str,
    *,
    size: int = DEFAULT_CJK_FONT_SIZE,
    font_path: str | None = None,
) -> str:
    """SHA-256 of the bytes of the font that will actually draw ``text``.

    Identity must be the file *content*, not the path: the same path resolves to
    different bytes on different machines, so a path string cannot distinguish
    two machines whose lettering differs (§5). The sentinels ``"builtin"`` and
    ``"builtin-no-cjk"`` stand in for Pillow's bitmap font, which has no file.
    """
    if not text_requires_cjk(text):
        return "builtin"
    found = _find_cjk_font(size, font_path)
    if found is None:
        return "builtin-no-cjk"
    _font, source = found
    for prefix in ("font_path:", "INKSTONE_FONT_PATH:"):
        if source.startswith(prefix):
            source = source[len(prefix) :]
            break
    try:
        with open(source, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()
    except OSError:
        return "unreadable"
```

Add `import hashlib` to the import block at the top of `core/comic/fonts.py`, keeping stdlib imports grouped and alphabetised (`hashlib` before `logging`).

The source-string unwrapping is needed because `_find_cjk_font` tags the explicit and environment-variable candidates (`font_path:<p>`, `INKSTONE_FONT_PATH:<p>`) and returns bare paths for the platform candidates.

- [ ] **Step 4: Create the `h_env` module**

Create `core/comic/env_snapshot.py`:

```python
"""core.comic.env_snapshot — the ``h_env`` digest for content-addressed keys.

``h_env`` covers everything that changes generated bytes while living outside
the Python source: the resolved font's *bytes*, the resolved output size, the
provider identity, and the registered output-affecting switches. Python source
identity is a separate component (``h_stage``), so it is not repeated here.

Three rules from §5 shape this module:

1. Values are the **resolved** ones, never config strings. A font path can
   resolve to different bytes on different machines; a requested size can
   silently fall back to a smaller one (§8 invariant 6b).
2. Runtime-only knobs (concurrency, throttling, retries, deadlines, log paths)
   never enter. They cannot change bytes, and folding them in causes
   over-invalidation, which is itself a defect.
3. Credentials never enter. Rotating a key must not invalidate cached work, and
   a key must never be reconstructible from a digest's inputs.

The registry below is asserted to partition ``core.config.ALL_ENV_VARS``
exactly, so adding an output-affecting switch without registering it fails a
test rather than silently missing every key.
"""

import hashlib
import json

from core.comic.fonts import resolved_font_digest
from core.config import (
    ENV_AGNES_API_KEY,
    ENV_AGNES_CHAT_MODEL,
    ENV_AGNES_IMAGE_2K_RPM,
    ENV_AGNES_IMAGE_3K_RPM,
    ENV_AGNES_IMAGE_I2I_MODEL,
    ENV_AGNES_IMAGE_MAX_RETRIES,
    ENV_AGNES_IMAGE_RETRY_BASE_DELAY,
    ENV_AGNES_RATE_LIMIT,
    ENV_ERROR_LOG,
    ENV_FONT_PATH,
    ENV_INKSTONE_IMAGE_CONCURRENCY,
    ENV_L3,
    ENV_OPENAI_COMPAT_API_KEY,
    ENV_OPENAI_COMPAT_BASE_URL,
    ENV_OPENAI_COMPAT_CHAT_API_KEY,
    ENV_OPENAI_COMPAT_CHAT_BASE_URL,
    ENV_OPENAI_COMPAT_CHAT_MODEL,
    ENV_OPENAI_COMPAT_MODEL_I2I,
    ENV_OPENAI_COMPAT_MODEL_T2I,
    ENV_PAGE_SCRIPT,
    ENV_PAGE_SIZE,
    ENV_PROVIDER,
    ENV_RENDER_MODE,
    ENV_RUN_DEADLINE_HOURS,
    ENV_SUPERVISOR_BACKOFF_BASE,
    ENV_SUPERVISOR_BACKOFF_CAP,
    ENV_WEBTOON_MAX_PIXELS,
    ENV_WEBTOON_WARN_MB,
    ChatConfig,
    ImageConfig,
    finished_page_size,
    l3_enabled,
    page_script_enabled,
    render_mode,
)

# Switches whose value can change generated bytes, so they belong in the digest.
# INKSTONE_FONT_PATH is deliberately absent: its *path string* is not identity,
# while the bytes it resolves to enter through resolved_font_digest().
H_ENV_REGISTERED: frozenset[str] = frozenset(
    {
        ENV_L3,
        ENV_PAGE_SIZE,
        ENV_PAGE_SCRIPT,
        ENV_RENDER_MODE,
        ENV_PROVIDER,
        ENV_AGNES_IMAGE_I2I_MODEL,
        ENV_AGNES_CHAT_MODEL,
        ENV_OPENAI_COMPAT_BASE_URL,
        ENV_OPENAI_COMPAT_MODEL_T2I,
        ENV_OPENAI_COMPAT_MODEL_I2I,
        ENV_OPENAI_COMPAT_CHAT_BASE_URL,
        ENV_OPENAI_COMPAT_CHAT_MODEL,
    }
)

# Switches that provably cannot change bytes: throttling, concurrency, retry
# policy, deadlines, log destinations, capacity warnings, credentials. Retry
# exhaustion is recorded as an ``outcome`` (§6), never as part of a key.
H_ENV_EXCLUDED: frozenset[str] = frozenset(
    {
        ENV_FONT_PATH,
        ENV_INKSTONE_IMAGE_CONCURRENCY,
        ENV_AGNES_RATE_LIMIT,
        ENV_AGNES_IMAGE_2K_RPM,
        ENV_AGNES_IMAGE_3K_RPM,
        ENV_AGNES_IMAGE_MAX_RETRIES,
        ENV_AGNES_IMAGE_RETRY_BASE_DELAY,
        ENV_RUN_DEADLINE_HOURS,
        ENV_SUPERVISOR_BACKOFF_BASE,
        ENV_SUPERVISOR_BACKOFF_CAP,
        ENV_WEBTOON_WARN_MB,
        ENV_WEBTOON_MAX_PIXELS,
        ENV_ERROR_LOG,
        ENV_AGNES_API_KEY,
        ENV_OPENAI_COMPAT_API_KEY,
        ENV_OPENAI_COMPAT_CHAT_API_KEY,
    }
)


def h_env(
    *,
    text: str,
    resolved_size: str | None = None,
    font_path: str | None = None,
) -> str:
    """Return the SHA-256 of every byte-affecting value outside the source.

    Args:
        text: the text whose font resolution determines the font digest. Callers
            pass the text that will actually be drawn on the artifact under key.
        resolved_size: the size the call will really use. Pass the fallback
            (``1024x1024``) when a size error triggered one, so the key reflects
            the bytes rather than the request (§8 invariant 6b). ``None`` means
            no fallback occurred and the configured size is authoritative.
        font_path: an explicit per-engine font override, mirroring
            ``fonts.resolve_font``.

    Returns:
        A 64-character lowercase hex digest. Two calls with equal bytes-affecting
        inputs return the same digest; any difference in the registered set, the
        resolved font bytes, or the resolved size changes it.
    """
    image = ImageConfig()
    chat = ChatConfig()
    payload = {
        "font_digest": resolved_font_digest(text, font_path=font_path),
        "resolved_size": resolved_size or finished_page_size(),
        "render_mode": render_mode(),
        "provider": image.provider,
        "image_i2i_model": image.agnes_i2i_model,
        "chat_model": chat.agnes_chat_model,
        "openai_base_url": image.openai_compat_base_url,
        "openai_t2i_model": image.openai_compat_model_t2i,
        "openai_i2i_model": image.openai_compat_model_i2i,
        "openai_chat_base_url": chat.openai_compat_chat_base_url,
        "openai_chat_model": chat.openai_compat_chat_model,
        "l3_enabled": l3_enabled(),
        "panel_continuity": image.panel_continuity,
        "page_script_enabled": page_script_enabled(),
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
```

The `sort_keys=True` plus explicit-separator `json.dumps` mirrors `_render_fingerprint`'s canonicalisation so the two digests are constructed the same way. `ensure_ascii=False` keeps CJK font paths readable in a debugger.

- [ ] **Step 5: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_env_snapshot.py -v`

Expected: 9 passed.

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/python -m pytest -q`

Expected: all pass. The only pre-existing behaviour touched is three new `ENV_*` names in `core/config.py`'s namespace, which nothing reads by iteration.

- [ ] **Step 7: Lint and commit**

```bash
.venv/bin/python -m ruff check core tests
.venv/bin/python -m ruff format --check core tests
git add core/comic/env_snapshot.py core/comic/fonts.py tests/test_env_snapshot.py
git commit -m "feat: hash byte-affecting environment into an h_env digest"
```

---

## Self-Review

**Spec coverage.** §9 phase 0e is Task 1. §9 phase 0a's two halves are Task 2 (the registry `core/config.py` must expose, without which "unregistered switch fails a test" cannot be written) and Task 3 (the digest, the font-byte rule, and the resolved-size rule). §8 invariant 6b is asserted by two tests, one per resolved value (size in `test_h_env_follows_resolved_size_not_config_string`, font in `test_font_path_string_does_not_enter_h_env` and `test_resolved_font_digest_hashes_bytes_not_path`). §13 resolved item 16 ("`h_env`'s list must cover every env var in `core/config.py` and be written as a test") is satisfied by `test_registry_partitions_every_declared_env_var`.

**Deliberately not covered here.** Wiring `h_env` into `letter` / `export` keys belongs to phase 2, where those stages gain manifests; attaching a digest to a stage that has no key yet would produce a value nothing consumes. §9 phase 0a's "write size fallback into `state.json` for audit" is likewise a phase 2 concern, because the fallback it audits is recorded by the manifest's `outputs` — there is no manifest to attach it to today. `INKSTONE_PDF_BATCH` is recorded above as a known registry gap with a phase 2 owner.

**Placeholder scan.** No `TBD`, no "add appropriate error handling", no "similar to Task N". Every code step carries runnable code; every test step carries the literal assertion. The one intentional deferral (`INKSTONE_PDF_BATCH`) states its owner and the reason it cannot be asserted yet, rather than being written as a placeholder.

**Type consistency.** `_chunk_sort_key` is defined in Task 1 and consumed only by `_recent_layout_intents` in the same task. `ALL_ENV_VARS` is produced by Task 2 as `frozenset[str]` and consumed by Task 3's test as `set(ALL_ENV_VARS)`; `H_ENV_REGISTERED` / `H_ENV_EXCLUDED` are produced as `frozenset[str]` and compared against it set-wise. `resolved_font_digest` is produced by Task 3 Step 3 with signature `(text, *, size=DEFAULT_CJK_FONT_SIZE, font_path=None) -> str` and consumed by `h_env` in Step 4 with `text` positional and `font_path` keyword-only, matching. The test that monkeypatches `_find_cjk_font` uses the real two-argument positional shape `(size, path)`, which is what `fonts._find_cjk_font` actually takes — verified against the source at `core/comic/fonts.py:140`.