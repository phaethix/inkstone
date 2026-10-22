"""An empty beat extraction must not be accepted as a finished answer.

Measured on one 4,594-character chunk, one of three runs of the extractor
returned an empty beat set. Nothing was wrong with the call: the model answered
in the right shape with no beats in it. `extract_key_beats` validated that shape
and returned it, and the pipeline then cached it as this chunk's beats, wrote it
to the stage body on disk, and planned 18 panels against a beat set with nothing
in it.

The failure is quiet in three separate ways, and each is checked here:

1. The extractor does not ask again. A shape-valid empty answer is taken as the
   answer, even though the band sentence in the same prompt asked for seven to
   fourteen beats.
2. The pipeline caches it. `beat_cache[key] = beats` runs for an empty set, so a
   later chunk in the same run, and a later run that restores from disk, both
   reuse the empty result instead of extracting again.
3. The coverage check cannot see it. `uncovered_must_draw_beats` iterates the
   beats that exist, so an empty set has zero uncovered beats and passes.
"""

import asyncio

from core.schemas import KeyBeat, KeyBeatSet, StoryElements
from core.screenwriter import extract_key_beats

ELEMENTS = StoryElements(characters=[], settings=[], style_guide="manhua")

ONE_BEAT = {
    "beats": [
        {
            "beat_id": "open_letter",
            "summary": "thick unsigned letter opened",
            "must_draw": True,
        }
    ]
}


class EmptyThenFullChat:
    """Answers the first extraction empty, then answers properly."""

    def __init__(self) -> None:
        self.calls = 0
        self.user_messages: list[str] = []

    async def chat_function_call(self, messages, tools, tool_choice, **kw):
        self.calls += 1
        self.user_messages.append(str(messages[-1].get("content", "")))
        if self.calls == 1:
            return {"beats": []}
        return ONE_BEAT


class AlwaysEmptyChat:
    """Never finds a beat, however many times it is asked."""

    def __init__(self) -> None:
        self.calls = 0

    async def chat_function_call(self, messages, tools, tool_choice, **kw):
        self.calls += 1
        return {"beats": []}


def test_an_empty_extraction_is_asked_again():
    """One empty answer is a sample, not a result.

    The prompt in the same request asks for a band of beats derived from the
    excerpt length. An answer with none is the model having missed the
    instruction, and the cheapest correction is to say so once rather than to
    plan a whole chunk against nothing.
    """
    chat = EmptyThenFullChat()
    beats = asyncio.run(extract_key_beats("甲板上。", ELEMENTS, chat=chat))

    assert chat.calls == 2, "an empty beat set was accepted without asking again"
    assert [beat.beat_id for beat in beats.beats] == ["open_letter"]


def test_the_retry_says_what_was_missing():
    """A retry that repeats the same prompt invites the same empty answer.

    The corrective sentence has to name the failure, because the first answer
    already satisfied every structural requirement the prompt stated.
    """
    chat = EmptyThenFullChat()
    asyncio.run(extract_key_beats("甲板上。", ELEMENTS, chat=chat))

    retry_text = chat.user_messages[1]
    first_text = chat.user_messages[0]
    assert retry_text != first_text
    assert "no beats" in retry_text.lower()


def test_a_persistently_empty_extraction_is_returned_rather_than_retried_forever():
    """The retry is bounded. A caller still has to get an answer.

    Returning the empty set keeps the decision about what an empty extraction
    means with the caller, which is where the beat cache and the budget live.
    """
    chat = AlwaysEmptyChat()
    beats = asyncio.run(extract_key_beats("甲板上。", ELEMENTS, chat=chat))

    assert isinstance(beats, KeyBeatSet)
    assert beats.beats == []
    assert chat.calls == 2, "the retry has to be bounded to one extra call"


def test_an_empty_extraction_is_not_cached_as_the_chunks_beats():
    """Caching an empty set turns one bad answer into a permanent one.

    The pipeline writes `state.beat_cache[key] = beats` and records the stage
    body on disk, so an empty result is reused for the rest of the run and by
    every later run that restores from that file. The chunk is then committed to
    having no beats, which is a stronger claim than the model made.
    """
    from core.comic.key_beats import cacheable_beats

    empty = KeyBeatSet(beats=[])
    real = KeyBeatSet(beats=[KeyBeat(beat_id="b1", summary="甲板上", must_draw=True)])

    assert cacheable_beats(empty) is None
    assert cacheable_beats(real) is real
    assert cacheable_beats(None) is None


def test_an_empty_extraction_is_not_restored_from_disk_as_the_chunks_beats():
    """An empty stage body on disk is evidence, not a verdict.

    Older runs recorded empty extractions as verified stage bodies. Restoring one
    would make a single answer permanent for the project, so the pipeline treats
    an empty body as a miss and extracts again. Checked on the same helper the
    pipeline uses for both directions, so the two cannot drift.
    """
    from core.comic.key_beats import cacheable_beats

    restored_empty = KeyBeatSet.model_validate({"beats": []})
    assert cacheable_beats(restored_empty) is None

    restored_real = KeyBeatSet.model_validate(
        {"beats": [{"beat_id": "b1", "summary": "甲板上", "must_draw": True}]}
    )
    assert cacheable_beats(restored_real) is restored_real


def test_the_beats_retry_costs_no_extra_budget_reservation():
    """The retry happens inside one charge, so it must not claim a second slot.

    ``_charge("beats", ...)`` runs once, before ``extract_key_beats`` is awaited.
    The empty-answer retry is a second model call inside that one charged item,
    so raising the ``beats`` floor to two would reserve a slot the pipeline never
    spends and would starve ``page_plan`` on a five-call budget:

        floors before: extract 1, bible 1, beats 1, page_plan 2, portrait 1
        allocate(5)  -> beats 2, page_plan 0

    That is a real regression for the tightest budgets a user can ask for, in
    exchange for protecting a call that was never charged separately.
    """
    from core.comic.budget import allocate

    assert allocate(12).reservations["beats"] == 1
    assert allocate(5).reservations["page_plan"] == 1, "a beat retry starved the plan"


def test_a_panel_compose_run_does_not_pay_for_a_beats_stage_it_never_calls():
    """The panel-compose path has no beat stage, so it must not fund one.

    Funding a stage a run never charges would shrink the render allowance for no
    call, which ``allocate`` explicitly avoids.
    """
    from core.comic.budget import allocate

    spec = allocate(12, render_mode="panel_compose")
    assert "beats" not in spec.reservations
