# tests/test_beat_count_band.py
"""The beat extractor's count band has to scale with the excerpt.

Measured on one 4,594-character chunk, three runs of the same tier extracted 15,
7 and 7 beats. The panels that followed were 31, 15 and 10 — panels track beats
almost exactly (2.07, 2.14 and 1.43 panels per beat), so the beat count is the
denominator the panel budget was missing, and its own spread was the larger half
of the variance:

    beats        cv 0.48
    panels/beat  cv 0.21
    panels       cv 0.59

The band was the problem, not the model. ``Prefer 4–12 beats`` was the same
sentence for a 200-character excerpt and a 4,594-character one, and 15 beats
satisfied "prefer" while sitting outside the range. A band that is fixed while
the input varies by a factor of twenty cannot bound the count.
"""

from core.schemas import StoryElements
from core.screenwriter import build_beats_request


def _beats_prompt(chars: int) -> str:
    return build_beats_request("甲" * chars, StoryElements()).messages[1]["content"]


def test_the_beat_band_is_lower_for_a_short_excerpt_than_a_long_one():
    """A 200-character excerpt and a 4,600-character one cannot share a band.

    Both used to read "Prefer 4–12 beats". Four beats over 200 characters is one
    beat every fifty characters; the same four over 4,600 is one every 1,150. The
    text is not asking for the same thing, so the sentence must not say so.
    """
    short = _beats_prompt(200)
    long = _beats_prompt(4600)

    assert "4–12" not in short
    assert "4–12" not in long
    assert short != long, "a twentyfold length difference produced one band"


def test_the_beat_band_states_the_length_it_scales_from():
    """The band needs its own denominator, the same way the panel budget did."""
    prompt = _beats_prompt(4600)
    assert "4600" in prompt or "4,600" in prompt


def test_the_beat_band_is_ordered_and_within_one_beat():
    """A band must be usable: lower bound first, and never a single number."""
    from core.screenwriter import _beat_band

    for chars in (80, 200, 900, 2400, 4600, 12000):
        low, high = _beat_band(chars)
        assert 1 <= low < high, f"{chars} chars produced the band {low}-{high}"


def test_the_beat_band_grows_with_length():
    """More text means more turning points to show, never fewer."""
    from core.screenwriter import _beat_band

    lengths = [100, 400, 1200, 3000, 6000, 12000]
    bands = [_beat_band(chars) for chars in lengths]
    for (low_a, _), (low_b, _) in zip(bands, bands[1:], strict=False):
        assert low_b >= low_a
    assert bands[-1][1] > bands[0][1], "the band never opened up across a 120x range"


def test_a_tiny_excerpt_still_asks_for_at_least_one_beat():
    """A two-line excerpt has a turning point or none; the band must not be empty."""
    from core.screenwriter import _beat_band

    low, high = _beat_band(10)
    assert low >= 1
    assert high >= low


def test_the_band_reaches_the_prompt_not_just_the_helper():
    """Wiring the band into a helper alone would leave the template unchanged."""
    prompt = _beats_prompt(4600)
    assert "beats" in prompt
    assert "Prefer 4–12" not in prompt
