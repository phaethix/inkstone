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

Neither denominator predicts the panel count. Over a 9x range of excerpt length
and a 2.6x range of beat count, the panels written barely move: they sit between
13 and 44 whatever the input. Beats is the tighter of the two (cv 0.38 against
0.63), which is the direction the earlier reasoning guessed, but a correlation of
0.23 is not a rate anyone can build a budget on.

That result is the reason this change does not turn the budget into a rate. A
rate is a coefficient that multiplies a denominator to predict an output; here
the output does not follow either denominator, so the coefficient would be fit to
noise and the per-chunk count would keep being wrong, only more expensively
derived. Nothing that only rescales the number can fix a planner that returns 13
to 44 panels regardless of what it is asked for.

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
