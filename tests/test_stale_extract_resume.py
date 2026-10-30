"""A stale extraction must be re-run, not short-circuited by finished pages.

The bug: extraction is invalidated when its action key moves (``elements = None``
at the cache check), but the "fully planned chunk" short-circuit just below
decides from ``pageset`` + ``pages_done`` alone. A chunk committed with a broken
extraction therefore resumes in zero seconds, keeps its empty character table,
and never re-extracts — the exact project the empty-extract fix was meant to
repair.

The invariant: a chunk may only short-circuit when the extraction that produced
it is still the extraction this build would run.

Running this build twice cannot produce a stale key: the action key is a pure
function of the chunk, the model, the request digest and the stage source, so two
consecutive runs agree to the byte. What actually goes stale is a cache written
by a *different* build. The first pass here therefore runs under a previous
``_EXTRACT_STAGE_SRC`` — the same axis that moved for real when
``extract_story_elements`` gained its empty-answer retry — which writes both the
old cache key and the old CAS body, exactly the shape a project on disk has after
upgrading.
"""

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image

from core.api import ChatProvider, ImageProvider
from core.pipelines import creative_comic as pipeline
from core.pipelines.creative_comic import creative_comic

SRC = "第一章\n福贵在村口。"
CHARACTER = {
    "name": "福贵",
    "role": "protagonist",
    "l1_prompt": "a lean farmer in coarse cloth, clean line art",
    "portrait_prompt": "character sheet, clean line art",
}
# What the build before the empty-answer retry hashed into its action keys.
OLD_STAGE_SRC = "sha256:an-earlier-build-of-extract"


class FakeImageOutput:
    fmt = "b64"
    data = ""
    ext = "png"

    def save(self, path):
        Image.new("RGB", (20, 30), (90, 90, 90)).save(path)


class FakeImage(ImageProvider):
    def __init__(self):
        self.calls = 0

    async def generate_single_image(self, prompt, reference_image_paths=None, size=None, **kw):
        self.calls += 1
        return FakeImageOutput()


class FakeChat(ChatProvider):
    """Answers extraction with nothing or with the character, and plans one page."""

    def __init__(self, *, empty_extract: bool = False):
        self.empty_extract = empty_extract
        self.extract_calls = 0
        self.calls = 0

    async def chat_function_call(self, messages, tools, tool_choice):
        self.calls += 1
        name = (tool_choice.get("function") or {}).get("name", "")
        if name == "extract_story_elements":
            self.extract_calls += 1
            if self.empty_extract:
                return {"characters": [], "settings": [], "style_guide": "ink"}
            return {"characters": [CHARACTER], "settings": [], "style_guide": "ink"}
        if name == "reconcile_visual_bible":
            return {
                "canons": [
                    {
                        "canonical_name": "福贵",
                        "aliases": [],
                        "face_lock": "lean weathered face",
                        "palette_notes": "earth tones",
                    }
                ],
                "style_guide": "ink",
            }
        if name == "extract_key_beats":
            return {"beats": [{"beat_id": "gate", "summary": "walks in", "must_draw": True}]}
        if name == "plan_comic_pages":
            return {
                "unit_id": "1",
                "pages": [
                    {
                        "page_id": "p0",
                        "purpose": "arrival",
                        "layout_intent": "wide establishing shot",
                        "panels": [
                            {
                                "panel_id": "p0a",
                                "action": "福贵 walks through the gate",
                                "characters": ["福贵"],
                            }
                        ],
                        "lettering_boxes": [],
                    }
                ],
            }
        return {}


def _fake_export_pdf(self, page_dir, out="comic.pdf", layout=None, direction=None):
    Path(out).write_bytes(b"%PDF-1.4\n%%EOF\n")
    return out


@pytest.fixture(autouse=True)
def _fast_and_offline(monkeypatch, tmp_path):
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "finished_page")
    monkeypatch.setenv("INKSTONE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("INKSTONE_HARNESS", "0")
    with patch("core.comic.export.ExportEngine.export_pdf", _fake_export_pdf):
        yield


def _run(output_dir: Path, chat: FakeChat) -> object:
    return asyncio.run(
        creative_comic(SRC, output_dir=str(output_dir), chat=chat, image=FakeImage())
    )


def _first_pass_under_old_build(output_dir: Path, *, empty_extract: bool) -> FakeChat:
    """Commit a chunk whose extraction was keyed by a previous build's stage source."""
    chat = FakeChat(empty_extract=empty_extract)
    with patch.object(pipeline, "_EXTRACT_STAGE_SRC", OLD_STAGE_SRC):
        _run(output_dir, chat)
    assert chat.extract_calls >= 1
    cache = json.loads((output_dir / "cache.json").read_text())
    assert cache["chunk_cache"]["0"]["extract_action_key"], (
        "the first pass must stamp the old build's key, or nothing goes stale"
    )
    return chat


def _stored_extract_key(output_dir: Path) -> str:
    return json.loads((output_dir / "cache.json").read_text())["chunk_cache"]["0"][
        "extract_action_key"
    ]


def test_a_stale_extraction_replaces_an_empty_character_table(tmp_path):
    """The bug's exact shape: pages committed from an empty extraction must heal."""
    _first_pass_under_old_build(tmp_path, empty_extract=True)
    cache = json.loads((tmp_path / "cache.json").read_text())
    assert cache["chunk_cache"]["0"]["elements"]["characters"] == []

    chat = FakeChat()
    proj = _run(tmp_path, chat)

    assert chat.extract_calls >= 1, "a stale extraction must be retried on resume"
    assert "福贵" in proj.state.characters
    assert "福贵" in proj.state.generated.portraits
    assert _stored_extract_key(tmp_path) != OLD_STAGE_SRC


def test_a_stale_extraction_is_not_short_circuited_by_finished_pages(tmp_path):
    """A healthy old extraction must not buy a free pass just because pages exist."""
    _first_pass_under_old_build(tmp_path, empty_extract=False)
    pages = sorted(p.name for p in (tmp_path / "pages").glob("page_*.png"))
    assert pages, "the first pass must leave pages on disk, or nothing is short-circuited"

    chat = FakeChat()
    proj = _run(tmp_path, chat)

    assert chat.extract_calls >= 1, "finished pages must not hide a stale extraction"
    assert "福贵" in proj.state.characters
    # The old key is replaced by the one this build computes, so the resume after
    # this one is cheap again instead of re-extracting forever.
    assert _stored_extract_key(tmp_path) != OLD_STAGE_SRC


def test_a_current_extraction_still_short_circuits(tmp_path):
    """The cheap resume path must survive: same build, same key, no new chat call."""
    _run(tmp_path, FakeChat())

    chat = FakeChat()
    proj = _run(tmp_path, chat)

    assert chat.extract_calls == 0, "an unchanged extraction must not be re-billed"
    assert "福贵" in proj.state.characters
