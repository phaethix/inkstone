"""Phase 0f: a rejected page persists a tombstone instead of being retried.

Design §6 requires a content-policy rejection to count as a **hit** on resume,
carrying its ``outcome`` and ``reason``. Today the skip is remembered only by the
page's absence from ``pages_done`` plus its presence in ``skipped_pages``: the
reason is lost, and there is no way for a human to release a page that was
rejected in error. Phase 0f records the tombstone inside the existing
``state.json`` structure (no CAS, no ``index/``) and ships its override path in
the same change, as §13 item 8 requires.
"""

import asyncio
from unittest.mock import patch

from core.comic.identity import clear_tombstones
from core.pipelines.creative_comic import _page_needs_generation, creative_comic
from core.schemas import ProjectState, Tombstone
from tests.test_finished_page_pipeline import (
    FakeChat,
    FakeImage,
    RejectingPageImage,
    _fake_export_pdf,
)

_PATCH = "core.comic.export.ExportEngine.export_pdf"
_SRC = "第一章\n福贵在村口。"


@patch(_PATCH, _fake_export_pdf)
def test_policy_rejection_records_a_tombstone_with_reason(tmp_path, monkeypatch):
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "finished_page")

    proj = asyncio.run(
        creative_comic(_SRC, output_dir=str(tmp_path), chat=FakeChat(), image=RejectingPageImage())
    )

    assert proj.state.skipped_pages  # the legacy skip set is still written
    tombstone = next(iter(proj.state.tombstones.values()))
    assert tombstone.outcome == "rejected"
    assert tombstone.reason == "content_policy"
    assert tombstone.stage == "render.page"


@patch(_PATCH, _fake_export_pdf)
def test_resume_does_not_re_attempt_a_tombstoned_page(tmp_path, monkeypatch):
    """The headline §6 rule: a tombstone is a hit, so no call is re-issued."""
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "finished_page")
    out = str(tmp_path)
    first = asyncio.run(
        creative_comic(_SRC, output_dir=out, chat=FakeChat(), image=RejectingPageImage())
    )
    rejected_key = next(iter(first.state.tombstones))

    retry = RejectingPageImage()
    proj = asyncio.run(creative_comic(_SRC, output_dir=out, chat=FakeChat(), image=retry))

    assert retry.calls == 0
    assert rejected_key in proj.state.tombstones
    assert rejected_key not in proj.state.pages_done


@patch(_PATCH, _fake_export_pdf)
def test_clearing_the_tombstone_permits_a_retry(tmp_path, monkeypatch):
    """§13 item 8: without an override, a page rejected in error is stuck forever."""
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "finished_page")
    out = str(tmp_path)
    first = asyncio.run(
        creative_comic(_SRC, output_dir=out, chat=FakeChat(), image=RejectingPageImage())
    )
    rejected_key = next(iter(first.state.tombstones))

    state = ProjectState.load(tmp_path / "state.json")
    cleared = clear_tombstones(state, [rejected_key])
    state.save(tmp_path / "state.json")

    retry = FakeImage()
    proj = asyncio.run(creative_comic(_SRC, output_dir=out, chat=FakeChat(), image=retry))

    assert cleared == [rejected_key]
    assert retry.calls >= 1  # the page is eligible again
    assert rejected_key in proj.state.pages_done
    assert rejected_key not in proj.state.tombstones
    assert rejected_key not in proj.state.skipped_pages


@patch(_PATCH, _fake_export_pdf)
def test_successful_page_records_no_tombstone(tmp_path, monkeypatch):
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "finished_page")

    proj = asyncio.run(
        creative_comic(_SRC, output_dir=str(tmp_path), chat=FakeChat(), image=FakeImage())
    )

    assert proj.state.tombstones == {}
    assert proj.state.pages_done


def test_clearing_a_tombstone_also_removes_pages_done():
    """A released page must not stay 'done' just because a stale marker was removed."""
    key = "c0000-p0000"
    state = ProjectState(
        project_id="t",
        pages_done=[key],
        stale_pages=[key],
        skipped_pages=[key],
        tombstones={"c0000-p0000": Tombstone(outcome="rejected", reason="content_policy")},
    )

    assert clear_tombstones(state, [key]) == [key]

    assert key not in state.pages_done
    assert key not in state.stale_pages
    assert key not in state.skipped_pages
    assert _page_needs_generation(state, key) is True
