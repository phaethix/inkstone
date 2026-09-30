# Phase 0h Threshold Instruments Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the two zero-quota instruments of §9 phase 0h — a counting-fake measurement of what one typical alias merge costs under the current architecture, and a two-call idempotency probe — so the numeric threshold that gates phases 0c and 1-3 is evidence rather than belief.

**Architecture:** Both instruments live as importable logic in `core/` and are exposed by thin scripts, matching the repo's existing pattern where `core/cli.py` holds logic and `scripts/` holds entry points. `scripts/` has no test convention and no `__init__.py`, so putting the logic there would make it untestable; the logic therefore goes into `core/comic/merge_cost_probe.py` and `core/api/idempotency_probe.py`, each fully covered by fakes that never touch the network.

**Tech Stack:** Python >= 3.10, pytest, ruff (line-length 100), pydantic v2, Pillow.

## Global Constraints

Copied verbatim from `2026-09-29-phase0-defect-fixes-and-env-identity.md`; each task's requirements implicitly include them.

- Python floor is `>= 3.10` (from `pyproject.toml` `requires-python`). No `match`, no `X | Y` in `isinstance`, no 3.11+ stdlib.
- Ruff: `line-length = 100`, `select = ["E", "F", "I", "UP", "B"]`. Import order is enforced (`I`); first-party is `["core", "utils"]`.
- No new dependencies. The runtime set is exactly `requests`, `tenacity`, `Pillow`, `numpy`, `pydantic`, `json-repair`, `tqdm`, `pypdf`.
- Run tests with the repo venv: `.venv/bin/python -m pytest <path> -v`. Pytest config lives in `pyproject.toml` (`testpaths=["tests"]`, `pythonpath=["."]`, `addopts="-q"`), so bare `tests/...` paths work.
- Lint gate before every commit: `.venv/bin/python -m ruff check core tests` and `.venv/bin/python -m ruff format --check core tests`.
- Commit one task at a time. Only `git add` that task's files: the working tree already has an unrelated uncommitted `docs/ROADMAP.md` / `.gitignore` change, which must stay out of every commit.
- **Both instruments must cost zero quota by default.** The merge-cost probe injects fakes and never imports a live provider; the idempotency probe issues exactly two calls and only when a human runs the script.

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `core/comic/merge_cost_probe.py` | Counting-fake measurement of one alias merge's billable calls | Create |
| `scripts/measure_merge_cost.py` | Thin CLI that runs the probe and prints the §9 table row | Create |
| `tests/test_merge_cost_probe.py` | Guards the harness shape and the sample constants | Create |
| `core/api/idempotency_probe.py` | Byte comparison of two responses to one request | Create |
| `scripts/probe_image_idempotency.py` | Thin CLI that issues the two live calls | Create |
| `tests/test_idempotency_probe.py` | Guards the comparison and the fake-provider path | Create |
| `docs/architecture/2026-09-28-content-addressed-pipeline-design.md` | §9 threshold table | Modify: fill the measured column |
| `CHANGELOG.md` | Release notes | Modify: record both probe results |

The two probes are separate modules because they measure different things and one of them must never be reachable from the other: the merge-cost probe is deliberately network-free, while the idempotency probe is deliberately a network call. Keeping them in one module would make "does this run cost quota" an unanswerable question by inspection.

---

### Task 1: Measure one alias merge's billable calls (§9 phase 0h, resolved item 26)

The threshold gating phases 0c and 1-3 is *the total billable calls caused by one typical alias merge*, on a fixed sample of 300 pages where the alias appears on 15 pages spanning 3 chunks (§9, §13 resolved item 26). The design's "current architecture" column is an estimate ("Roughly 15 to 190" image, "Roughly 15 to 20" chat) explicitly marked as *to be filled in from measurement*. This task produces the measurement.

The probe must count only the **second** run: run once to build the sample, inject the merge, then re-run with fresh counting fakes and report what that re-run spent.

