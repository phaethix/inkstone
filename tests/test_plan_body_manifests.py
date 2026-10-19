"""tests/test_plan_body_manifests.py — extract and bible manifest-backed restore.

Design: docs/superpowers/specs/2026-10-19-plan-body-manifests-design.md
Task:   Task 2, Step 5 of docs/superpowers/plans/2026-10-19-plan-body-manifests.md
"""

import asyncio
from pathlib import Path
from unittest.mock import patch

from core.comic.cas import verify
from core.pipelines.creative_comic import creative_comic
from core.schemas import ProjectState
from tests.test_finished_page_pipeline import FakeChat, FakeImage, _fake_export_pdf

_SRC = "第一章\n福贵在村口。"

# The tool-choice names FakeChat answers on; used to assert a stage did not call.
_EXTRACT_TOOL = "extract_story_elements"
_BIBLE_TOOL = "reconcile_visual_bible"


class RecordingChat(FakeChat):
    """FakeChat that records which tool names were called."""

    def __init__(self) -> None:
        super().__init__()
        self.tool_names: list[str] = []

    async def chat_function_call(self, messages, tools, tool_choice, **kw):
        self.tool_names.append(tool_choice["function"]["name"])
        return await super().chat_function_call(messages, tools, tool_choice, **kw)

    def called(self, name: str) -> bool:
        return name in self.tool_names


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_extract_and_bible_manifests_verify(tmp_path: Path, monkeypatch) -> None:
    """After a finished-page run, all manifests in the index are consistent."""
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "finished_page")
    chat, img = FakeChat(), FakeImage()
    asyncio.run(creative_comic(_SRC, output_dir=str(tmp_path), chat=chat, image=img))

    # verify: no missing CAS blobs, no hash mismatches, no invalid keys
    assert verify(tmp_path) == []


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_cache_loss_restores_extract_and_bible_bodies(tmp_path: Path, monkeypatch) -> None:
    """Delete cache.json; re-run restores chunk_cache.elements and visual_bible with zero
    chat calls, repopulates bible_action_keys, and does not clear pages_done."""
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "finished_page")
    chat, img = RecordingChat(), FakeImage()
    asyncio.run(creative_comic(_SRC, output_dir=str(tmp_path), chat=chat, image=img))

    # Snapshot the heavy fields before deleting cache.json
    pre = ProjectState.load(tmp_path / "state.json")
    pre_chunk0 = pre.chunk_cache.get("0")
    assert pre_chunk0 is not None and pre_chunk0.elements is not None
    pre_bible = pre.visual_bible
    assert pre_bible is not None
    pre_pages_done = list(pre.pages_done)
    assert pre_pages_done
    pre_bible_keys = dict(pre.bible_action_keys)
    assert pre_bible_keys

    (tmp_path / "cache.json").unlink()

    # Same provider class for the resume: the snapshot key (which the manifest is
    # addressed by) is stable across runs, so the bodies can be restored.
    chat2, img2 = RecordingChat(), FakeImage()
    proj2 = asyncio.run(creative_comic(_SRC, output_dir=str(tmp_path), chat=chat2, image=img2))

    # Extract and bible bodies were restored from manifests: neither stage called
    # the provider (beats/page_plan are not yet manifest-backed and still call,
    # which is expected in this slice).
    assert not chat2.called(_EXTRACT_TOOL), f"extract called: {chat2.tool_names}"
    assert not chat2.called(_BIBLE_TOOL), f"bible called: {chat2.tool_names}"

    # chunk_cache.elements was restored from the extract manifest. The raw body's
    # stable content (character names, style, settings) round-trips exactly.
    restored_chunk0 = proj2.state.chunk_cache.get("0")
    assert restored_chunk0 is not None
    assert restored_chunk0.elements is not None
    pre_el, post_el = pre_chunk0.elements, restored_chunk0.elements
    assert [c.name for c in post_el.characters] == [c.name for c in pre_el.characters]
    assert post_el.style_guide == pre_el.style_guide
    assert post_el.settings == pre_el.settings

    # The bible stage enriches the *shared* character objects in place; the light
    # character table (which actually feeds the image prompts) is preserved by the
    # restored reconcile.
    assert proj2.state.characters == pre.characters

    # visual_bible was restored from the bible manifest (via apply_reconcile replay)
    assert proj2.state.visual_bible is not None
    assert proj2.state.visual_bible.content_hash == pre_bible.content_hash

    # bible_action_keys was repopulated (stamp rule cleared it at run start because
    # visual_bible was None; the bible manifest restore put it back)
    assert len(proj2.state.bible_action_keys) > 0
    # The repopulated stamps match the original values
    assert proj2.state.bible_action_keys == pre_bible_keys

    # The restored bible replay must NOT wipe pages_done:
    # the invalidation guard is suppressed when the reconcile came from a manifest
    assert proj2.state.pages_done == pre_pages_done


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_restored_bible_suppresses_soft_invalidation(tmp_path: Path, monkeypatch) -> None:
    """A resume with visual_bible explicitly None (and cache.json deleted) restores the
    bible from its manifest and preserves pages_done and generated.pages."""
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "finished_page")
    chat, img = RecordingChat(), FakeImage()
    proj = asyncio.run(creative_comic(_SRC, output_dir=str(tmp_path), chat=chat, image=img))
    pre_pages_done = set(proj.state.pages_done)
    pre_generated_pages = dict(proj.state.generated.pages)
    assert pre_pages_done

    # Simulate a state where the bible body was lost but the manifest is intact:
    # clear visual_bible and bible_action_keys in state.json (as the stamp rule would
    # do at run start after a cache.json deletion), and also delete cache.json
    state = ProjectState.load(tmp_path / "state.json")
    state.visual_bible = None
    state.bible_action_keys = {}
    state.save(tmp_path / "state.json")
    (tmp_path / "cache.json").unlink()

    chat2, img2 = RecordingChat(), FakeImage()
    proj2 = asyncio.run(creative_comic(_SRC, output_dir=str(tmp_path), chat=chat2, image=img2))

    # The bible was restored from the manifest: the bible stage did not call the
    # provider (beats/page_plan still call in this slice; that is expected).
    assert not chat2.called(_BIBLE_TOOL), f"bible called: {chat2.tool_names}"
    assert not chat2.called(_EXTRACT_TOOL), f"extract called: {chat2.tool_names}"

    # The bible was rebuilt
    assert proj2.state.visual_bible is not None

    # pages_done and generated.pages are preserved (restored=True suppresses the
    # prev_hash=None + had_render_assets soft-invalidation branch)
    assert set(proj2.state.pages_done) == pre_pages_done
    assert set(proj2.state.generated.pages.keys()) == set(pre_generated_pages.keys())
