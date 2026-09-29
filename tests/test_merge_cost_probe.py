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


def test_probe_asserts_the_sample_build_completed(tmp_path):
    """A partial build makes the re-run repair the build, inflating the number."""
    report = _small(tmp_path)

    assert report.pages_completed == report.pages
    assert report.build_was_complete is True


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
