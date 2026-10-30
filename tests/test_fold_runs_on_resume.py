"""tests/test_fold_runs_on_resume.py — a finished project must still heal identity.

Measured: a completed ``cbz`` project resumed in 0.1 seconds, skipped every chunk
as already complete, and came out the other side still holding ``著名小说家r`` and
``著名小说家R`` as two people with two portraits. Every identity repair lived
inside the chunk loop, so the faster the resume short-circuited, the more
permanently the fork survived.

The invariant: repairing the character table is part of loading a project, not
part of rendering it. A run that draws nothing must still be able to fix a
duplicate a previous build created.
"""

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image

from core.api import ChatProvider, ImageProvider
from core.pipelines.creative_comic import creative_comic
from core.schemas import (
    CharacterAsset,
    CharacterCanon,
    CharacterStage,
    ColorBible,
    ComicPagePlan,
    ComicPagePlanSet,
    ProjectState,
    VisualBible,
)

SRC = "第一章\n著名小说家r在火车站买了一份报纸。"
EXTRACTED = "著名小说家r"
RECONCILED = "著名小说家R"
CHARACTER = {
    "name": EXTRACTED,
    "role": "protagonist",
    "l1_prompt": "a novelist in a charcoal overcoat, clean line art",
    "portrait_prompt": "character sheet, clean line art",
}


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
    def __init__(self):
        self.extract_calls = 0
        self.calls = 0

    async def chat_function_call(self, messages, tools, tool_choice):
        self.calls += 1
        name = (tool_choice.get("function") or {}).get("name", "")
        if name == "extract_story_elements":
            self.extract_calls += 1
            return {"characters": [CHARACTER], "settings": [], "style_guide": "ink"}
        if name == "reconcile_visual_bible":
            return {
                "canons": [
                    {
                        "canonical_name": RECONCILED,
                        "aliases": [],
                        "face_lock": "thin angular face, sharp nose",
                        "gender": "male",
                        "role": "protagonist",
                        "palette_notes": "earth tones",
                    }
                ],
                "style_guide": "ink",
            }
        if name == "extract_key_beats":
            return {"beats": [{"beat_id": "gate", "summary": "buys a paper", "must_draw": True}]}
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
                                "action": "the novelist buys a newspaper",
                                "characters": [EXTRACTED],
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


def _fork(state_path: Path, output_dir: Path) -> None:
    """Reproduce a project on disk that holds both spellings of one person."""
    state = ProjectState.load(state_path)
    state.characters[RECONCILED] = CharacterAsset(
        name=RECONCILED, role="protagonist", l1_prompt="stale spellings"
    )
    state.characters[f"{RECONCILED}@adult"] = CharacterAsset(
        name=f"{RECONCILED}@adult", role="protagonist", l1_prompt="stale spellings"
    )
    state.generated.portraits[RECONCILED] = str(output_dir / "assets" / "a.png")
    state.generated.portraits[f"{RECONCILED}@adult"] = str(output_dir / "assets" / "b.png")
    state.visual_bible = VisualBible(
        version="bible_v1",
        style_guide="ink",
        color=ColorBible(palette=[], lighting="", forbidden=[]),
        characters={
            RECONCILED: CharacterCanon(
                canonical_name=RECONCILED,
                face_lock="thin angular face, sharp nose",
                role="protagonist",
                gender="male",
                stages=[CharacterStage(stage="adult", portrait_key=f"{RECONCILED}@adult")],
            )
        },
    )
    state.page_cache["0"] = ComicPagePlanSet(
        chunk_index=0,
        pages=[
            ComicPagePlan(
                page_id="p0",
                purpose="arrival",
                layout_intent="wide",
                panels=[],
                reference_characters=[EXTRACTED],
            )
        ],
    )
    state.save(state_path)


def _case_collisions(output_dir: Path) -> dict[str, list[str]]:
    state = ProjectState.load(output_dir / "state.json")
    folded: dict[str, list[str]] = {}
    for name in state.characters:
        folded.setdefault(name.casefold(), []).append(name)
    return {key: names for key, names in folded.items() if len(names) > 1}


def test_a_finished_project_folds_the_duplicate_it_already_paid_for(tmp_path):
    """The measured bug: a 0-second resume left the fork untouched forever."""
    _run = asyncio.run(
        creative_comic(SRC, output_dir=str(tmp_path), chat=FakeChat(), image=FakeImage())
    )
    _fork(tmp_path / "state.json", tmp_path)
    assert _case_collisions(tmp_path), "the fixture must actually create the fork"

    chat = FakeChat()
    asyncio.run(creative_comic(SRC, output_dir=str(tmp_path), chat=chat, image=FakeImage()))

    assert _case_collisions(tmp_path) == {}, (
        "loading a project must repair a case-collided character table even when "
        "every chunk short-circuits and nothing is drawn"
    )
    assert chat.extract_calls == 0, (
        "the repair must not cost a chat call; it is a local identity fold"
    )


def test_the_fold_keeps_the_portrait_the_pages_use(tmp_path):
    """Folding must not orphan the portrait the plan's spelling already owns."""
    asyncio.run(creative_comic(SRC, output_dir=str(tmp_path), chat=FakeChat(), image=FakeImage()))
    _fork(tmp_path / "state.json", tmp_path)

    asyncio.run(creative_comic(SRC, output_dir=str(tmp_path), chat=FakeChat(), image=FakeImage()))

    state = ProjectState.load(tmp_path / "state.json")
    assert state.visual_bible is not None
    canons = [
        name for name in state.visual_bible.characters if name.casefold() == RECONCILED.casefold()
    ]
    assert len(canons) == 1, f"the bible kept both spellings: {canons}"
    assert state.page_cache["0"].pages[0].reference_characters, (
        "the reference must survive the fold"
    )


def test_a_folded_base_takes_its_stage_rows_with_it(tmp_path):
    """A leftover ``Name@adult`` is a paid portrait of a face nobody references.

    Measured after the first pass: the base rows folded, the canon repaired, and
    ``著名小说家R@adult`` still sat in the table with its own image.
    """
    asyncio.run(creative_comic(SRC, output_dir=str(tmp_path), chat=FakeChat(), image=FakeImage()))
    _fork(tmp_path / "state.json", tmp_path)

    asyncio.run(creative_comic(SRC, output_dir=str(tmp_path), chat=FakeChat(), image=FakeImage()))

    state = ProjectState.load(tmp_path / "state.json")
    stage_rows = [name for name in state.characters if name.startswith(EXTRACTED + "@")]
    assert stage_rows, "the stage row must survive under the surviving base spelling"
    for name in stage_rows:
        assert name.startswith(EXTRACTED + "@"), (
            f"stage row {name!r} keeps the dropped spelling of its base"
        )
    for name in state.characters:
        if "@" not in name:
            continue
        base = name.split("@", 1)[0]
        assert base in state.characters, (
            f"stage row {name!r} has no base row {base!r} left to hang off"
        )
