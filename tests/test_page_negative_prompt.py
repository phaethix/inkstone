"""The negative prompt is a real channel, not just prose in the positive prompt.

A diffusion model treats "NO speech bubbles" in a positive prompt as a hint
that bubbles belong somewhere on the page, so page_c0000_p0000.png came back
with a garbled bubble reading "S:cooas t9!". The provider already accepted a
separate ``negative_prompt`` field; nothing ever sent one.
"""

from core.comic.page_prompt import (
    FINISHED_PAGE_NEGATIVE_PROMPT,
    render_finished_page_negative_prompt,
)


def test_deferred_negative_prompt_bans_bubbles_and_glyphs():
    lowered = render_finished_page_negative_prompt().lower()
    assert "speech bubbles" in lowered
    assert "glyphs" in lowered
    assert "caption" in lowered
    assert "logo" in lowered


def test_negative_prompt_covers_diegetic_props():
    """``DIEGETIC_TEXT_LINE`` alone did not stop a newspaper headline.

    A real run printed "WIENER MORGENPOST" across a newspaper and gibberish on
    a station sign, so the props are named in the negative channel too.
    """
    lowered = render_finished_page_negative_prompt().lower()
    assert "newspaper" in lowered
    assert "book pages" in lowered
    assert "signposts" in lowered or "station signs" in lowered
    assert "posters" in lowered


def test_strict_negative_prompt_extends_the_default():
    strict = render_finished_page_negative_prompt(strict=True)
    assert strict.startswith(FINISHED_PAGE_NEGATIVE_PROMPT)
    assert len(strict) > len(FINISHED_PAGE_NEGATIVE_PROMPT)
    assert "absolutely no text" in strict.lower()


def test_negative_prompt_is_not_the_positive_prompt():
    """The whole point of a separate channel: the two strings must differ."""
    negative = render_finished_page_negative_prompt()
    assert "Finished readable manga/comic page" not in negative
    assert "A4 portrait" not in negative
    assert "dynamic panel layout" not in negative


def test_in_image_lettering_forbids_no_bubbles_negative():
    """``in_image`` asks for lettering, so banning bubbles would contradict it."""
    assert render_finished_page_negative_prompt(lettering="in_image") == ""


def test_negative_prompt_is_stable_across_calls():
    """It feeds the render key; a wobbling string would miss every cache hit."""
    assert render_finished_page_negative_prompt() == render_finished_page_negative_prompt()


def test_negative_prompt_never_leaks_plan_strings():
    """A negative prompt listing the dialogue would draw the dialogue."""
    negative = render_finished_page_negative_prompt()
    assert "This map is moving." not in negative
    assert "MIDNIGHT" not in negative
    assert "Mira" not in negative