**Files:**
- Create: `core/comic/merge_cost_probe.py`
- Create: `scripts/measure_merge_cost.py`
- Test: `tests/test_merge_cost_probe.py`

**Interfaces:**
- Consumes: `core.pipelines.creative_comic.creative_comic(source_txt, *, output_dir, chat, image, ...) -> ComicProject`; `core.comic.identity.merge_character_alias(state, new_name, keep_name) -> list[str]`; `core.schemas.ProjectState.load(path)` / `.save(path)`; `core.api.ChatProvider` / `ImageProvider`
- Produces: `core.comic.merge_cost_probe.measure_merge_cost(workdir, *, chunks, pages_per_chunk, affected_chunks, affected_pages_per_chunk) -> MergeCost`; `MergeCost` fields `pages`, `affected_pages`, `chat_calls`, `image_calls`, `merged_keys`, and property `total_calls`; constants `SAMPLE_CHUNKS`, `SAMPLE_PAGES_PER_CHUNK`, `SAMPLE_AFFECTED_CHUNKS`, `SAMPLE_AFFECTED_PAGES_PER_CHUNK`

- [ ] **Step 1: Write the failing test**

Create `tests/test_merge_cost_probe.py`:

```python
"""Guards for the alias-merge cost instrument (§9 phase 0h, resolved item 26).

The instrument exists to replace an estimate with a measurement, so the tests
assert the harness is faithful — the sample shape, and that it counts the
*second* run (the merge) rather than the build — not a specific call count,
which is the value being discovered.
"""

import asyncio

from core.comic.merge_cost_probe import (
    SAMPLE_AFFECTED_CHUNKS,
    SAMPLE_AFFECTED_PAGES_PER_CHUNK,
    SAMPLE_CHUNKS,
    SAMPLE_PAGES_PER_CHUNK,
    measure_merge_cost,
)


def _small(tmp_path):
    """A six-page, one-affected-page sample: fast enough for CI, full code path."""
    return asyncio.run(
        measure_merge_cost(
            tmp_path,
            chunks=3,
            pages_per_chunk=2,
            affected_chunks=(1,),
            affected_pages_per_chunk=1,
        )
    )


def test_default_sample_matches_the_design_threshold_table():
    """§9 fixes the sample: 300 pages, the alias on 15 across 3 chunks."""
    assert SAMPLE_CHUNKS * SAMPLE_PAGES_PER_CHUNK == 300
    assert len(SAMPLE_AFFECTED_CHUNKS) * SAMPLE_AFFECTED_PAGES_PER_CHUNK == 15


def test_probe_reports_the_sample_shape(tmp_path):
    report = _small(tmp_path)

    assert report.pages == 6
    assert report.affected_pages == 1


def test_probe_records_the_keys_the_merge_marked_stale(tmp_path):
    report = _small(tmp_path)

    assert report.merged_keys, "the merge marked no page; the sample cannot measure a merge"


def test_probe_repaints_at_least_the_affected_pages(tmp_path):
    """A stale page is a miss, so the second run must re-issue at least that call."""
    report = _small(tmp_path)

    assert report.image_calls >= report.affected_pages
    assert report.total_calls == report.chat_calls + report.image_calls


def test_probe_is_network_free(tmp_path, monkeypatch):
    """The instrument must be runnable in CI: a live factory call is a defect."""
    import core.comic.merge_cost_probe as probe

    def _explode(*_args, **_kwargs):
        raise AssertionError("merge-cost probe reached a live provider factory")

    monkeypatch.setattr(probe, "get_chat_provider", _explode, raising=False)
    monkeypatch.setattr(probe, "get_image_provider", _explode, raising=False)

    assert _small(tmp_path).pages == 6
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_merge_cost_probe.py -v`

Expected: collection error — `ModuleNotFoundError: No module named 'core.comic.merge_cost_probe'`.

- [ ] **Step 3: Create the probe module**

