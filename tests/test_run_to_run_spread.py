"""The band has to cover run-to-run spread, which is the largest term here.

Two rounds of measurement converged on the same finding from opposite directions,
so it is written down once with the numbers.

Same chunk, same prompt, tier B, run repeatedly:

    2.5-flash, one chunk      11, 21, 25, 38 panels    mean 23.8  sd 11.2  cv 0.47
    3.0-flash, 1,059 chars     8, 13                  mean 10.5  sd  3.5  cv 0.34
    3.0-flash,   201 chars     8, 10                  mean  9.0  sd  1.4  cv 0.16
    3.0-flash,   502 chars    10,  8                  mean  9.0  sd  1.4  cv 0.16

The spread is 1.25x to 3.5x on identical input. That is larger than any of the
effects the model tries to capture: comparing 620 to 4,594 characters moves the
prediction from 16.3 to 24.8, a 1.5x, which is inside one standard deviation of a
single chunk's own scatter.

This is why an earlier claim was wrong twice over. That claim was that the model
was extrapolating below its fitted range and that short chunks write a flat 9.4.
Measuring 1,059 characters under 3.0 gave 8 and 13 on two runs: the "short chunks
write about half" reading was one run landing at the bottom of a wide spread, and
the same length came back inside the line's band on the rerun.

What survives is narrower and is what these tests pin: a single run can land far
from the point estimate, so the band must be wide enough to contain single-run
scatter rather than only the mean's error. A band that contains a mean but not a
run is a false promise, because the user gets one run.
"""

import statistics

from core.density import (
    PANELS_C_RESIDUAL_P10,
    PANELS_C_RESIDUAL_P90,
    PANELS_FLAT_C,
    PANELS_RESIDUAL_P10,
    PANELS_RESIDUAL_P90,
    estimate,
)

# Every same-input rerun measured, as (label, panels). These are what a user
# experiences as "I ran it twice and got different answers".
SAME_INPUT_RERUNS = {
    "2.5 one chunk": [11, 21, 25, 38],
    "3.0 1059 chars": [8, 13],
    "3.0 201 chars": [8, 10],
    "3.0 502 chars": [10, 8],
}


def test_the_band_is_wide_enough_for_a_single_run_not_just_the_mean():
    """The user gets one run, so the band must cover one run's spread.

    If the band only covered the error of the mean, a user would routinely see
    their run land outside a band that was advertised as where runs land. The
    widest observed single-run excursion is what the band has to reach.
    """
    band = PANELS_RESIDUAL_P90 - PANELS_RESIDUAL_P10
    widest = 0.0
    for runs in SAME_INPUT_RERUNS.values():
        if len(runs) < 2:
            continue
        # How far the worst run sat from the mean of its own group.
        mean = statistics.mean(runs)
        excursion = max(abs(r - mean) for r in runs)
        widest = max(widest, excursion)

    assert band >= widest, (
        f"band spans {band:.1f} panels but a single run has been observed "
        f"{widest:.1f} panels from its own mean; a user would see runs land "
        f"outside the published band"
    )


def test_run_spread_is_larger_than_the_effect_of_length():
    """State the finding the model has to live with, so nobody re-derives it wrong.

    Comparing 620 to 4,594 characters changes the prediction by less than one
    standard deviation of a single chunk's scatter. Any conclusion drawn from a
    single run at one length, compared against a single run at another length, is
    reading noise. This test exists to make that ratio visible and to fail if
    someone narrows the band back to something that implies the opposite.
    """
    from core.density import PANELS_BASE_B, PANELS_SLOPE_B

    prediction_short = PANELS_BASE_B + PANELS_SLOPE_B * 620
    prediction_long = PANELS_BASE_B + PANELS_SLOPE_B * 4594
    length_effect = prediction_long - prediction_short

    widest_sd = max(statistics.stdev(runs) for runs in SAME_INPUT_RERUNS.values() if len(runs) > 1)

    assert widest_sd > length_effect, (
        f"run-to-run sd ({widest_sd:.1f}) should exceed the length effect "
        f"({length_effect:.1f}); if it does not, the earlier reading that length "
        f"drives output may be right again and this evidence needs revisiting"
    )


def test_the_c_band_covers_a_single_c_run():
    """Same requirement for tier C, whose spread was measured at three lengths.

    C wrote 39, 30 and 26 panels at 4,594, 5,619 and 5,043 characters. Those are
    three different lengths, so this is a weaker statement than the tier B reruns
    above, but C's band must at least contain them or it is not describing C.
    """
    observed = [39, 30, 26]
    mean = statistics.mean(observed)
    low = PANELS_FLAT_C + PANELS_C_RESIDUAL_P10
    high = PANELS_FLAT_C + PANELS_C_RESIDUAL_P90

    misses = [p for p in observed if not low <= p <= high]
    assert not misses, (
        f"C band {low:.1f}-{high:.1f} misses measured runs {misses} (mean {mean:.1f})"
    )


def test_a_single_run_outside_the_band_is_reported_as_possible():
    """When a run lands outside, the estimate must have warned it could.

    One of sixteen probes landed outside its band (8 panels at 1,059 characters).
    A band is not a guarantee, but the wording must not read like one, or the user
    is misled about what the number means.
    """
    import tempfile
    from pathlib import Path

    text = "第一章 雨夜\n\n" + ("雨落在青石板上，灯光一盏一盏亮起来。" * 120)
    tmp = Path(tempfile.mkdtemp()) / "s.txt"
    tmp.write_text(text[:1200], encoding="utf-8")
    result = estimate(tmp, density="B")
    combined = " ".join(result.warnings)

    assert "区间" in combined, "the estimate should quote a band at all"
    # The wording must describe the band as observed spread, not as a bound.
    assert "波动" in combined or "离散" in combined, (
        f"the band should be described as observed spread so a run landing "
        f"outside is not read as a broken promise; got: {combined}"
    )
