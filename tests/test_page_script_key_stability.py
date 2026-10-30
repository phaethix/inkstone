"""tests/test_page_script_key_stability.py — the page-script key must not drift mid-run.

The key is the address of a page-script manifest *and* the stamp that says a
cached script is still current. Making it depend on a field the run rewrites
in place turns it into a moving target: the manifest is recorded under the key
computed before the rewrite, and every later attempt computes a different one,
misses its own manifest, and pays for the script again.

Measured before the fix, on a two-chunk panel-compose run: chunk 0 was planned
under ``sha256:7bbd7e9f…`` while the extract body on disk still read
``portrait`` where the live ``CharacterAsset`` read
``adult man, a young man's face``. Deleting ``cache.json`` and resuming
re-planned the script under ``sha256:c15b3e09…``.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

from core.pipelines.creative_comic import _page_script_elements, creative_comic
from core.schemas import CharacterAsset, ProjectState, Setting, StoryElements
from tests.test_d2_pipeline import D2FakeChat, FakeImage, _fake_export_pdf

_SRC = "第一章\n方鸿渐在甲板上。\n第二章\n方鸿渐在读书。"


def _elements(**overrides) -> StoryElements:
    payload = {
        "characters": [CharacterAsset(name="方鸿渐", portrait_prompt="portrait", role="")],
        "settings": [Setting(name="甲板", scene_prompt="deck")],
        "style_guide": "manhua",
    }
    payload.update(overrides)
    return StoryElements(**payload)


def test_rewriting_a_portrait_prompt_does_not_move_the_page_script_key():
    """The bible and L1 steps rewrite these fields in place during a run.

    ``merge_characters`` stores the extracted objects by reference, so the very
    instance keyed at plan time is the one later mutated. If the key read them,
    it would describe the run's progress instead of the chunk.
    """
    elements = _elements()
    before = _page_script_elements(elements)

    elements.characters[0].portrait_prompt = "adult man, a young man's face"
    elements.characters[0].role = "protagonist"
    elements.characters[0].l1_prompt = "hardened identity prompt"
    elements.settings[0].scene_prompt = "a ferry deck at dusk"
    elements.settings[0].description = "the upper deck"

    assert _page_script_elements(elements) == before


def test_the_cast_and_the_place_still_move_the_key():
    """Stability must not become insensitivity: the plan's inputs still count."""
    base = _page_script_elements(_elements())

    assert _page_script_elements(_elements(style_guide="watercolor")) != base
    assert (
        _page_script_elements(
            _elements(
                characters=[
                    CharacterAsset(name="方鸿渐"),
                    CharacterAsset(name="孙柔嘉"),
                ]
            )
        )
        != base
    )
    assert _page_script_elements(_elements(settings=[])) != base


def test_no_rewritten_field_is_key_material():
    """Pin the intent where a future edit will see it.

    ``portrait_prompt``, ``role``, ``l1_prompt``, ``scene_prompt`` and
    ``description`` are all written by ``ensure_character_l1``,
    ``sync_characters_from_bible`` or ``merge_settings`` after the key is taken.
    """
    source = _page_script_elements.__doc__ or ""
    elements = _elements()
    dumped = _page_script_elements(elements)
    for volatile in ("portrait", "protagonist", "deck", "hardened", "upper deck"):
        assert volatile not in dumped, f"{volatile!r} leaked into the key material"
    assert "stable" in source.lower()


@patch("core.comic.export.ExportEngine.export_pdf", _fake_export_pdf)
def test_a_lost_cache_restores_the_script_without_a_call(tmp_path: Path, monkeypatch) -> None:
    """End to end: the same run twice, with only the hot copy deleted between.

    This is the defect as it was found. The pure-function tests above cannot
    catch it on their own: they hold the elements still, while the real run
    mutates the shared objects between planning the script and saving the state.
    """
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "panel_compose")
    monkeypatch.setenv("INKSTONE_PAGE_SCRIPT", "1")

    chat = D2FakeChat()
    asyncio.run(creative_comic(_SRC, output_dir=str(tmp_path), chat=chat, image=FakeImage()))
    pre = ProjectState.load(tmp_path / "state.json")
    planned = {key: chunk.page_script_action_key for key, chunk in pre.chunk_cache.items()}
    assert planned and all(planned.values())

    (tmp_path / "cache.json").unlink()

    chat2 = D2FakeChat()
    proj2 = asyncio.run(
        creative_comic(_SRC, output_dir=str(tmp_path), chat=chat2, image=FakeImage())
    )

    assert chat2.calls == 0, f"the scripts were re-planned: {chat2.calls} call(s)"
    restored = {key: chunk.page_script_action_key for key, chunk in proj2.state.chunk_cache.items()}
    assert restored == planned, "the restore landed on a different key than the plan"


@patch("core.comic.export.ExportEngine.export_pdf", _fake_export_pdf)
def test_the_stamp_survives_the_bible_rewrite_within_one_run(tmp_path: Path, monkeypatch) -> None:
    """A same-process resume must not re-plan either.

    Before the fix the stamp written for chunk 0 no longer matched the key the
    stage computed later in the *same* run, so even a warm ``cache.json`` was
    only safe because the check tolerates an empty stamp.
    """
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "panel_compose")
    monkeypatch.setenv("INKSTONE_PAGE_SCRIPT", "1")

    asyncio.run(
        creative_comic(_SRC, output_dir=str(tmp_path), chat=D2FakeChat(), image=FakeImage())
    )
    pre = ProjectState.load(tmp_path / "state.json")
    planned = {key: chunk.page_script_action_key for key, chunk in pre.chunk_cache.items()}

    # Same cache, new provider: a stable key means the stamps are still current.
    chat2 = D2FakeChat()
    asyncio.run(creative_comic(_SRC, output_dir=str(tmp_path), chat=chat2, image=FakeImage()))

    assert chat2.calls == 0
    post = ProjectState.load(tmp_path / "state.json")
    assert {key: chunk.page_script_action_key for key, chunk in post.chunk_cache.items()} == planned


def test_an_old_stamp_is_still_treated_as_stale(tmp_path: Path) -> None:
    """A project written before the fix carries keys that no longer compute.

    It has to re-plan once and settle, not oscillate: the stamp it writes on
    that run is the new key, so the next resume is free.
    """
    key = _page_script_elements(_elements())
    assert json.loads(key)["characters"] == ["方鸿渐"]
