"""core.comic.merge_cost_probe — zero-quota instrument for §9 phase 0h.

The threshold that gates phases 0c and 1-3 is "the total billable calls caused
by one typical alias merge" on a fixed sample: 300 pages, the alias on 15 of
them, spanning 3 chunks (§9, §13 resolved item 26). The design's "current
architecture" column is an estimate that must be replaced by a measurement, or
the number is a belief rather than an argument.

This module produces that measurement without spending quota: both providers are
fakes that count calls, and the pipeline is driven exactly as the CLI drives it.
The counted value is the **second** run — the run that happens after a human
merges an alias — because that is the run the threshold describes.

The harness deliberately does not import ``get_chat_provider`` /
``get_image_provider``: a live factory here would turn a CI test into real image
spend. ``tests/test_merge_cost_probe.py`` asserts that is impossible.

Faithfulness note: the fake chat derives its chunk ordinal from the chapter
marker in the chunk text rather than from a call counter. A counter looked
simpler but was wrong — the pipeline may skip or retry a chat call, and page ids
derived from a drifted counter collide, which silently inflated the measured
re-run. Deriving identity from the text makes both runs agree on which page
belongs to which chunk, which is a precondition for the number meaning anything.
"""

from __future__ import annotations

import os
import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from core.api import ChatProvider, ImageProvider
from core.comic.identity import merge_character_alias
from core.pipelines.creative_comic import creative_comic
from core.schemas import ProjectState

# The fixed sample from the §9 threshold table: 20 chunks x 15 pages = 300
# pages, the alias on 5 pages of each of 3 chunks = 15 pages. The density is 5%
# and the affected chunks cross boundaries, which is what the table names.
SAMPLE_CHUNKS = 20
SAMPLE_PAGES_PER_CHUNK = 15
SAMPLE_AFFECTED_CHUNKS = (4, 9, 14)
SAMPLE_AFFECTED_PAGES_PER_CHUNK = 5

BASE_NAME = "方鸿渐"
ALIAS_NAME = "鸿渐"
SETTING_NAME = "甲板"

_CHAPTER_RE = re.compile(r"第(\d+)章")


