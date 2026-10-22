"""The model must not be extrapolated below the range it was fitted on.

The fit `panels = 14.92 + 0.00214 x chars` was measured on chunks from 620 to
5,619 characters. For a 150-character chunk it predicts 15.2 panels. Measured on
the independent excerpt (``examples/sample_novel.txt``), short chunks write about
half that:

    chars   first run   rerun   model predicts
      117        11        -        15.2
      150         7        -        15.2
      201         8       10        15.4
      352        12        -        15.7
      502        10        8        16.0

The reruns matter: 201 chars wrote 8 then 10, 502 wrote 10 then 8, so a short
chunk's own scatter is about +-2 panels. That scatter is small compared with the
gap, so the gap is not noise. Fitting the short range alone gives R2 0.14, which
means there is no line to fit down here — a short chunk writes roughly 9 panels
whatever its length, because it has only enough story for a handful of beats.

So the defect is extrapolation, not a bad slope: a straight line through 620-5,619
characters says 15 at 150 characters, and reality says 7 to 11. The fix is to stop
extrapolating and hold the prediction at the lowest measured length instead.
"""

import statistics

from core.density import (
    PANELS_BASE_B,
    PANELS_SHORT_CHARS,
    PANELS_SHORT_PANELS,
    PANELS_SHORT_RESIDUAL_P10,
    PANELS_SHORT_RESIDUAL_P90,
    PANELS_SLOPE_B,
    estimate,
)


def _estimate_for(tmp_path, chars: int):
    """A source of exactly ``chars`` characters that segments to one chunk."""
    text = "第一章 雨夜\n\n" + ("雨落在青石板上，灯光一盏一盏亮起来。" * (chars // 18 + 1))
    path = tmp_path / f"len{chars}.txt"
    path.write_text(text[:chars], encoding="utf-8")
    return estimate(path, density="B")


def test_a_short_chunk_is_not_predicted_above_what_short_chunks_write(tmp_path):
    """150 characters wrote 7 panels; the line predicts 15.2. That is the bug.

    The mean of the short-range measurements is about 9.6, and their own scatter
    is about +-2, so predicting more than 12 is claiming the line knows something
    the measurements contradict.
    """
    measured = [11, 7, 8, 10, 12, 10, 8]  # 117/150/201/201/352/502/502 chars
    short_mean = statistics.mean(measured)
    short_sd = statistics.stdev(measured)

    result = _estimate_for(tmp_path, 150)
    predicted_per_chunk = result.panels / result.chunks

    assert predicted_per_chunk <= short_mean + 2 * short_sd, (
        f"predicting {predicted_per_chunk:.1f} panels for a 150-char chunk, but the "
        f"measurements average {short_mean:.1f} +- {short_sd:.1f} (n={len(measured)})"
    )


def test_the_line_is_not_extrapolated_below_its_fitted_range(tmp_path):
    """At the edge of the fit the two readings must agree, so the join is honest.

    Just above the shortest fitted length the line is correct, and just below it
    the floor is applied. If these disagree the model has a step where the
    evidence says there is a slope.
    """
    line_at_floor = PANELS_BASE_B + PANELS_SLOPE_B * PANELS_SHORT_CHARS
    assert PANELS_SHORT_PANELS <= line_at_floor, (
        "the floor should not exceed what the line predicts at the same length, "
        "or the model would jump upward on shorter input"
    )
    assert PANELS_SHORT_PANELS >= 0.5 * line_at_floor, (
        "the floor should stay near the line's value at the join; a large gap means "
        "the two ranges were measured under different conditions"
    )


def test_a_shorter_chunk_never_predicts_more_than_a_longer_one(tmp_path):
    """Monotonicity: more text cannot mean fewer panels.

    The floor is a flat segment, so this holds by construction — but only if it is
    actually applied as a floor rather than as a replacement for the whole model.
    """
    short = _estimate_for(tmp_path, 150)
    medium = _estimate_for(tmp_path, 1_200)
    assert short.panels <= medium.panels, (
        f"150 chars predicts {short.panels} panels but 1,200 predicts {medium.panels}"
    )


def test_the_floor_is_reported_rather_than_silently_applied(tmp_path):
    """A user with a short file is told why the number is what it is.

    A silent floor is a number with no provenance, which is the failure this whole
    line of work exists to avoid.
    """
    result = _estimate_for(tmp_path, 150)
    combined = " ".join(result.warnings)
    assert "短" in combined and ("实测" in combined or "段落" in combined), (
        f"expected a warning explaining the short-chunk floor, got: {result.warnings}"
    )


# Every short-chunk measurement taken: (chars, panels). The two reruns are the
# evidence that a short chunk's scatter is run-to-run, not a length trend.
SHORT_MEASUREMENTS = [
    (117, 11),
    (150, 7),
    (201, 8),
    (201, 10),
    (352, 12),
    (502, 10),
    (502, 8),
]


def test_the_short_band_covers_every_short_measurement():
    """The band is a promise about where a short run lands, so check it against all.

    An earlier version used +- a percentile band on seven points and missed the
    352-character run, which wrote 12 against a ceiling of 11. With seven samples a
    percentile is a guess dressed as a statistic, so the band is the measured
    spread itself and this test holds it to every point rather than to a shape.
    """
    low = PANELS_SHORT_PANELS + PANELS_SHORT_RESIDUAL_P10
    high = PANELS_SHORT_PANELS + PANELS_SHORT_RESIDUAL_P90
    misses = [(chars, panels) for chars, panels in SHORT_MEASUREMENTS if not low <= panels <= high]
    assert not misses, (
        f"the short band {low:.1f}-{high:.1f} misses {misses}; "
        f"widen it or re-measure rather than letting a run land outside the promise"
    )


def test_the_short_band_is_not_widened_past_what_the_evidence_supports():
    """A band that covers everything by being huge is not a measurement.

    The short measurements span 7 to 12. A band reaching well past both ends would
    pass the coverage test while telling the user nothing, so this pins the width.
    """
    low = PANELS_SHORT_PANELS + PANELS_SHORT_RESIDUAL_P10
    high = PANELS_SHORT_PANELS + PANELS_SHORT_RESIDUAL_P90
    measured_low = min(panels for _, panels in SHORT_MEASUREMENTS)
    measured_high = max(panels for _, panels in SHORT_MEASUREMENTS)

    assert low >= measured_low - 1, (
        f"band floor {low:.1f} reaches below the measured {measured_low}"
    )
    assert high <= measured_high + 1, (
        f"band ceiling {high:.1f} reaches above the measured {measured_high}"
    )
