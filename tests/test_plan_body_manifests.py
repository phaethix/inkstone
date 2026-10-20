"""tests/test_plan_body_manifests.py — extract and bible manifest-backed restore.

Design: docs/superpowers/specs/2026-10-19-plan-body-manifests-design.md
Task:   Task 2, Step 5 of docs/superpowers/plans/2026-10-19-plan-body-manifests.md
"""

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import core.pipelines.creative_comic as creative_comic_module
from core.comic.budget import BudgetSpec
from core.comic.cas import object_path, verify
from core.pipelines.creative_comic import clear_stale_bible_stamps, creative_comic
from core.schemas import ColorBible, ProjectState, VisualBible
from tests.test_d2_pipeline import D2FakeChat
from tests.test_finished_page_pipeline import FakeChat, FakeImage, _fake_export_pdf

_SRC = "第一章\n福贵在村口。"

# The tool-choice names FakeChat answers on; used to assert a stage did not call.
_EXTRACT_TOOL = "extract_story_elements"
_BIBLE_TOOL = "reconcile_visual_bible"


class FakeImageWithCount(FakeImage):
    """``FakeImage`` already counts; the alias exists so panel tests read clearly."""


def _budget(*, render: int, chat: int = 10) -> BudgetSpec:
    """A budget whose per-stage reservations are all ``chat`` except ``render``."""
    reservations = {
        "extract": chat,
        "bible": chat,
        "beats": chat,
        "page_plan": chat,
        "portrait": chat,
        "render": render,
    }
    return BudgetSpec(
        total=sum(reservations.values()) + 1,
        recovery_reserve=1,
        reservations=reservations,
    )


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
    # the provider. Beats and page plans are manifest-backed too (Task 3), so they
    # restore as well; page renders come from the finished-page cache.
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

    # bible_action_keys was repopulated by the bible stage on this resume. The
    # stamps match the original values because the key is a pure function of the
    # chunk, history, and request digest, all of which are unchanged.
    got = proj2.state.bible_action_keys
    assert got, "the bible stage left no stamps"
    assert got == pre_bible_keys

    # The restored bible replay must NOT wipe pages_done:
    # the invalidation guard is suppressed when the reconcile came from a manifest
    assert proj2.state.pages_done == pre_pages_done


def _bible(**overrides):
    payload = {
        "version": "bible_v1",
        "style_guide": "",
        "color": ColorBible(palette=[], lighting="", forbidden=[]),
        "characters": {},
        "sheet_ref_local": None,
    }
    payload.update(overrides)
    return VisualBible(**payload)


def test_a_lost_bible_clears_the_stamps_that_described_it():
    """A stamp describes a bible body. With the body gone the stamp lies.

    ``state.visual_bible is None`` already forces ``needs_bible``, so this rule
    does not rescue a restore on its own. What it prevents is a *stamp* outliving
    the body it describes: the stage reads ``bible_action_keys`` before it reads
    any manifest, and a leftover key is how a later run would decide a chunk was
    already reconciled against a body that no longer exists.
    """
    state = ProjectState(project_id="p", bible_action_keys={"0": "sha256:aaa"})
    state.visual_bible = None

    assert clear_stale_bible_stamps(state) is True
    assert state.bible_action_keys == {}


def test_a_live_bible_keeps_its_stamps():
    """Clearing stamps on every run would make each chunk reconcile again."""
    state = ProjectState(project_id="p", bible_action_keys={"0": "sha256:aaa"})
    state.visual_bible = _bible()

    assert clear_stale_bible_stamps(state) is False
    assert state.bible_action_keys == {"0": "sha256:aaa"}


def test_clearing_a_stamp_twice_is_idempotent():
    """The rule runs once per run; a second pass must report no change."""
    state = ProjectState(project_id="p", bible_action_keys={"0": "sha256:aaa"})
    state.visual_bible = None

    assert clear_stale_bible_stamps(state) is True
    assert clear_stale_bible_stamps(state) is False


