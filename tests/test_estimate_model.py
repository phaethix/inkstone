"""The estimate should be anchored on what a plan writes, not on its budget.

Root cause, measured: the estimate multiplies the tier budget by the chunk count,
but the budget is not what the pipeline produces. The prompt for tiers B and C
states it outright — "this number is a target, not a cap" — so a plan that runs
past it is following its instructions. On the five finished projects under
``comic_out/`` the estimate said 200 panels and the runs wrote 617: every project
understated, by 3.1x in total and 1.8x to 3.8x individually.

So the number the estimate needs is not the budget, it is the output. Two
independent measurement sets agree on its shape:

    23 stored chunks, tier B    13 to 44 panels, median 27
    budget sweep, one excerpt   budget 8 -> 22, budget 14 -> 34
    length ladder, two runs     1,055 chars -> 17, 4,594 chars -> 24

Pooled, 13 points fit ``panels = 14.92 + 0.00214 x chars`` with R2 0.76: a
per-chunk base near 15 that grows about 2.1 panels per 1,000 characters. The same
fit predicts 153 panels for the 6-chunk book whose four runs wrote 152, 182, 100
and 169, a mean of 151. Against the old estimate's 48, that is the difference
between a 3.1x error and a 1% one.

Tier C does not follow that slope. Its three measured lengths wrote 32, 34 and
29.3 panels — cv 0.074 across a 4.4x range of length, so its output is flat where
B's rises. It is modelled as a constant.

Tier A is untouched. It clips, so its budget really is its output, and 3 panels a
chunk is what survives.

The band around the prediction is the per-chunk residual of that fit (p10 -5.6,
p90 +12.3), scaled by chunk count, because the error is per chunk and a longer
book should not get a wider relative band than a short one.
"""

from pathlib import Path

from core.density import (
    PANELS_BASE_B,
    PANELS_FLAT_C,
    PANELS_PER_CHUNK_A,
    PANELS_PER_CHUNK_B,
    PANELS_PER_PAGE,
    PANELS_RESIDUAL_P10,
    PANELS_RESIDUAL_P90,
    PANELS_SLOPE_B,
    PLANNED_PANELS_PER_PAGE,
    estimate,
)

_BODY = "这是一段用于密度预估测试的示例小说文本，包含人物与场景描写。\n" * 60
_BIG = "第一段文字内容较长的示例文本用来触发分段。\n" * 800


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "book.txt"
    path.write_text(body, encoding="utf-8")
    return path


def test_tier_b_is_predicted_from_the_measured_model_not_the_budget(tmp_path):
    """The whole point: stop quoting 8 panels a chunk for a run that writes 25.

    A chunk of the measured length should land near the measured base, not on
    the budget the prompt tells the planner it may exceed.
    """
    est = estimate(_write(tmp_path, _BIG), density="B")

    expected = est.chunks * (PANELS_BASE_B + PANELS_SLOPE_B * (est.total_chars / est.chunks))
    assert abs(est.panels - expected) <= 1, f"{est.panels} should follow the model, got {expected}"
    assert est.panels > est.chunks * PANELS_PER_CHUNK_B, (
        "the estimate must not fall back to the budget the planner is told to ignore"
    )


def test_the_prediction_lands_near_what_stored_runs_wrote(tmp_path):
    """Calibrated against the real book, not against itself.

    Four runs of this 29,644-character text wrote 152, 182, 100 and 169 panels, a
    mean of 151. The model should land inside that spread rather than an order of
    magnitude below it.
    """
    source = Path(__file__).resolve().parents[1] / "comic_out/d48f851dbead/source.txt"
    if not source.is_file():
        return

    est = estimate(source, density="B")

    assert 130 <= est.panels <= 210, f"{est.panels} is outside the four measured runs"


def test_tier_c_is_flat_in_length_where_b_rises(tmp_path):
    """C wrote 32, 34 and 29.3 panels across 1,055 to 4,594 characters.

    A slope fitted to that would be noise, so C is modelled as a constant and its
    prediction for a short chunk should exceed B's rather than track it.
    """
    short = _write(tmp_path, "第一段文字内容较长的示例文本用来触发分段。\n" * 30)
    est_b = estimate(short, density="B")
    est_c = estimate(short, density="C")

    assert est_c.panels > est_b.panels, "C's flat rate is above B's short-chunk rate"
    per_chunk = est_c.panels / est_c.chunks
    assert 0.9 * PANELS_FLAT_C <= per_chunk <= 1.1 * PANELS_FLAT_C, (
        f"C per chunk was {per_chunk}, measured 29 to 34"
    )


def test_pages_follow_the_engine_capacity_not_the_planning_rate(tmp_path):
    """The user's page count comes from the renderer, which re-paginates.

    A plan divides pages on narrative beats and packs 3.35 panels a page, but
    ``LayoutEngine._compose_pages`` ignores that division and re-paginates every
    panel at its own capacity of 4. So the pages the user receives are the
    capacity ones, and the estimate has to quote those; the planning rate is
    recorded as ``PLANNED_PANELS_PER_PAGE`` to explain why a plan looks thinner.
    """
    est = estimate(_write(tmp_path, _BIG), density="B")

    import math

    assert PLANNED_PANELS_PER_PAGE < PANELS_PER_PAGE
    assert est.pages == math.ceil(est.panels / PANELS_PER_PAGE)


def test_the_band_is_the_residual_of_the_fit(tmp_path):
    """The band is the model's own error, not a multiplier on a budget gap."""
    est = estimate(_write(tmp_path, _BIG), density="B")

    assert est.panels_low is not None and est.panels_high is not None
    assert est.panels_low == max(0, round(est.panels + PANELS_RESIDUAL_P10 * est.chunks))
    assert est.panels_high == round(est.panels + PANELS_RESIDUAL_P90 * est.chunks)
    assert est.panels_low < est.panels < est.panels_high


def test_tier_a_still_reports_the_clipped_count_exactly(tmp_path):
    """A clips, so its budget is its output and there is no residual to widen."""
    est = estimate(_write(tmp_path, _BIG), density="A")

    assert est.panels == est.chunks * PANELS_PER_CHUNK_A
    assert est.panels_low is None
    assert est.panels_high is None


def test_a_measured_project_reports_no_band(tmp_path):
    """Measured counts are the project's own numbers, not the model's guess."""
    est = estimate(_write(tmp_path, _BIG), density="B", observed_panels=[14] * 3)

    assert est.panels_low is None
    assert est.panels_high is None
