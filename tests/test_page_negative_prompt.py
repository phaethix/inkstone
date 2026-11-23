"""Agnes i2i has no effective negative channel.

The workflow drops negative_prompt before conditioning, so the legacy ban list
is retained only as an audit inventory. The public helper returns an empty
string; all live constraints are positive wording in the image prompt.
"""

from core.comic.page_prompt import (
    FINISHED_PAGE_NEGATIVE_PROMPT,
    render_finished_page_negative_prompt,
)


def test_deferred_negative_prompt_is_empty():
    assert render_finished_page_negative_prompt() == ""


def test_negative_prompt_covers_diegetic_props():
    """``DIEGETIC_TEXT_LINE`` alone did not stop a newspaper headline.

    A real run printed "WIENER MORGENPOST" across a newspaper and gibberish on
    a station sign, so the props are named in the negative channel too.
    """
    lowered = FINISHED_PAGE_NEGATIVE_PROMPT.lower()
    assert "newspaper" in lowered
    assert "book pages" in lowered
    assert "signposts" in lowered or "station signs" in lowered
    assert "posters" in lowered


def test_strict_negative_prompt_extends_the_default():
    assert render_finished_page_negative_prompt(strict=True) == ""


def test_negative_prompt_is_not_the_positive_prompt():
    """The dropped channel must never leak into the prompt sent to the model."""
    assert render_finished_page_negative_prompt() == ""


def test_in_image_lettering_forbids_no_bubbles_negative():
    """``in_image`` asks for lettering, so banning bubbles would contradict it."""
    assert render_finished_page_negative_prompt(lettering="in_image") == ""


def test_negative_prompt_is_stable_across_calls():
    """It feeds the render key; a wobbling string would miss every cache hit."""
    assert render_finished_page_negative_prompt() == render_finished_page_negative_prompt()


def test_negative_prompt_bans_monochrome_character_sketches():
    """Lineart character sketches leaked into finished pages 3 and 4.

    A model appended an uncolored black-and-white character doodle inset beside
    the fully shaded panels. The negative channel must name these explicitly —
    "NO design sheets" alone did not stop a small monochrome figure.
    """
    lowered = FINISHED_PAGE_NEGATIVE_PROMPT.lower()
    assert "monochrome" in lowered or "black-and-white" in lowered
    assert "lineart" in lowered or "line art" in lowered or "uncolored" in lowered
    assert "sketch" in lowered


def test_negative_prompt_bans_extra_people():
    """A second copy of a locked character is the consistency failure.

    Positive cast locks did not stop the model adding an unnamed reader beside
    the declared cast, so the negative channel names the extra person too.
    """
    lowered = FINISHED_PAGE_NEGATIVE_PROMPT.lower()
    assert "extra unnamed" in lowered or "bystander" in lowered
    assert "duplicate" in lowered
    assert "palette" in lowered or "color chart" in lowered
    assert "sheet music" in lowered or "open books" in lowered


def test_negative_prompt_never_leaks_plan_strings():
    """A negative prompt listing the dialogue would draw the dialogue."""
    negative = FINISHED_PAGE_NEGATIVE_PROMPT
    assert "This map is moving." not in negative
    assert "MIDNIGHT" not in negative
    assert "Mira" not in negative
