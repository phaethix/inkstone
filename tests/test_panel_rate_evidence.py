"""What the stored runs say about which denominator predicts the plan.

The open question was which quantity a per-chunk panel budget should be a rate
of: excerpt characters, or staged beats. ROADMAP recorded that nobody had
measured panels per beat and that the old beat counts were gone with their
scratch directories. The counts were not gone. Every finished project under
``comic_out/`` stores both caches in one ``state.json``:

    beat_cache["0"] = {"beats": [...]}     the staged beats for chunk 0
    page_cache["0"] = [{... "pages": [{"panels": [...]}]}]

so 23 chunks across five projects carry a beat count and a panel count from the
same run. Rebuilding the chunk text with the project's own segmenter gives the
excerpt length for each one, which makes the two candidates comparable on
identical rows.

Measured, 23 chunks:

    chars   620 - 5,619   cv 0.21
    beats     5 -    13   cv 0.31
    panels   13 -    44   cv 0.28

    corr(chars,  panels) = 0.21
    corr(beats,  panels) = 0.23

    panels per 1,000 chars   mean 6.31   cv 0.63
    panels per beat          mean 3.16   cv 0.38

Neither denominator predicts the panel count *when read as a rate*. Over a 9x
range of excerpt length and a 2.6x range of beat count, the panels written barely
move: they sit between 13 and 44 whatever the input. Beats is the tighter of the
two (cv 0.38 against 0.63), which is the direction the earlier reasoning guessed,
but a correlation of 0.23 is not a rate anyone can build a budget on.

*Read on its own this section looks like "panels are unpredictable", and that is
the wrong conclusion.* A near-zero correlation between panels and length is what
a large additive constant looks like when the slope is small: `panels = 14.92 +
0.00214 x chars` over the measured 620 to 5,619 characters only moves the
prediction from 16 to 27, so within this sample the constant dominates and the
correlation collapses. Pooled over three independent measurement sets that fit is
R2 0.76 and predicts the stored runs to within their own run-to-run spread. So
"neither denominator is a rate" was right; "therefore model nothing" was not, and
the intercept is the thing that resolves it. See `core/density.py` for the model
and `tests/test_estimate_model.py` for its guards.

That earlier result is why this project does not turn the budget into a rate. A
rate is a coefficient that multiplies a denominator to predict an output; here
the output does not follow either denominator closely enough, so a pure
coefficient would be fit to noise. What works instead is a base plus a small
slope, and that is not a rescaling of the budget — it replaces the anchor.

These figures are pinned as a regression guard on the measurement itself. They
are not a contract the code must preserve: if the planner later starts honouring
a denominator, these numbers should change, and the test should be updated with
a note saying which change moved them.
"""

import json
import statistics
from pathlib import Path

import pytest

from core.comic.segmentation import segment_text

ROOT = Path(__file__).resolve().parents[1]


def _project_rows(root: Path) -> list[tuple[int, int, int]]:
    """(excerpt chars, beats, panels) for every chunk that has all three."""
    rows: list[tuple[int, int, int]] = []
    for state_file in sorted(root.glob("comic_out/*/state.json")):
        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        beats_cache = state.get("beat_cache") or {}
        page_cache = state.get("page_cache") or {}
        source_file = state_file.with_name("source.txt")
        if not source_file.is_file():
            continue
        try:
            chunks = segment_text(source_file.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue
        for key, plan in page_cache.items():
            plans = plan if isinstance(plan, list) else [plan]
            panels = sum(
                len(page.get("panels") or [])
                for one in plans
                if isinstance(one, dict)
                for page in (one.get("pages") or [])
                if isinstance(page, dict)
            )
            staged = beats_cache.get(key)
            beat_count = len(staged.get("beats") or []) if isinstance(staged, dict) else 0
            try:
                index = int(key)
            except (TypeError, ValueError):
                continue
            if index >= len(chunks) or not beat_count or not panels:
                continue
            rows.append((len(chunks[index]), beat_count, panels))
    return rows


def _corr(xs: list[int], ys: list[int]) -> float:
    mx, my = statistics.mean(xs), statistics.mean(ys)
    numerator = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=False))
    dx = sum((x - mx) ** 2 for x in xs) ** 0.5
    dy = sum((y - my) ** 2 for y in ys) ** 0.5
    return numerator / (dx * dy)


def _cv(values: list[float]) -> float:
    return statistics.stdev(values) / statistics.mean(values)