Create `core/comic/merge_cost_probe.py`:

```python
"""core.comic.merge_cost_probe — zero-quota instrument for §9 phase 0h.

The threshold that gates phases 0c and 1-3 is "the total billable calls caused
by one typical alias merge" on a fixed sample: 300 pages, the alias on 15 of
them, spanning 3 chunks (§9, §13 resolved item 26). The design's "current
architecture" column is an estimate that must be replaced by a measurement, or
the number is a belief rather than an argument.

This module produces that measurement without spending quota: both providers
are fakes that count calls, and the pipeline is driven exactly as the CLI drives
it. The counted value is the **second** run — the run that happens after a human
merges an alias — because that is the run the threshold describes.

The harness deliberately does not import `get_chat_provider` /
`get_image_provider`: a live factory here would turn a CI test into real image
spend. `tests/test_merge_cost_probe.py` asserts that is impossible.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from core.api import ChatProvider, ImageProvider
from core.comic.identity import merge_character_alias
from core.pipelines.creative_comic import creative_comic
from core.schemas import ProjectState

# The fixed sample from the §9 threshold table: 20 chunks x 15 pages = 300
# pages, the alias on 5 pages of each of 3 chunks = 15 pages. The density is
# 5% and the affected chunks cross boundaries, which is what the table names.
SAMPLE_CHUNKS = 20
SAMPLE_PAGES_PER_CHUNK = 15
SAMPLE_AFFECTED_CHUNKS = (4, 9, 14)
SAMPLE_AFFECTED_PAGES_PER_CHUNK = 5

BASE_NAME = "方鸿渐"
ALIAS_NAME = "鸿渐"
SETTING_NAME = "甲板"


class _FakeImageOutput:
    """Stand-in for ``ImageOutput``; the probe only counts and persists."""

    def __init__(self) -> None:
        self.fmt = "b64"
        self.data = ""
        self.ext = "png"

    def save(self, path) -> None:
        Image.new("RGB", (8, 8), (100, 100, 100)).save(path)


class CountingImage(ImageProvider):
    """Counts every image call; the count is the render-side measurement."""

    def __init__(self) -> None:
        self.calls = 0

    async def generate_single_image(
        self,
        prompt,
        reference_image_paths=None,
        size=None,
        max_retries=None,
        retry_base_delay=None,
        **kwargs,
    ) -> _FakeImageOutput:
        self.calls += 1
        return _FakeImageOutput()


class SyntheticChat(ChatProvider):
    """Deterministic chat double that emits a fixed page count per chunk.

    Chunks in ``affected_chunks`` introduce ``ALIAS_NAME`` as a variant of
    ``BASE_NAME`` on their first ``affected_pages_per_chunk`` pages. That is what
    makes the merge "typical": a handful of pages per affected chunk, not the
    whole book. The chunk ordinal is tracked from the extract call, which the
    pipeline issues once per chunk, in order, on the first run.
    """

    def __init__(
        self,
        *,
        pages_per_chunk: int,
        affected_chunks: tuple[int, ...],
        affected_pages_per_chunk: int,
    ) -> None:
        self.calls = 0
        self.pages_per_chunk = pages_per_chunk
        self.affected_chunks = set(affected_chunks)
        self.affected_pages_per_chunk = affected_pages_per_chunk
        self._chunk_index = -1

    async def chat_function_call(self, messages, tools, tool_choice, **kwargs):
        self.calls += 1
        name = tool_choice["function"]["name"]
        if name == "extract_story_elements":
            self._chunk_index += 1
            characters = [
                {"name": BASE_NAME, "l1_prompt": "a young man", "portrait_prompt": "portrait"}
            ]
            if self._chunk_index in self.affected_chunks:
                characters.append(
                    {"name": ALIAS_NAME, "l1_prompt": "a young man", "portrait_prompt": "portrait"}
                )
            return {
                "characters": characters,
                "settings": [{"name": SETTING_NAME, "scene_prompt": "ship deck at dawn"}],
                "style_guide": "manhua",
            }
        if name == "reconcile_visual_bible":
            return {
                "merges": [],
                "stages": [],
                "keeps": [],
                "color_patches": [],
                "style_guide": "manhua",
                "color": {
                    "palette": [{"name": "ink", "hex": "#1A1A1A", "usage": "lines"}],
                    "lighting": "soft",
                    "forbidden": [],
                },
                "canons": [
                    {
                        "canonical_name": name_value,
                        "face_lock": "a young man",
                        "stages": [
                            {
                                "stage": "adult",
                                "outfit_lock": "white shirt",
                                "hair_lock": "short dark hair",
                                "portrait_key": name_value,
                            }
                        ],
                    }
                    for name_value in self._canon_names()
                ],
            }
        if name == "extract_key_beats":
            return {"beats": []}
        if name == "plan_comic_pages":
            return {"unit_id": str(self._chunk_index), "pages": self._pages()}
        return {}

    def _canon_names(self) -> list[str]:
        if self._chunk_index in self.affected_chunks:
            return [BASE_NAME, ALIAS_NAME]
        return [BASE_NAME]

    def _pages(self) -> list[dict]:
        affected = self._chunk_index in self.affected_chunks
        pages: list[dict] = []
        for index in range(self.pages_per_chunk):
            use_alias = affected and index < self.affected_pages_per_chunk
            who = ALIAS_NAME if use_alias else BASE_NAME
            pages.append(
                {
                    "page_id": f"u{self._chunk_index}_p{index:04d}",
                    "purpose": "beat",
                    "layout_intent": f"layout-{self._chunk_index}-{index}",
                    "panels": [
                        {
                            "panel_id": "1",
                            "role": "establishing",
                            "shape_hint": "wide",
                            "shot": "medium",
                            "action": f"{who} looks at the sea",
                            "characters": [who],
                            "setting_ref": SETTING_NAME,
                            "caption": "清晨。",
                        }
                    ],
                    "reference_characters": [who],
                    "setting_refs": [SETTING_NAME],
                }
            )
        return pages


@dataclass
class MergeCost:
    """Billable calls attributable to one alias merge, plus the sample shape."""

    pages: int
    affected_pages: int
    chat_calls: int
    image_calls: int
    merged_keys: list[str] = field(default_factory=list)

    @property
    def total_calls(self) -> int:
        return self.chat_calls + self.image_calls


def _fake_export_pdf(self, page_dir, out="comic.pdf", layout="TwoPageRight", direction="R2L"):
    """Stand-in for the manga2pdf CLI (not installed in test/CI envs)."""
    Path(out).write_bytes(b"%PDF-1.4 fake")
    return out


def _source_text(chunks: int) -> str:
    """Build ``chunks`` chapter blocks so ``segment_text`` yields one chunk each."""
    return "\n".join(
        f"第{index + 1}章\n{ALIAS_NAME if False else BASE_NAME}在甲板上。"
        for index in range(chunks)
    )


async def measure_merge_cost(
    workdir: Path,
    *,
    chunks: int = SAMPLE_CHUNKS,
    pages_per_chunk: int = SAMPLE_PAGES_PER_CHUNK,
    affected_chunks: tuple[int, ...] = SAMPLE_AFFECTED_CHUNKS,
    affected_pages_per_chunk: int = SAMPLE_AFFECTED_PAGES_PER_CHUNK,
) -> MergeCost:
    """Measure the billable calls one typical alias merge causes on re-run.

    Run once to build the sample, merge ``ALIAS_NAME`` into ``BASE_NAME`` the way
    ``apply_review`` does, then re-run with fresh counting fakes. The returned
    counts describe the **second** run only, which is the threshold's subject.

    Args:
        workdir: an empty directory the sample is built in.
        chunks / pages_per_chunk: the sample's shape (defaults give 300 pages).
        affected_chunks: chunk ordinals whose pages reference the alias.
        affected_pages_per_chunk: how many pages per affected chunk do.

    Returns:
        A :class:`MergeCost` whose ``chat_calls`` and ``image_calls`` are the
        re-run's billable calls, and whose ``merged_keys`` are the page keys the
        merge marked stale.
    """
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    source = _source_text(chunks)
    state_path = workdir / "state.json"

    with patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf):
        await creative_comic(
            source,
            output_dir=str(workdir),
            chat=SyntheticChat(
                pages_per_chunk=pages_per_chunk,
                affected_chunks=affected_chunks,
                affected_pages_per_chunk=affected_pages_per_chunk,
            ),
            image=CountingImage(),
        )

        state = ProjectState.load(state_path)
        merged_keys = merge_character_alias(state, ALIAS_NAME, BASE_NAME)
        state.save(state_path)

        chat, image = (
            SyntheticChat(
                pages_per_chunk=pages_per_chunk,
                affected_chunks=affected_chunks,
                affected_pages_per_chunk=affected_pages_per_chunk,
            ),
            CountingImage(),
        )
        await creative_comic(source, output_dir=str(workdir), chat=chat, image=image)

    return MergeCost(
        pages=chunks * pages_per_chunk,
        affected_pages=len(affected_chunks) * affected_pages_per_chunk,
        chat_calls=chat.calls,
        image_calls=image.calls,
        merged_keys=list(merged_keys),
    )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_merge_cost_probe.py -v`