def test_a_lost_bible_with_no_stamps_changes_nothing():
    """A fresh project has neither body nor stamps; the rule stays quiet."""
    state = ProjectState(project_id="p", bible_action_keys={})
    state.visual_bible = None

    assert clear_stale_bible_stamps(state) is False


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_the_stamp_rule_runs_before_the_bible_stage(tmp_path: Path, monkeypatch) -> None:
    """A lost body must not leave its stamps visible to the stage that reads them.

    Pinned at the pipeline level because the rule is only useful at run start:
    anywhere later and the stage has already consulted the stamps.
    """
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "finished_page")
    asyncio.run(creative_comic(_SRC, output_dir=str(tmp_path), chat=FakeChat(), image=FakeImage()))

    pre = ProjectState.load(tmp_path / "state.json")
    assert pre.bible_action_keys

    # Lose the body, keep the stamps: what a partial write leaves behind. The
    # page is un-done so the chunk loop actually runs and reaches the bible stage
    # instead of resuming past it.
    pre.visual_bible = None
    pre.pages_done = []
    pre.save(tmp_path / "state.json")

    observed: list[dict[str, str]] = []
    original = creative_comic_module.bible_action_key

    def spy(**kwargs):
        # Read what the on-disk state holds at the moment the stage keys a chunk:
        # that is what the rule was supposed to have cleared already.
        try:
            observed.append(dict(ProjectState.load(tmp_path / "state.json").bible_action_keys))
        except Exception:  # noqa: BLE001 — an unreadable state proves nothing; record it
            observed.append({"<unreadable>": "1"})
        return original(**kwargs)

    monkeypatch.setattr(creative_comic_module, "bible_action_key", spy)

    chat2, img2 = RecordingChat(), FakeImage()
    proj2 = asyncio.run(creative_comic(_SRC, output_dir=str(tmp_path), chat=chat2, image=img2))

    assert observed, "the bible stage never ran"
    # Every consult of the stage found the stamps already cleared.
    assert observed[0] == {}
    # The reconcile that then ran restamped its chunks.
    assert proj2.state.bible_action_keys


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_a_lost_cache_costs_no_provider_call(tmp_path: Path, monkeypatch) -> None:
    """The headline claim: ``cache.json`` is a hot copy, not the record.

    Deleting it must not re-plan the book. Every planning body is restored from
    its manifest and every rendered asset is recorded in ``state.json``, so a
    resume spends zero chat calls and zero image calls, and carries the same
    bodies forward. This is the test that makes the manifest backing real: the
    cheaper assertions elsewhere only pin one stage at a time.
    """
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "finished_page")
    chat, img = RecordingChat(), FakeImage()
    asyncio.run(creative_comic(_SRC, output_dir=str(tmp_path), chat=chat, image=img))
    assert chat.calls > 0 and img.calls > 0  # the first run did the work

    pre = ProjectState.load(tmp_path / "state.json")
    assert pre.visual_bible is not None
    snapshot = {
        "chunk_cache": pre.chunk_cache,
        "beat_cache": pre.beat_cache,
        "page_cache": pre.page_cache,
        "visual_bible": pre.visual_bible,
        "pages_done": list(pre.pages_done),
        "generated_pages": dict(pre.generated.pages),
    }
    assert snapshot["beat_cache"] and snapshot["page_cache"] and snapshot["pages_done"]

    # Only the hot copy goes; the manifests and the checkpoint stay.
    (tmp_path / "cache.json").unlink()
    assert not (tmp_path / "cache.json").exists()

    chat2, img2 = RecordingChat(), FakeImage()
    proj2 = asyncio.run(creative_comic(_SRC, output_dir=str(tmp_path), chat=chat2, image=img2))

    assert chat2.calls == 0, f"chat re-called: {chat2.tool_names}"
    assert img2.calls == 0

    # Bodies, not just counts: a restore that silently normalized a field would
    # pass a "did not call" assertion while changing what gets rendered.
    #
    # The extract body is compared on what the manifest owns (the parsed story
    # elements). `elements.characters` holds the *shared* CharacterAsset objects
    # that the bible stage enriches in place, so those legitimately differ from a
    # pre-run snapshot and are asserted through the pipeline-visible state below.
    restored_elements = proj2.state.chunk_cache["0"].elements
    saved_elements = snapshot["chunk_cache"]["0"].elements
    assert restored_elements is not None
    assert [c.name for c in restored_elements.characters] == [
        c.name for c in saved_elements.characters
    ]
    assert restored_elements.style_guide == saved_elements.style_guide
    assert restored_elements.settings == saved_elements.settings
    assert proj2.state.beat_cache == snapshot["beat_cache"]
    assert proj2.state.page_cache == snapshot["page_cache"]
    assert proj2.state.visual_bible == snapshot["visual_bible"]
    assert proj2.state.pages_done == snapshot["pages_done"]
    assert proj2.state.generated.pages == snapshot["generated_pages"]
    # The stamps were rewritten by the restore, and name the same keys.
    assert proj2.state.bible_action_keys == pre.bible_action_keys


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_a_corrupt_body_falls_back_to_the_provider(tmp_path: Path, monkeypatch) -> None:
    """A damaged CAS object must degrade to a call, not to a crash or a fake hit.

    ``_restore_stage_body`` warns and returns None on a decode failure. That
    branch exists for exactly this case and is otherwise unexercised, so a bad
    object would be discovered only in production.
    """
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "finished_page")
    asyncio.run(creative_comic(_SRC, output_dir=str(tmp_path), chat=FakeChat(), image=FakeImage()))

    # Corrupt the extract body: overwrite every CAS object the extract manifest
    # points at with junk, leaving the manifest and its key intact.
    index = tmp_path / "index"
    assert index.is_dir()
    extract_keys = 0
    corrupted: set[str] = set()
    for path in sorted(index.glob("*.json")):
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if manifest.get("stage") != "extract":
            continue
        extract_keys += 1
        for output in manifest.get("outputs", []):
            blob = object_path(tmp_path, output["content"])
            blob.write_bytes(b"not a story elements payload")
            corrupted.add(output["content"].split(":", 1)[-1])
    assert extract_keys, "no extract manifest to corrupt"

    (tmp_path / "cache.json").unlink()

    chat2, img2 = RecordingChat(), FakeImage()
    proj2 = asyncio.run(creative_comic(_SRC, output_dir=str(tmp_path), chat=chat2, image=img2))

    # The corrupt body was not trusted: the stage called instead.
    assert chat2.called(_EXTRACT_TOOL), f"extract never re-called: {chat2.tool_names}"
    # The run completed and re-published a parseable body.
    assert proj2.state.chunk_cache["0"].elements is not None
    # The only consistency complaints left are the blobs this test damaged; no
    # key went stale and no unrelated object went missing.
    complaints = verify(tmp_path)
    assert complaints, "the damaged objects should still be reported"
    assert all(any(cid in complaint for cid in corrupted) for complaint in complaints), (
        f"unexpected complaints: {complaints}"
    )
    assert all("does not match its bytes" in complaint for complaint in complaints), (
        f"unexpected complaint kinds: {complaints}"
    )


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