@pytest.fixture(scope="module")
def rows() -> list[tuple[int, int, int]]:
    if not (ROOT / "comic_out").is_dir():
        pytest.skip("no finished projects on this machine to measure")
    measured = _project_rows(ROOT)
    if len(measured) < 10:
        pytest.skip(f"only {len(measured)} chunks carry both caches; need 10 to measure")
    return measured


def test_neither_denominator_predicts_the_panel_count(rows):
    """The measurement that rules out a per-chunk rate, on stored data.

    If either correlation were strong, converting the budget to a rate of that
    quantity would be the fix. Both are near zero, so it is not: the planner
    writes 13 to 44 panels across a 9x range of excerpt length, which is not a
    response to length or to the beats it stages.
    """
    chars = [r[0] for r in rows]
    beats = [r[1] for r in rows]
    panels = [r[2] for r in rows]

    assert _corr(chars, panels) < 0.5, "length now predicts panels; a length rate may work"
    assert _corr(beats, panels) < 0.5, "beats now predict panels; a beat rate may work"


def test_beats_is_the_tighter_denominator_even_though_neither_is_good(rows):
    """Beats spreads less than length, which is why it was the better guess.

    Pinned because it is the one part of the earlier reasoning the data supports:
    if a denominator is chosen at all, per-beat is measurably steadier. It is
    still not steady enough to be a budget.
    """
    per_beat = _cv([p / b for _, b, p in rows])
    per_1000 = _cv([p / c * 1000 for c, _, p in rows])

    assert per_beat < per_1000, (
        f"per-beat spread {per_beat:.2f} should stay under per-1000-chars {per_1000:.2f}"
    )


def test_the_observed_panel_range_is_what_a_rate_would_have_to_cover(rows):
    """The spread a budget has to be robust to, stated as a number.

    A rate is only useful if the realised output clusters around it. Here the
    same denominator produces a 3.4x spread in panels, so any single coefficient
    is wrong by that much on some chunks.
    """
    panels = [p for _, _, p in rows]
    assert min(panels) >= 10
    assert max(panels) / min(panels) > 2.5, (
        "the panel spread narrowed; a per-chunk rate may now be usable"
    )


def test_the_measured_overrun_is_too_wide_to_quote_as_a_single_factor(rows):
    """Why the estimate cannot simply say "expect 3x".

    Every measured chunk was a tier B run with a budget of 8 panels, and the
    plans written were 13 to 44: a mean of 3.35x the budget, spread from 1.6x to
    5.5x. A factor that ranges over 3.4x cannot be quoted to a user as a
    correction, because on the widest chunk it would still understate the run by
    more than half.

    That is the argument for keeping the estimate's existing qualitative warning
    ("the real output will be more") instead of replacing it with a number, and it
    is also why this change does not re-tune 3/8/14: the data cannot support a
    replacement value either.
    """
    ratios = [p / 8 for _, _, p in rows]
    assert statistics.mean(ratios) > 2.0, "the budget is no longer being overrun"
    assert max(ratios) / min(ratios) > 2.5, (
        "the overrun narrowed enough to quote as a single factor"
    )