Expected: 5 passed.

If `test_probe_records_the_keys_the_merge_marked_stale` fails, the alias never
reached `state.characters`: check that `extract_story_elements` is called once
per chunk (it is cached per chunk, and `_chunk_index` advances only there) and
that `detect_character_aliases` sees `ALIAS_NAME` as a substring of `BASE_NAME`.

- [ ] **Step 5: Create the CLI entry point**

Create `scripts/measure_merge_cost.py`:

```python
"""Measure what one typical alias merge costs today (design §9 phase 0h).

Zero-quota: the pipeline is driven with counting fake providers, so this script
never calls a live API. Run it from the repo root:

    .venv/bin/python scripts/measure_merge_cost.py

It prints the row that belongs in the §9 threshold table's "current
architecture" column. Commit that value to the table and to CHANGELOG.md; an
empty column is equivalent to having no threshold at all (§13 resolved item 26).
"""

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.comic.merge_cost_probe import (  # noqa: E402
    SAMPLE_AFFECTED_CHUNKS,
    SAMPLE_AFFECTED_PAGES_PER_CHUNK,
    SAMPLE_CHUNKS,
    SAMPLE_PAGES_PER_CHUNK,
    measure_merge_cost,
)


async def main() -> None:
    with tempfile.TemporaryDirectory() as workdir:
        report = await measure_merge_cost(Path(workdir))

    print("alias-merge cost under the current architecture")
    print(
        f"  sample           : {report.pages} pages, "
        f"{report.affected_pages} affected "
        f"({SAMPLE_CHUNKS} chunks x {SAMPLE_PAGES_PER_CHUNK} pages; "
        f"alias on {SAMPLE_AFFECTED_PAGES_PER_CHUNK} pages of "
        f"chunks {list(SAMPLE_AFFECTED_CHUNKS)})"
    )
    print(f"  stale page keys  : {len(report.merged_keys)}")
    print(f"  chat calls       : {report.chat_calls}")
    print(f"  image calls      : {report.image_calls}")
    print(f"  total calls      : {report.total_calls}")
    print()
    print(
        "| Item | Current architecture | Migration target |\n"
        f"| Image calls (render) | {report.image_calls} | <= 17 |\n"
        f"| Chat calls (extract / plan) | {report.chat_calls} | <= 3 |\n"
        "| Growth with book length | record from a 600-page run | O(affected pages) |"
    )


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 6: Run the script and record the measured value**

Run: `.venv/bin/python scripts/measure_merge_cost.py`

Expected: a table with the measured `chat calls` and `image calls`. Then edit the
§9 threshold table in
`docs/architecture/2026-09-28-content-addressed-pipeline-design.md` (the row
beginning `**Before committing to Phase 0c and Phases 1-3, a numeric threshold`)
to replace `fill in from measurement` with the measured numbers, and append both
numbers to `CHANGELOG.md` under `## [Unreleased]` as a `### Changed` bullet.