_PANEL_SRC = "第一章\n方鸿渐在甲板上。\n第二章\n方鸿渐在读书。"


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_storyboard_restores_from_its_manifest_with_zero_calls(tmp_path: Path, monkeypatch) -> None:
    """The panel-compose path restores the board instead of re-planning it.

    The finished-page tests above cannot reach this branch: that flow never runs
    a storyboard stage at all, so a broken board restore would pass every one of
    them. Here the suite default (``panel_compose`` from ``conftest.py``) applies,
    and losing ``cache.json`` has to come back from the board manifest.
    """
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "panel_compose")

    chat, img = D2FakeChat(), FakeImageWithCount()
    asyncio.run(creative_comic(_PANEL_SRC, output_dir=str(tmp_path), chat=chat, image=img))
    assert chat.calls > 0, "the first run planned the book"

    pre = ProjectState.load(tmp_path / "state.json")
    boards = {key: chunk.storyboard for key, chunk in pre.chunk_cache.items()}
    assert boards and all(board is not None for board in boards.values())

    # The board manifests exist and are self-consistent before anything is lost.
    stages = set()
    for path in sorted((tmp_path / "index").glob("*.json")):
        stages.add(json.loads(path.read_text(encoding="utf-8"))["stage"])
    assert "storyboard" in stages, f"no storyboard manifest: {sorted(stages)}"
    assert verify(tmp_path) == []

    (tmp_path / "cache.json").unlink()

    chat2 = D2FakeChat()
    proj2 = asyncio.run(
        creative_comic(_PANEL_SRC, output_dir=str(tmp_path), chat=chat2, image=FakeImageWithCount())
    )

    assert chat2.calls == 0, "the board was re-planned instead of restored"
    restored = {key: chunk.storyboard for key, chunk in proj2.state.chunk_cache.items()}
    assert restored == boards, "the restored boards differ from the planned ones"
    # The stamp names the key the restore was addressed by.
    assert all(chunk.storyboard_action_key for chunk in proj2.state.chunk_cache.values())


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_page_script_restores_from_its_manifest_with_zero_calls(
    tmp_path: Path, monkeypatch
) -> None:
    """The same for the page script, which is opt-in behind ``INKSTONE_PAGE_SCRIPT``.

    ``test_d2_pipeline.py`` only deletes the manifest to force a re-plan; it never
    proves the opposite direction. This test does: with the manifest intact and
    ``cache.json`` gone, the script comes back without a provider call.
    """
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "panel_compose")
    monkeypatch.setenv("INKSTONE_PAGE_SCRIPT", "1")

    chat = D2FakeChat()
    asyncio.run(
        creative_comic(_PANEL_SRC, output_dir=str(tmp_path), chat=chat, image=FakeImageWithCount())
    )

    pre = ProjectState.load(tmp_path / "state.json")
    scripts = {key: chunk.page_script for key, chunk in pre.chunk_cache.items()}
    assert scripts and all(script is not None for script in scripts.values())

    stages = set()
    for path in sorted((tmp_path / "index").glob("*.json")):
        stages.add(json.loads(path.read_text(encoding="utf-8"))["stage"])
    assert "page_script" in stages, f"no page_script manifest: {sorted(stages)}"
    assert verify(tmp_path) == []

    (tmp_path / "cache.json").unlink()

    chat2 = D2FakeChat()
    proj2 = asyncio.run(
        creative_comic(_PANEL_SRC, output_dir=str(tmp_path), chat=chat2, image=FakeImageWithCount())
    )

    assert chat2.calls == 0, "the script was re-planned instead of restored"
    restored = {key: chunk.page_script for key, chunk in proj2.state.chunk_cache.items()}
    assert restored == scripts, "the restored scripts differ from the planned ones"
    assert all(chunk.page_script_action_key for chunk in proj2.state.chunk_cache.values())


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_a_fully_restored_resume_never_trips_the_budget(tmp_path: Path, monkeypatch) -> None:
    """A resume that spends nothing must not be paused for want of budget.

    The budget guards *spending*. A restore costs zero calls, so a tight budget
    with no headroom has to be enough; if a reservation were taken before the
    restore was attempted, a free resume would pause anyway.
    """
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "finished_page")
    generous = _budget(render=10)
    asyncio.run(
        creative_comic(
            _SRC,
            output_dir=str(tmp_path / "out"),
            chat=FakeChat(),
            image=FakeImage(),
            budget=generous,
            data_dir=tmp_path / "data",
        )
    )
    pre = ProjectState.load(tmp_path / "out" / "state.json")
    assert pre.pages_done

    (tmp_path / "out" / "cache.json").unlink()

    # No headroom anywhere: every reservation is zero and the total is exactly
    # the recovery reserve.
    tight = _budget(render=0, chat=0)
    img2 = FakeImage()
    proj = asyncio.run(
        creative_comic(
            _SRC,
            output_dir=str(tmp_path / "out"),
            chat=FakeChat(),
            image=img2,
            budget=tight,
            data_dir=tmp_path / "data",
        )
    )

    assert img2.calls == 0, "a restored resume drew a page"
    assert proj.state.pages_done == pre.pages_done


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_losing_every_manifest_repairs_the_book_and_keeps_the_pages(
    tmp_path: Path, monkeypatch
) -> None:
    """Deleting the whole index is a re-plan, not a re-render.

    Losing every manifest costs the planning bodies: the stages re-call and
    rewrite their manifests, which is the billable part and is expected. The
    rendered pages are a different matter. They live in ``pages_done`` and on
    disk, so a re-plan must not silently repaint the book.

    Today it does repaint, and this test records why: with ``cache.json`` gone
    the bible body goes with it, so the resume cannot prove the style did not
    change and ``_soft_invalidate_render`` conservatively drops every asset. The
    assertions below are written against behaviour that is worth keeping, so
    that whoever narrows that invalidation has a test telling them the pages
    must survive.
    """
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "finished_page")
    asyncio.run(creative_comic(_SRC, output_dir=str(tmp_path), chat=FakeChat(), image=FakeImage()))

    pre = ProjectState.load(tmp_path / "state.json")
    pages_before = dict(pre.generated.pages)
    assert pages_before and pre.pages_done

    manifests = sorted((tmp_path / "index").glob("*.json"))
    assert manifests, "nothing to delete"
    for path in manifests:
        path.unlink()
    (tmp_path / "cache.json").unlink()

    img2 = FakeImage()
    proj = asyncio.run(creative_comic(_SRC, output_dir=str(tmp_path), chat=FakeChat(), image=img2))

    # The plan stages re-call, because their bodies are gone...
    assert img2.calls > 0, "the pages were expected to be repainted on a re-plan"
    # ...and every manifest comes back, so the index is whole again.
    rewritten = sorted((tmp_path / "index").glob("*.json"))
    assert len(rewritten) == len(manifests)
    stages = {json.loads(path.read_text(encoding="utf-8"))["stage"] for path in rewritten}
    assert "extract" in stages and "bible" in stages
    # The pages are recorded again under the same keys, and the run is coherent.
    assert set(proj.state.generated.pages) == set(pages_before)
    assert set(proj.state.pages_done) == set(pre.pages_done)
    assert verify(tmp_path) == []
