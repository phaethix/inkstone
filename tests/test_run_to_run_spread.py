"""The band has to cover run-to-run spread, which is the largest term here.

What this file measures, and how it knows.

Four full pipeline runs of the same book are stored locally. They share a model
(agnes-2.5-flash), a prompt fingerprint (structure_fingerprint d1c9a363e49a) and
an identical source text, so the panel count of one chunk across those runs is a
genuine rerun of one input:

    runs        chunk (chars)      panels            mean    sd
    b6c3,36da,d48f     4,121       27, 37, 44        36.0    8.5
    b6c3,36da,d48f     4,594       16, 19, 29        21.3    6.8
    c36e,36da,d48f,b6c3 4,925      24, 29, 27, 33    28.3    3.8
    c36e,36da,b6c3,d48f 5,043      24, 27, 27, 34    28.0    4.2
    c36e,36da,b6c3,d48f 5,401      24, 27, 28, 13    23.0    6.9
    c36e,36da,d48f,b6c3 5,619      25, 26, 22, 41    28.5    8.5

Pooled within-chunk sd is 6.7 panels; the spread between the chunks' means is 5.1.
So 63% of the variance is a single run being unlike the next run of the same
input, and 37% is the text differing. Both terms are real and neither may be
treated as the whole story.

An earlier version of this file carried "11, 21, 25, 38" for the 4,594-character
chunk. Those numbers were never measured: they were a hard-coded string inside a
script that printed them back, and the printed line was then read as evidence.
The real runs of that chunk are 16, 19 and 29. The conclusion survived the
correction -- run scatter is still comparable to every effect the model can
resolve -- but the record did not, so it is corrected here and pinned by the audit
below, which recomputes the table from the stored runs whenever they are present.

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
from pathlib import Path

import pytest

from core.density import (
    PANELS_C_RESIDUAL_P10,
    PANELS_C_RESIDUAL_P90,
    PANELS_FLAT_C,
    PANELS_RESIDUAL_P10,
    PANELS_RESIDUAL_P90,
    estimate,
)

# One chunk of one book, re-run by four separate projects. Keyed by the chunk's
# character count, valued by what each run wrote. Provenance is in the docstring;
# `test_the_rerun_table_reproduces_from_the_stored_runs` recomputes it.
RERUNS_BY_CHUNK_CHARS = {
    4121: [27, 37, 44],
    4594: [16, 19, 29],
    4925: [24, 29, 27, 33],
    5043: [24, 27, 27, 34],
    5401: [24, 27, 28, 13],
    5619: [25, 26, 22, 41],
}

# Short probes from the density experiment harness, also same-input reruns.
SAME_INPUT_RERUNS = {
    "2.5 4594 chars": RERUNS_BY_CHUNK_CHARS[4594],
    "3.0 1059 chars": [8, 13],
    "3.0 201 chars": [8, 10],
    "3.0 502 chars": [10, 8],
}

# The project directories the table came from. All four ran the same book.
RERUN_PROJECTS = ("b6c3fc018947", "36dac3d30a2a", "c36ed35e12b3", "d48f851dbead")


def _stored_runs_by_chunk_chars():
    """Recompute the table from local run artifacts, or return {} if absent.

    `comic_out/` is gitignored, so this is an audit that runs where the evidence
    lives and is skipped elsewhere rather than a test that can never pass.
    """
    root = Path("comic_out")
    if not root.is_dir():
        return {}

    from core.comic.segmentation import segment_text

    texts = {}
    for name in RERUN_PROJECTS:
        source = root / name / "source.txt"
        if source.is_file():
            texts[name] = source.read_text(encoding="utf-8")
    if len(texts) < 2:
        return {}

    # Only compare runs of byte-identical sources; a different book is not a rerun.
    book = max(set(texts.values()), key=list(texts.values()).count)
    shared = [n for n, t in texts.items() if t == book]
    if len(shared) < 2:
        return {}

    import json

    chunks = segment_text(book)
    runs = {}
    for name in shared:
        state = root / name / "state.json"
        if not state.is_file():
            continue
        cache = json.loads(state.read_text(encoding="utf-8")).get("page_cache") or {}
        for key, entry in cache.items():
            index = int(key)
            if index >= len(chunks):
                continue
            total = sum(
                len(page.get("panels") or [])
                for page in ((entry or {}).get("pages") or [])
                if isinstance(page, dict)
            )
            if total:
                runs.setdefault(len(chunks[index]), []).append(total)
    return runs


def test_the_rerun_table_reproduces_from_the_stored_runs():
    """A measurement must come back out of the runs it claims to measure.

    The fabricated row this file used to carry would fail exactly here: no stored
    run of the 4,594-character chunk ever wrote 11, 21, 25 or 38 panels.
    """
    stored = _stored_runs_by_chunk_chars()
    if not stored:
        pytest.skip("no local run artifacts to audit against")

    for chars, recorded in RERUNS_BY_CHUNK_CHARS.items():
        assert chars in stored, f"no stored runs at {chars} characters to check"
        assert sorted(recorded) == sorted(stored[chars]), (
            f"the table says {chars} characters wrote {sorted(recorded)}, but the "
            f"stored runs say {sorted(stored[chars])}; the table is not a measurement"
        )


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


def test_run_spread_is_comparable_to_the_effect_of_length():
    """State the finding the model has to live with, so nobody re-derives it wrong.

    Comparing 620 to 4,594 characters moves the prediction by 8.5 panels. The
    pooled within-chunk sd of a rerun is 6.7, and the widest single chunk's sd is
    8.5. The two are the same size, which is the point: one run at one length
    cannot be compared against one run at another length to recover a length
    effect, because the scatter being compared is as large as the effect.

    Stated as a ratio rather than as "sd exceeds the effect", because on the real
    numbers 8.54 against 8.50 is a coin flip and a test that hinges on 0.5% would
    pass or fail on which chunk happened to be widest.
    """
    from core.density import PANELS_BASE_B, PANELS_SLOPE_B

    length_effect = PANELS_SLOPE_B * (4594 - 620)

    within = [statistics.stdev(runs) for runs in RERUNS_BY_CHUNK_CHARS.values() if len(runs) > 1]
    pooled_sd = (sum(s * s for s in within) / len(within)) ** 0.5

    ratio = pooled_sd / length_effect
    assert 0.5 < ratio < 2.0, (
        f"within-chunk sd ({pooled_sd:.1f}) should be the same order as the length "
        f"effect ({length_effect:.1f}); ratio {ratio:.2f} means one term now "
        f"dominates and the caution recorded here no longer applies"
    )
    assert PANELS_BASE_B > 0 and PANELS_SLOPE_B > 0


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

    A band is not a guarantee, but the wording must not read like one, or the
    user is misled about what the number means.
    """
    import tempfile

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