- [ ] **Step 7: Lint and commit**

```bash
.venv/bin/python -m ruff check core tests
.venv/bin/python -m ruff format --check core tests
git add core/comic/merge_cost_probe.py scripts/measure_merge_cost.py tests/test_merge_cost_probe.py CHANGELOG.md docs/architecture/2026-09-28-content-addressed-pipeline-design.md
git commit -m "feat: measure the billable cost of one alias merge at zero quota"
```

---

### Task 2: Probe provider idempotency with two calls (§9 phase 0a, resolved item 27)

§8 accepts a duplicate-charge window on the premise that no provider honours an
idempotency key. That premise was inferred backwards from one incident and has
never been verified; §13 resolved item 27 names closing it as "the only place in
the document where an acknowledged defect can be narrowed at zero design cost".
The measurement is: issue the same request twice and compare the bytes. This
task ships the comparison (testable, network-free) and the script that issues the
two calls (run by a human, two calls).

**Files:**
- Create: `core/api/idempotency_probe.py`
- Create: `scripts/probe_image_idempotency.py`
- Test: `tests/test_idempotency_probe.py`

**Interfaces:**
- Consumes: `core.api.image_provider.ImageOutput` (`.fmt`, `.data`, `.save(path)`); `core.api.image_provider.ImageProvider.generate_single_image(prompt, reference_image_paths=None, size=None, ...)`; `core.api.get_image_provider()`
- Produces: `core.api.idempotency_probe.IdempotencyVerdict` (`identical`, `digest_a`, `digest_b`, `size_a`, `size_b`, `.summary() -> str`); `compare_outputs(first: bytes, second: bytes) -> IdempotencyVerdict`; `probe_image_idempotency(provider, *, prompt, size, workdir) -> IdempotencyVerdict`

