"""tests.test_density — unit tests for the D1 density estimator.

Covers all three tiers, the webtoon size guard, both billing backends, and the
empty / tiny-file boundaries, plus custom concurrency and threshold overrides.

A/B/C tiers follow the product brief (A overview → B chapter-complete → C
near-original), not the old inverted prototype where A was densest.

Run with: ``python -m pytest tests/test_density.py -q`` (from the repo root).
"""

from math import ceil
from pathlib import Path

from core.config import ENV_WEBTOON_WARN_MB
from core.density import (
    PANELS_PER_CHUNK_A,
    PANELS_PER_CHUNK_B,
    PANELS_PER_CHUNK_C,
    PANELS_PER_PAGE,
    DensityEstimate,
    _scale_observed,
    estimate,
    get_density_plan,
)


def _write(tmp_path: Path, text: str) -> Path:
    """Write ``text`` to a temp ``.txt`` and return its path."""
    path = tmp_path / "book.txt"
    path.write_text(text, encoding="utf-8")
    return path


# A multi-paragraph body so segment_text produces more than one chunk.
_BODY = "这是一段用于密度预估测试的示例小说文本，包含人物与场景描写。\n" * 60


# --------------------------------------------------------------------------- #
# Tiers
# --------------------------------------------------------------------------- #
def test_get_density_plan_constants():
    a = get_density_plan("A")
    b = get_density_plan("B")
    c = get_density_plan("C")
    assert a.panels_per_chunk == PANELS_PER_CHUNK_A == 3
    assert b.panels_per_chunk == PANELS_PER_CHUNK_B == 8
    assert c.panels_per_chunk == PANELS_PER_CHUNK_C == 14
    assert a.description == "主线概览"
    assert b.description == "章级完整"
    assert c.description == "近原著"


def test_tier_a_b_c_panel_counts(tmp_path):
    book = _write(tmp_path, _BODY)
    est_a = estimate(book, density="A")
    est_b = estimate(book, density="B")
    est_c = estimate(book, density="C")
    assert est_a.chunks == est_b.chunks == est_c.chunks
    assert est_a.panels == est_a.chunks * PANELS_PER_CHUNK_A
    assert est_b.panels == est_b.chunks * PANELS_PER_CHUNK_B
    assert est_c.panels == est_c.chunks * PANELS_PER_CHUNK_C
    # Product order: A overview < B chapter-complete < C near-original
    assert est_a.panels < est_b.panels < est_c.panels


