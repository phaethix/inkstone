"""An uncapped tier's estimate should read as a range, not a point.

Tier B and C never clip, so their budget is a floor the plan routinely runs past.
The measured overrun on 23 stored chunks is 1.6x to 5.5x, which means the point
value the estimate prints today ("24 格 / 6 页") is not the middle of a
distribution but its bottom edge. A user who plans against it is short by a mean
of 3.35x, and by 5.5x on the widest chunk.

The fix is not to raise the number. The measurement says the planner writes 13 to
44 panels whatever it is asked for, so any single value would be wrong just as
often, only in the other direction. What the data supports is a range, and a range
is what these tests pin: the estimate keeps reporting the floor it can compute
from the budget, and adds the measured band on top of it.

Tier A keeps a point value. It clips, so its budget is what the run produces and
there is no band to report.

The band is B's, measured on B. C's budget is 14 rather than 8, so the same
multipliers would describe a run C has never been observed making. C gets the
floor caveat it already had, without inheriting numbers that were not measured on
it.
"""

from pathlib import Path

from core.density import (
    PANELS_OVERRUN_HIGH,
    PANELS_OVERRUN_LOW,
    estimate,
)


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "book.txt"
    path.write_text(body, encoding="utf-8")
    return path


def _long_book(tmp_path: Path) -> Path:
    return _write(tmp_path, "第一段文字内容较长的示例文本用来触发分段。\n" * 800)


def test_an_uncapped_tier_reports_a_high_bound_not_a_point(tmp_path):
    """The estimate has to answer "how many panels might this become".

    Reporting the budget alone answers "what is the least it can be", which is
    not the question a user deciding whether to spend API calls is asking.
    """
    est = estimate(_long_book(tmp_path), density="B")

    assert est.panels_high is not None, "an uncapped tier should report an upper bound"
    assert est.panels_high > est.panels, "the upper bound has to exceed the floor"
    assert est.panels_high == int(est.panels * PANELS_OVERRUN_HIGH)


def test_the_upper_bound_bounds_the_pages_and_the_cost_too(tmp_path):
    """Panels drive pages, pages drive the page footprint, all three move together.

    A high panel count that leaves pages and cost at the floor would still
    understate the run, because the user pays per panel and reads per page.
    """
    est = estimate(_long_book(tmp_path), density="B")

    assert est.pages_high is not None
    assert est.pages_high >= est.pages
    # ceil(panels / PANELS_PER_PAGE), so a higher panel bound cannot give fewer pages
    assert est.pages_high >= -(-est.panels_high // 4)


def test_a_clipping_tier_reports_no_upper_bound(tmp_path):
    """A clips, so its panel count is what the run produces.

    Attaching a band here would describe a gap the tier does not have.
    """
    est = estimate(_long_book(tmp_path), density="A")

    assert est.panels_high is None
    assert est.pages_high is None


def test_a_measured_project_reports_no_upper_bound(tmp_path):
    """Measured counts are the project's own numbers, not a band around a budget.

    Once a project has planned chunks, the estimate is no longer extrapolating
    from the tier's 8 and there is no gap to widen.
    """
    est = estimate(_long_book(tmp_path), density="B", observed_panels=[14] * 3)

    assert est.panels_high is None
    assert est.pages_high is None


def test_tier_c_does_not_inherit_a_band_measured_on_tier_b(tmp_path):
    """The band came from runs with a budget of 8. C's budget is 14.

    Reusing the multipliers would claim C overruns by the same factor, which has
    not been measured. C keeps its floor caveat and reports no band until its own
    numbers exist.
    """
    est = estimate(_long_book(tmp_path), density="C")

    assert est.panels_high is None
    assert any("下限" in w for w in est.warnings), "C should still report its floor"


def test_the_band_and_the_warning_agree(tmp_path):
    """The printed range and the printed caveat must be the same story.

    The warning is what a user reads; the fields are what a renderer prints. If
    they disagree, one of the two is lying about the same measurement.
    """
    est = estimate(_long_book(tmp_path), density="B")

    text = next(w for w in est.warnings if "下限" in w)
    assert f"{PANELS_OVERRUN_LOW:g}" in text
    assert f"{PANELS_OVERRUN_HIGH:g}" in text