- [ ] **Step 1: Write the failing test**

Create `tests/test_idempotency_probe.py`:

```python
"""Guards for the idempotency probe (§13 resolved item 27).

The probe's network half cannot be tested without spending quota, so the tests
cover the half that decides the answer: the byte comparison, and the fact that
the harness compares persisted bytes rather than response URLs (a URL comparison
would report "different" for two byte-identical images served from two URLs).
"""

import asyncio
import hashlib

from core.api import ImageProvider
from core.api.idempotency_probe import compare_outputs, probe_image_idempotency


class _DeterministicImage(ImageProvider):
    """Writes the same bytes every call, like a provider with stable semantics."""

    def __init__(self) -> None:
        self.calls = 0

    async def generate_single_image(self, prompt, reference_image_paths=None, size=None, **kwargs):
        self.calls += 1
        return _FakeOutput(b"identical-bytes")


class _StochasticImage(ImageProvider):
    """Writes different bytes each call, like an ordinary sampler."""

    def __init__(self) -> None:
        self.calls = 0

    async def generate_single_image(self, prompt, reference_image_paths=None, size=None, **kwargs):
        self.calls += 1
        return _FakeOutput(f"sample-{self.calls}".encode())


class _FakeOutput:
    """Minimal ``ImageOutput`` stand-in; ``save`` persists the payload."""

    def __init__(self, payload: bytes) -> None:
        self.fmt = "b64"
        self.data = payload.decode()
        self.ext = "png"
        self._payload = payload

    def save(self, path) -> None:
        with open(path, "wb") as handle:
            handle.write(self._payload)


def test_compare_outputs_reports_identical_bytes():
    verdict = compare_outputs(b"same", b"same")

    assert verdict.identical is True
    assert verdict.digest_a == hashlib.sha256(b"same").hexdigest()
    assert verdict.digest_a == verdict.digest_b


def test_compare_outputs_reports_different_bytes():
    verdict = compare_outputs(b"one", b"two")

    assert verdict.identical is False
    assert verdict.digest_a != verdict.digest_b
    assert verdict.size_a == 3 and verdict.size_b == 3


def test_verdict_summary_names_the_consequence():
    assert "deterministic" in compare_outputs(b"x", b"x").summary()
    assert "different bytes" in compare_outputs(b"x", b"y").summary()


def test_probe_issues_exactly_two_calls(tmp_path):
    provider = _DeterministicImage()

    verdict = asyncio.run(
        probe_image_idempotency(provider, prompt="a cat", size="1024x1024", workdir=tmp_path)
    )

    assert provider.calls == 2
    assert verdict.identical is True


def test_probe_reports_a_stochastic_provider_as_different(tmp_path):
    verdict = asyncio.run(
        probe_image_idempotency(
            _StochasticImage(), prompt="a cat", size="1024x1024", workdir=tmp_path
        )
    )

    assert verdict.identical is False
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_idempotency_probe.py -v`