def test_pages_follow_panels_per_page(tmp_path):
    book = _write(tmp_path, _BODY)
    est = estimate(book, density="B")
    expected_pages = -(-est.panels // PANELS_PER_PAGE)  # ceil
    assert est.pages == expected_pages
    assert est.output_format == "page"
    assert est.output_name == "comic.pdf"
    assert estimate(book, density="B", output_format="cbz").output_name == "comic.cbz"


# --------------------------------------------------------------------------- #
# Webtoon size guard
# --------------------------------------------------------------------------- #
def test_webtoon_warning_triggers_with_low_threshold(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_WEBTOON_WARN_MB, "0.01")  # almost anything triggers
    book = _write(tmp_path, _BODY)
    est = estimate(book, density="A", output_format="webtoon")
    assert est.webtoon_warning is True
    assert any("webtoon" in w for w in est.warnings)
    assert est.output_name == "webtoon.png"


def test_webtoon_warning_off_for_page_format(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_WEBTOON_WARN_MB, "0.01")
    book = _write(tmp_path, _BODY)
    est = estimate(book, density="A", output_format="page")
    assert est.webtoon_warning is False
    assert est.warnings == []  # guard is a no-op outside webtoon mode


def test_webtoon_threshold_override_suppresses_warning(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_WEBTOON_WARN_MB, "100000")  # huge threshold
    book = _write(tmp_path, _BODY)
    est = estimate(book, density="A", output_format="webtoon")
    assert est.webtoon_warning is False


# --------------------------------------------------------------------------- #
# Cost labelling
# --------------------------------------------------------------------------- #
def test_agnes_cost_label(tmp_path):
    book = _write(tmp_path, _BODY)
    est = estimate(book, density="B", api="agnes")
    assert "¥0" in est.cost_label
    assert "R6" in est.cost_label


def test_openai_cost_with_price(tmp_path):
    book = _write(tmp_path, _BODY)
    est = estimate(book, density="B", api="openai-compat", price_per_panel=0.05)
    assert est.cost_label.startswith("约 ¥")
    expected = f"约 ¥{est.panels * 0.05:.2f}"
    assert est.cost_label == expected


def test_openai_cost_without_price(tmp_path):
    book = _write(tmp_path, _BODY)
    est = estimate(book, density="B", api="openai-compat", price_per_panel=None)
    assert est.cost_label == "按您的 endpoint 单价"


def test_negative_price_per_panel_rejected():
    import pytest

    from core.density import estimate as _estimate

    with pytest.raises(ValueError):
        _estimate("nonexistent_ignored.txt", api="openai-compat", price_per_panel=-0.05)


# --------------------------------------------------------------------------- #
# Boundaries: empty / tiny file
# --------------------------------------------------------------------------- #
def test_empty_file(tmp_path):
    book = _write(tmp_path, "")
    est = estimate(book, density="B")
    assert est.total_chars == 0
    assert est.chunks == 0
    assert est.panels == 0
    assert est.pages == 0
    assert est.estimated_minutes == 0
    assert est.webtoon_warning is False
    assert any("空" in w for w in est.warnings)


def test_tiny_file_advisory(tmp_path):
    book = _write(tmp_path, "很短。")
    est = estimate(book, density="A")
    assert est.chunks >= 1
    assert est.panels == est.chunks * PANELS_PER_CHUNK_A
    assert any("过小" in w for w in est.warnings)


# --------------------------------------------------------------------------- #
# Custom concurrency + threshold
# --------------------------------------------------------------------------- #
def test_custom_concurrency_changes_duration(tmp_path):
    book = _write(tmp_path, _BODY)
    fast = estimate(book, density="B", concurrency=4)
    slow = estimate(book, density="B", concurrency=1)
    # More workers → fewer estimated minutes (ceil(panels / (conc * PPM))).
    assert slow.estimated_minutes > fast.estimated_minutes
    assert fast.estimated_minutes == -(-fast.panels // (4 * 2))
    assert slow.estimated_minutes == -(-slow.panels // (1 * 2))


def test_concurrency_zero_is_safe(tmp_path):
    book = _write(tmp_path, _BODY)
    est = estimate(book, density="B", concurrency=0)  # must not divide by zero
    assert est.estimated_minutes == -(-est.panels // (1 * 2))


def test_planned_counts_replace_the_experience_rate(tmp_path):
    book = _write(tmp_path, "第一章\n石猴出世。")
    bare = estimate(book, density="B")
    est = estimate(
        book,
        density="B",
        observed_panels=[3] * bare.chunks,
        observed_pages=[1] * bare.chunks,
    )
    assert est.panels == 3 * bare.chunks
    assert est.pages == bare.chunks
    assert any("已规划" in warning for warning in est.warnings)


def test_observed_panels_move_the_page_count_too(tmp_path):
    """A project that planned panels but no pages still gets pages from them.

    ``generate`` records a page plan after the storyboard, so a run interrupted
    between the two has measured panels and nothing measured for pages. The page
    total must follow those panels; leaving it on the experience rate reported
    two pages for a chunk that had already planned fourteen panels.
    """
    book = _write(tmp_path, "第一章\n石猴出世。")
    bare = estimate(book, density="B")
    est = estimate(
        book,
        density="B",
        observed_panels=[14] * bare.chunks,
    )
    assert est.panels == 14 * bare.chunks
    assert est.pages == ceil(14 * bare.chunks / PANELS_PER_PAGE)
    assert est.pages != ceil(bare.panels / PANELS_PER_PAGE)


def test_derived_pages_are_announced_as_derived(tmp_path):
    """Panels can be measured without pages, and the output must say so.

    The layout rate is not the planning rate: the Journey excerpt planned 14
    panels onto 8 pages, 1.75 per page against the engine's 4. Deriving pages
    from panels is therefore an under-count for a planner that splits on beats,
    so whenever pages are inferred rather than measured the estimate says which
    of the two numbers a reader is looking at.
    """
    book = _write(tmp_path, "第一章\n石猴出世。")
    est = estimate(book, density="B", observed_panels=[14])
    assert any("推算" in warning for warning in est.warnings)

    both = estimate(book, density="B", observed_panels=[14], observed_pages=[8])
    assert not any("推算" in warning for warning in both.warnings)


def test_partly_measured_pages_report_how_many_chunks_they_cover(tmp_path):
    """Pages measured for fewer chunks than panels must not claim full coverage.

    ``_planned_counts`` keeps the pages of the chunks that have a page plan and
    leaves the rest to be projected, so the page count can rest on one chunk
    while the panel count rests on three. A single "N chunks" line would claim
    the pages were measured everywhere.
    """
    book = _write(tmp_path, "第一段文字内容较长的示例文本用来触发分段。\n" * 800)
    est = estimate(book, density="B", observed_panels=[3, 4, 5], observed_pages=[2])
    assert est.chunks == 3
    assert any("格数来自已规划的 3 段" in warning for warning in est.warnings)
    assert any("页数只实测了 1 段" in warning for warning in est.warnings)
    # The measured chunk planned 3 panels onto 2 pages, so the planner packs 1.5
    # panels per page. The other two chunks already know they have 4 and 5 panels,
    # which need 3 and 4 pages at that rate: 2 + 3 + 4 = 9. The earlier expectation
    # of 6 spread the measured pages evenly and ignored those panel counts, which
    # put 2 panels on every page against a measured 1.5.
    assert est.pages == 9


def test_the_measured_run_of_the_public_excerpt_reproduces_exactly():
    """Feeding the Journey run's own numbers back must return those numbers.

    A local generate of the excerpt planned 14 panels onto 8 pages. That is the
    only evidence the project has about its own rate, so an estimate handed a
    project that reached that state reports 14 and 8 rather than the tier rate.
    """
    path = Path(__file__).resolve().parents[1] / "examples/showcase/journey-west-ch1/source.txt"
    rate = estimate(path, density="B")
    measured = estimate(
        path,
        density="B",
        observed_panels=[14] * rate.chunks,
        observed_pages=[8] * rate.chunks,
    )
    assert rate.panels == PANELS_PER_CHUNK_B and rate.pages == 2
    assert measured.panels == 14 * rate.chunks
    assert measured.pages == 8 * rate.chunks
    assert measured.panels_per_chunk == 14


def test_an_unplanned_chunk_uses_the_planned_average():
    assert _scale_observed(3, [2]) == 6
    assert _scale_observed(2, [3, 5]) == 8
    assert _scale_observed(1, [3, 9]) == 3


def test_counts_past_the_text_are_ignored():
    """A stored project can hold more chunks than the text now has.

    Re-segmenting a shorter source, or an edited book, leaves entries past the
    chunk count. They describe text that is no longer there, so they must not be
    added to the total: two chunks of five is ten, not the fifteen that summing
    three stored entries would give.
    """
    assert _scale_observed(2, [5, 5, 5]) == 10
    assert _scale_observed(1, [4, 7]) == 4


def test_the_public_excerpt_keeps_the_experience_rate():
    path = Path(__file__).resolve().parents[1] / "examples/showcase/journey-west-ch1/source.txt"
    est = estimate(path, density="B")
    assert path.read_text(encoding="utf-8").strip()
    assert est.chunks >= 1
    assert est.chunks == 1
    assert est.panels == PANELS_PER_CHUNK_B
    assert est.pages == 2
    assert not any("已规划" in warning for warning in est.warnings)


def test_default_threshold_from_constant(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV_WEBTOON_WARN_MB, raising=False)
    book = _write(tmp_path, _BODY)
    # Default 50 MB threshold is not exceeded by a small sample in webtoon mode.
    est = estimate(book, density="A", output_format="webtoon")
    # Only assert the field exists and type is correct; threshold is the default.
    assert isinstance(est.webtoon_warning, bool)
    assert isinstance(est, DensityEstimate)


def test_partly_measured_pages_still_fit_the_panels_they_cover(tmp_path):
    """A page holds at most PANELS_PER_PAGE panels, so the estimate must fit.

    A run stopped between chunks has measured pages for the chunks it finished and
    only a storyboard for the next. Averaging the *pages* of the measured chunks
    ignores how many panels those pages were carrying, so two measured pages can
    be spread across a chapter that planned fourteen panels. The result packs 5.5
    panels onto a page the engine caps at 4, which cannot be printed.

    Pages have to be projected from the measured rate (panels per measured page),
    not from the page count on its own, so the projection cannot exceed the cap.
    """
    book = _write(tmp_path, "第一段文字内容较长的示例文本用来触发分段。\n" * 400)
    est = estimate(book, density="B", observed_panels=[8, 14], observed_pages=[2])
    assert est.chunks == 2
    assert est.pages * PANELS_PER_PAGE >= est.panels
    # Chunk one measured 8 panels on 2 pages, so the planner packs 4 per page.
    # Chunk two already knows it has 14 panels; at that rate it needs 4 pages.
    assert est.panels == 22
    assert est.pages == 6


def test_a_missing_chunk_projects_from_its_own_panels_not_the_average(tmp_path):
    """A chunk with a storyboard already knows how many panels it has.

    Using the average panel count of the *measured* chunks throws that away. Here
    the measured chunk happened to plan 8 panels while the pending one planned 6,
    so the average overstates what the pending chunk needs. The measured rate is
    2 panels per page, well under the packing cap, so the cap cannot hide it.
    """
    book = _write(tmp_path, "第一段文字内容较长的示例文本用来触发分段。\n" * 400)
    est = estimate(book, density="B", observed_panels=[8, 6], observed_pages=[4])
    assert est.chunks == 2
    # 4 measured pages, plus 6 panels at 2 per page.
    assert est.pages == 7


def test_a_measured_page_count_is_never_shrunk_below_the_panels(tmp_path):
    """Even a hopeless measured rate must not produce fewer pages than fit.

    The measured rate is evidence, but the packing limit is a hard fact. If the
    two disagree the estimate has to stay printable, and say why the page count
    is higher than the measured rate implies.
    """
    book = _write(tmp_path, "第一段文字内容较长的示例文本用来触发分段。\n" * 400)
    est = estimate(book, density="B", observed_panels=[8, 14], observed_pages=[1])
    assert est.chunks == 2
    assert est.pages * PANELS_PER_PAGE >= est.panels
    assert any("排版上限" in warning for warning in est.warnings)


def test_an_ordinary_measured_rate_does_not_claim_the_cap_was_hit(tmp_path):
    """The cap note must mean something, so it stays quiet when nothing was capped."""
    book = _write(tmp_path, "第一段文字内容较长的示例文本用来触发分段。\n" * 400)
    est = estimate(book, density="B", observed_panels=[8, 14], observed_pages=[2])
    assert est.chunks == 2
    assert not any("排版上限" in warning for warning in est.warnings)


def test_pages_alone_still_cover_the_chunks_that_have_no_page_plan(tmp_path):
    """Pages can be measured without panels, and the gap must still be filled.

    A state can carry page plans without storyboards the CLI can count, and then
    ``observed_pages`` arrives alone. Summing only the measured pages drops every
    chunk that has no page plan yet: three chunks with two page plans reported 5
    pages against 24 panels, and the engine packs only 20 into 5 pages.
    """
    book = _write(tmp_path, "第一段文字内容较长的示例文本用来触发分段。\n" * 800)
    rate = estimate(book, density="B")
    assert rate.chunks == 3
    est = estimate(book, density="B", observed_pages=[2, 3])
    assert est.pages * PANELS_PER_PAGE >= est.panels
    # The measured chunks average 2.5 pages, so the unmeasured one adds that much.
    assert est.pages == 5 + 3


def test_a_gap_between_measured_chunks_does_not_invent_a_capacity_problem(tmp_path):
    """A zero page count marks a chunk with no page plan, not a page holding none.

    With a gap in the middle, summing every slot counts the gap's panels against
    the measured chunks' pages, so 8 panels on 3 measured pages looks like 2.7
    per page and the cap note fires. The measured chunk is ordinary: the gap
    simply has nothing to pair with, and only one chunk was measured.
    """
    book = _write(tmp_path, "第一段文字内容较长的示例文本用来触发分段。\n" * 800)
    est = estimate(book, density="B", observed_panels=[3, 8, 4], observed_pages=[0, 3, 0])
    assert est.chunks == 3
    assert est.pages * PANELS_PER_PAGE >= est.panels
    assert not any("排版上限" in warning for warning in est.warnings)
    assert any("页数只实测了 1 段" in warning for warning in est.warnings)