def _chunk_ordinal(messages) -> int:
    """Recover the chunk ordinal from the chapter marker in the prompt.

    The pipeline passes the chunk text through verbatim in the user message, so
    the marker is the only stable identity available to a fake. Defaults to 0
    for a prompt without a marker (the pipeline never sends one, but a helper
    must not raise on a surprise).
    """
    blob = " ".join(
        str(message.get("content", "")) for message in messages if isinstance(message, dict)
    )
    match = _CHAPTER_RE.search(blob)
    return int(match.group(1)) - 1 if match else 0


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
    """Deterministic chat double for the alias-merge measurement.

    ``ALIAS_NAME`` exists as its own character from the first chunk, so the
    visual bible settles immediately and the build completes. That is a
    faithfulness requirement, not a convenience: introducing the alias mid-book
    changes the bible partway through, which soft-invalidates the render assets
    already produced and leaves the build partial, so the measured re-run would
    include build repair rather than merge cost.

    Only the pages of ``affected_chunks`` reference the alias. Those are exactly
    the pages a human merge collapses onto ``BASE_NAME``, which is what makes the
    merge "typical": a handful of pages per affected chunk, not the whole book.
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

    async def chat_function_call(self, messages, tools, tool_choice, **kwargs):
        self.calls += 1
        name = tool_choice["function"]["name"]
        ordinal = _chunk_ordinal(messages)
        if name == "extract_story_elements":
            return {
                "characters": [
                    {"name": BASE_NAME, "l1_prompt": "a young man", "portrait_prompt": "portrait"},
                    {"name": ALIAS_NAME, "l1_prompt": "a young man", "portrait_prompt": "portrait"},
                ],
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
            return {"unit_id": f"u{ordinal}", "pages": self._pages(ordinal)}
        return {}

    def _canon_names(self) -> list[str]:
        """Both characters exist from the first chunk, so the bible settles once."""
        return [BASE_NAME, ALIAS_NAME]

    def _pages(self, ordinal: int) -> list[dict]:
        affected = ordinal in self.affected_chunks
        pages: list[dict] = []
        for index in range(self.pages_per_chunk):
            use_alias = affected and index < self.affected_pages_per_chunk
            who = ALIAS_NAME if use_alias else BASE_NAME
            pages.append(
                {
                    "page_id": f"u{ordinal}_p{index:04d}",
                    "purpose": "beat",
                    "layout_intent": f"layout-{ordinal}-{index}",
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
    pages_completed: int
    affected_pages: int
    chat_calls: int
    image_calls: int
    merged_keys: list[str] = field(default_factory=list)

    @property
    def total_calls(self) -> int:
        return self.chat_calls + self.image_calls

    @property
    def build_was_complete(self) -> bool:
        """True when the first run finished every page, so the merge is isolated.

        A partial build means the re-run spends calls repairing the build rather
        than applying the merge, which inflates the measurement. This was
        observed, not hypothesised: an earlier version of this probe introduced
        the alias mid-book, the bible changed partway through, the render assets
        built so far were soft-invalidated, and the "merge cost" included the
        repair. The guard below makes that failure loud.
        """
        return self.pages_completed == self.pages


def _fake_export_pdf(self, page_dir, out="comic.pdf", layout="TwoPageRight", direction="R2L"):
    """Stand-in for the manga2pdf CLI (not installed in test/CI envs)."""
    Path(out).write_bytes(b"%PDF-1.4 fake")
    return out


@contextmanager
def _finished_page_mode():
    """Force the finished-page path for the duration of the measurement.

    The §9 threshold table describes the finished-page render path. The pytest
    suite globally opts into ``panel_compose`` (see ``tests/conftest.py``) for
    the older fixtures, so a probe that did not pin the mode would measure a
    different path inside CI than from the script.
    """
    previous = os.environ.get("INKSTONE_RENDER_MODE")
    os.environ["INKSTONE_RENDER_MODE"] = "finished_page"
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("INKSTONE_RENDER_MODE", None)
        else:
            os.environ["INKSTONE_RENDER_MODE"] = previous


def _source_text(chunks: int) -> str:
    """Build ``chunks`` chapter blocks so ``segment_text`` yields one chunk each."""
    lines = [f"第{index + 1}章\n{BASE_NAME}在甲板上。" for index in range(chunks)]
    return "\n".join(lines)


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

    def _chat() -> SyntheticChat:
        return SyntheticChat(
            pages_per_chunk=pages_per_chunk,
            affected_chunks=affected_chunks,
            affected_pages_per_chunk=affected_pages_per_chunk,
        )

    with (
        _finished_page_mode(),
        patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf),
    ):
        await creative_comic(source, output_dir=str(workdir), chat=_chat(), image=CountingImage())

        state = ProjectState.load(state_path)
        pages_completed = len(state.pages_done)
        # ``merge_character_alias`` marks both panel keys (returned) and page
        # keys (written to ``state.stale_pages``). The page keys are what the
        # render stage re-issues, so they are the measurement's denominator.
        merge_character_alias(state, ALIAS_NAME, BASE_NAME)
        merged_pages = sorted(state.stale_pages)
        state.save(state_path)

        chat = _chat()
        image = CountingImage()
        await creative_comic(source, output_dir=str(workdir), chat=chat, image=image)

    result = MergeCost(
        pages=chunks * pages_per_chunk,
        pages_completed=pages_completed,
        affected_pages=len(affected_chunks) * affected_pages_per_chunk,
        chat_calls=chat.calls,
        image_calls=image.calls,
        merged_keys=list(merged_pages),
    )
    if not result.build_was_complete:
        raise RuntimeError(
            f"the sample build finished only {result.pages_completed} of {result.pages} pages, "
            "so the re-run repairs the build rather than measuring the merge; the fake "
            "provider is not faithful (see the module docstring)"
        )
    return result