Expected: collection error — `ModuleNotFoundError: No module named 'core.api.idempotency_probe'`.

- [ ] **Step 3: Create the probe module**

Create `core/api/idempotency_probe.py`:

```python
"""core.api.idempotency_probe — the two-call idempotency measurement (§13 item 27).

§8 accepts a duplicate-charge window because "no current provider honours an
idempotency key". That premise was inferred backwards from a single incident, so
it is an assumption. This module turns it into evidence: issue the same request
twice and compare the resulting bytes.

Two details are load-bearing:

- The comparison is over **persisted bytes**, not response URLs. Providers may
  serve byte-identical images from two different URLs, so comparing URLs would
  report "different" and wrongly leave the window open.
- The verdict is stated as a consequence, not a score. Identical bytes mean the
  window can be closed; different bytes leave §8's conclusion unchanged, and the
  measurement still upgrades it from assumption to evidence.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from core.api.image_provider import ImageProvider


@dataclass(frozen=True)
class IdempotencyVerdict:
    """The outcome of comparing two responses to one identical request."""

    identical: bool
    digest_a: str
    digest_b: str
    size_a: int
    size_b: int

    def summary(self) -> str:
        """State what the bytes mean for §8's duplicate-charge window."""
        if self.identical:
            return (
                "identical bytes — the provider is deterministic; the duplicate-charge "
                "window can be closed (§8)"
            )
        return (
            "different bytes — the duplicate-charge window stays open; it remains "
            "'shrinkable, not closable' (§8)"
        )


def compare_outputs(first: bytes, second: bytes) -> IdempotencyVerdict:
    """Compare two persisted responses byte-for-byte."""
    return IdempotencyVerdict(
        identical=first == second,
        digest_a=hashlib.sha256(first).hexdigest(),
        digest_b=hashlib.sha256(second).hexdigest(),
        size_a=len(first),
        size_b=len(second),
    )


async def probe_image_idempotency(
    provider: ImageProvider,
    *,
    prompt: str,
    size: str,
    workdir: Path,
) -> IdempotencyVerdict:
    """Issue one request twice and compare the persisted bytes.

    Costs exactly two image calls. The two artifacts are written into
    ``workdir`` so the comparison sees the bytes a caller would actually use,
    rather than a base64 string or a URL.

    Args:
        provider: the live provider; the caller owns the quota cost.
        prompt / size: the request, held identical across both calls.
        workdir: where the two response artifacts are written.

    Returns:
        The verdict from :func:`compare_outputs` over the two files.
    """
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    first_path = workdir / "idempotency-first.png"
    second_path = workdir / "idempotency-second.png"

    first = await provider.generate_single_image(prompt, size=size)
    first.save(str(first_path))
    second = await provider.generate_single_image(prompt, size=size)
    second.save(str(second_path))

    return compare_outputs(first_path.read_bytes(), second_path.read_bytes())
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_idempotency_probe.py -v`