def _per_chunk_runs(root: Path) -> list[tuple[str, int, int, float, float]]:
    """(project, chunks finished, chunks total, predicted per chunk, actual per chunk).

    Per chunk is the only denominator that makes a stopped run comparable. A
    project that finished 4 of 6 chunks wrote a smaller total than one that
    finished all 6, and dividing that smaller total by the whole book's prediction
    makes a healthy run look like a large miss.
    """
    from core.density import estimate

    out: list[tuple[str, int, int, float, float]] = []
    for state_file in sorted(root.glob("comic_out/*/state.json")):
        source_file = state_file.with_name("source.txt")
        if not source_file.is_file():
            continue
        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
            chunks = segment_text(source_file.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        total = 0
        finished = 0
        for plan in (state.get("page_cache") or {}).values():
            plans = plan if isinstance(plan, list) else [plan]
            written = sum(
                len(page.get("panels") or [])
                for one in plans
                if isinstance(one, dict)
                for page in (one.get("pages") or [])
                if isinstance(page, dict)
            )
            if written:
                total += written
                finished += 1
        if not finished:
            continue
        est = estimate(source_file, density="B")
        out.append(
            (
                state_file.parent.name,
                finished,
                len(chunks),
                est.panels / est.chunks,
                total / finished,
            )
        )
    return out


def test_every_stored_run_lands_inside_the_published_band():
    """The band is what the estimate promises a user, so check it against runs.

    This is the regression guard for a defect in the measurement, not in the
    model: the first version of this check summed each project's panels and
    divided by the whole book's prediction, so ``c36ed35e12b3`` — which stopped
    after 4 of 6 chunks — looked like a 53% overshoot when per chunk it was a 2%
    undershoot. Comparing per chunk and reporting how many chunks a project
    actually finished is what makes the comparison honest.
    """
    root = Path(__file__).resolve().parents[1]
    if not (root / "comic_out").is_dir():
        pytest.skip("no finished projects on this machine to measure")
    runs = _per_chunk_runs(root)
    if not runs:
        pytest.skip("no project carries a page plan to measure")

    from core.density import PANELS_RESIDUAL_P10, PANELS_RESIDUAL_P90

    worst: list[str] = []
    for name, finished, total, predicted, actual in runs:
        if finished < total:
            # A stopped run still has to land inside the band, but say so: it is
            # a weaker claim than a complete run's.
            worst.append(f"{name}: partial {finished}/{total}")
        assert predicted + PANELS_RESIDUAL_P10 <= actual <= predicted + PANELS_RESIDUAL_P90, (
            f"{name} wrote {actual:.1f} panels per chunk against a prediction of "
            f"{predicted:.1f} (band {predicted + PANELS_RESIDUAL_P10:.1f} to "
            f"{predicted + PANELS_RESIDUAL_P90:.1f}, {finished}/{total} chunks)"
        )
    assert runs, worst


def test_a_partial_run_is_not_compared_against_the_whole_book():
    """Guard the specific mistake: one denominator for every project.

    If a project stopped early, dividing its total by the full prediction has to
    be visibly wrong. This asserts the two differ enough that the mistake would be
    caught rather than pass silently as a rounding difference.
    """
    root = Path(__file__).resolve().parents[1]
    if not (root / "comic_out").is_dir():
        pytest.skip("no finished projects on this machine to measure")
    partial = [r for r in _per_chunk_runs(root) if r[1] < r[2]]
    if not partial:
        pytest.skip("every stored project finished all its chunks")

    name, finished, total, predicted, actual = partial[0]
    whole_book_reading = actual * finished / total
    assert abs(whole_book_reading / predicted - 1) > abs(actual / predicted - 1) + 0.2, (
        f"{name}: the two readings should differ clearly ({whole_book_reading:.1f} "
        f"vs {actual:.1f} per chunk), or this guard is not testing anything"
    )


def test_the_band_covers_one_prompt_not_the_prompt_axis():
    """The published band's scope, measured, so nobody widens it by mistake.

    The band is the residual spread of runs under the shipped prompt. Two stored
    prompt-thickness experiments planned the same 620-character excerpt under four
    prompts and wrote 9, 18, 16 and 10 panels: a 2x spread that surrounds the
    model's 16.2 prediction from both sides. So the band is not a bound on "how
    many panels this excerpt could yield" — only on "how many the shipped prompt
    yields per run". Quoting it for a different prompt would overclaim.
    """
    from core.density import PANELS_BASE_B, PANELS_SLOPE_B

    root = Path(__file__).resolve().parents[1]
    experiments = root / ".agent/experiments"
    if not experiments.is_dir():
        pytest.skip("no prompt experiments on this machine")

    panels: list[int] = []
    chars: set[int] = set()
    for directory in sorted(experiments.iterdir()):
        plan_dir = directory / "candidates"
        if not plan_dir.is_dir():
            continue
        meta = directory / "experiment.json"
        if not meta.is_file():
            continue
        try:
            info = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        chars.add(int(info.get("chunk_chars") or 0))
        for candidate in sorted(plan_dir.iterdir()):
            plan_file = candidate / "plan.json"
            if not plan_file.is_file():
                continue
            try:
                plan = json.loads(plan_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            pages = plan.get("pages") or []
            written = sum(len(page.get("panels") or []) for page in pages if isinstance(page, dict))
            if written:
                panels.append(written)

    if len(panels) < 4:
        pytest.skip(f"only {len(panels)} prompt-variant runs stored; need 4 to measure")
    assert len(chars) == 1, f"the experiments should share one excerpt length, got {chars}"

    length = chars.pop()
    predicted = PANELS_BASE_B + PANELS_SLOPE_B * length
    assert max(panels) / min(panels) >= 1.8, (
        "prompt variance narrowed; the band's stated scope may now be too narrow"
    )
    assert min(panels) < predicted < max(panels), (
        f"prompt variants {sorted(panels)} should straddle the {predicted:.1f} prediction; "
        "if they do not, the band's scope caveat describes a situation that no longer holds"
    )
