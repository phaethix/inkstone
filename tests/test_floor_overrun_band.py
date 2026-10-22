"""The floor warning should say how much the floor is exceeded by.

The estimate already tells a tier B user that 8 panels is a floor rather than a
forecast: "格数与页数是下限（8 格 / 2 页）… 实际产出会更多". That is honest but not
actionable. On the 23 stored chunks the plans written were 13 to 44 panels
against a budget of 8 — a mean of 3.35x, spread from 1.6x to 5.5x — so "it will
be more" could mean 13 panels or 44, and the user has no way to tell which end
they are on.

The band is what the data supports and nothing more. It is deliberately not a
single factor: 1.6x to 5.5x is a 3.4x spread, so quoting "expect 3x" would still
understate the widest chunks by over half. A band states the uncertainty the
measurement actually has.

These tests check the warning text only. The numbers behind the band are pinned
in ``tests/test_panel_rate_evidence.py``, which reads the same stored runs, so if
a future planner makes the overrun narrower the band here has to be revisited
rather than silently drifting.
"""

from pathlib import Path

from core.density import estimate


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "book.txt"
    path.write_text(body, encoding="utf-8")
    return path


def _long_book(tmp_path: Path) -> Path:
    return _write(tmp_path, "第一段文字内容较长的示例文本用来触发分段。\n" * 800)


def test_the_floor_warning_quotes_the_measured_overrun_band(tmp_path):
    """A floor with no magnitude is not usable for deciding whether to run.

    8 panels quoted against a measured 13 to 44 tells the user the number is
    low; it does not tell them the run may cost five times the quote. Naming the
    band is the smallest change that makes the warning actionable without
    inventing precision the data does not have.
    """
    est = estimate(_long_book(tmp_path), density="B")

    floor_warnings = [w for w in est.warnings if "下限" in w]
    assert floor_warnings, "tier B should still report its budget as a floor"
    text = floor_warnings[0]
    assert "倍" in text, f"the floor warning states no magnitude: {text!r}"
    assert "1.6" in text and "5.5" in text, (
        f"the floor warning should quote the measured 1.6x-5.5x band: {text!r}"
    )


def test_the_overrun_band_is_attributed_to_measurement_not_to_a_promise(tmp_path):
    """The band is an observation about stored runs, not a guarantee.

    A user who reads "1.6x to 5.5x" as the range they will land in is being
    misled in the other direction: those runs were tier B on one book family,
    and the planner is not deterministic. The warning has to say where the
    numbers came from so its status is clear.
    """
    est = estimate(_long_book(tmp_path), density="B")

    text = next(w for w in est.warnings if "下限" in w)
    assert "实测" in text, f"the band does not say it is measured: {text!r}"


def test_a_clipping_tier_gets_no_overrun_band(tmp_path):
    """Tier A clips, so its budget is what the run produces.

    Quoting an overrun band there would describe a gap the tier does not have.
    """
    est = estimate(_long_book(tmp_path), density="A")

    assert not any("倍" in w for w in est.warnings if "下限" in w)


def test_a_measured_run_gets_no_overrun_band(tmp_path):
    """Once counts are measured, the estimate is not extrapolating from 8.

    The band describes the gap between the tier budget and what a plan tends to
    write. When the project has its own measured counts there is no gap to
    describe, and the measured numbers already supersede the budget.
    """
    est = estimate(_long_book(tmp_path), density="B", observed_panels=[14] * 3)

    assert not any("倍" in w for w in est.warnings if "下限" in w)