Expected: 5 passed.

- [ ] **Step 5: Create the CLI entry point**

Create `scripts/probe_image_idempotency.py`:

```python
"""Probe whether the image provider honours request identity (design §13 item 27).

Costs exactly two image calls. Run it once and record the verdict in
CHANGELOG.md; if the bytes are identical, §8's duplicate-charge window can be
closed outright.

    .venv/bin/python scripts/probe_image_idempotency.py

Requires a configured provider (``AGNES_API_KEY``, or ``PROVIDER=openai_compat``
plus its base URL and key).
"""

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.api import get_image_provider  # noqa: E402
from core.api.idempotency_probe import probe_image_idempotency  # noqa: E402
from core.config import finished_page_size  # noqa: E402

PROBE_PROMPT = "a bespectacled cat, ink-wash manhua style, single character sheet"


async def main() -> None:
    provider = get_image_provider()
    size = finished_page_size()
    print(f"probing {type(provider).__name__} at {size} with two identical calls")

    with tempfile.TemporaryDirectory() as workdir:
        verdict = await probe_image_idempotency(
            provider, prompt=PROBE_PROMPT, size=size, workdir=Path(workdir)
        )

    print(f"  first  : {verdict.digest_a} ({verdict.size_a} bytes)")
    print(f"  second : {verdict.digest_b} ({verdict.size_b} bytes)")
    print(f"  verdict: {verdict.summary()}")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/python -m pytest`

Expected: all pass. The new modules are import-only additions; nothing existing
imports them yet.

- [ ] **Step 7: Lint and commit**

```bash
.venv/bin/python -m ruff check core tests
.venv/bin/python -m ruff format --check core tests
git add core/api/idempotency_probe.py scripts/probe_image_idempotency.py tests/test_idempotency_probe.py
git commit -m "feat: add the two-call provider idempotency probe"
```

---

## Self-Review

**Spec coverage.** §9 phase 0h names two deliverables: `scripts/measure_merge_cost.py` (Task 1) and `scripts/probe_image_idempotency.py` (Task 2). §13 resolved item 26's requirement that the measured value be *committed to the §9 table* is Task 1 Step 6. §13 resolved item 27's requirement that the idempotency result be recorded in `CHANGELOG.md` is the same step's second half. §5 and §13 item 16 (the `h_env` registry) are already satisfied by the shipped phase 0a work, so they are not repeated.

**Deliberately not covered here.** Phases 0d, 5a, and 5b are the business layer (§12) and get their own plan: they are a different subsystem, and mixing them here would make this plan's "zero quota" claim harder to check at a glance. Phases 0c and 1-3 stay blocked behind this plan's output by design — §9 says a numeric threshold must precede them.

**Placeholder scan.** No `TBD`, no "add appropriate error handling", no "similar to Task N". Every code step carries runnable code; every test step carries the literal assertion.

**Type consistency.** `measure_merge_cost` is produced by Task 1 as an async function returning `MergeCost` and consumed only by Task 1's own script and tests. `MergeCost.total_calls` is a property, asserted as such in `test_probe_repaints_at_least_the_affected_pages`. `compare_outputs` takes `bytes` and returns `IdempotencyVerdict`; `probe_image_idempotency` returns the same type and is consumed by Task 2's script and tests. `SyntheticChat.chat_function_call` matches the `ChatProvider` signature the pipeline calls (`messages, tools, tool_choice`), and `CountingImage.generate_single_image` matches the widened `ImageProvider` signature (`prompt, reference_image_paths=None, size=None, max_retries=None, retry_base_delay=None, **kwargs`) verified against `core/api/image_provider.py:63`.