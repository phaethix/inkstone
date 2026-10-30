"""tests/test_bible_covers_every_character.py — a canon per person, or ask again.

Measured on a real run: the excerpt named four people, ``extract_story_elements``
found all four, and ``reconcile_visual_bible`` returned canons for two. The two
guests then had no face lock at all — the visual bible exists to keep a face
consistent across pages, so a character it never described gets no such promise,
and the run still reported success.

The retry mirrors the empty-extract rule: the same model, the same request, one
explicit correction listing exactly who was skipped, bounded to one extra call.
"""

import asyncio

from core.api import ChatProvider
from core.schemas import CharacterAsset
from core.screenwriter import reconcile_visual_bible

NOVELIST = "著名小说家"
SERVANT = "仆人"
GENTLEMAN = "一位先生"
LADY = "一位女士"
TEXT = "著名小说家r·返回维也纳。仆人在门口等着。一位先生和一位女士坐在客厅里等他。"

CHARACTERS = {
    name: CharacterAsset(name=name, role="mentioned", l1_prompt="clean line art")
    for name in (NOVELIST, SERVANT, GENTLEMAN, LADY)
}


def _canon(name: str, **extra) -> dict:
    return {
        "canonical_name": name,
        "face_lock": f"{name} face",
        "gender": "male",
        "narrative_function": "extra",
        **extra,
    }


class ScriptedChat(ChatProvider):
    """Replies with each queued reconcile answer in turn, then repeats the last."""

    def __init__(self, *answers: dict):
        self.answers = list(answers)
        self.calls = 0
        self.seen: list[str] = []

    async def chat_function_call(self, messages, tools, tool_choice):
        self.calls += 1
        # The correction is the last user message; keep it to assert on later.
        if messages:
            self.seen.append(str(messages[-1].get("content", "")))
        index = min(self.calls - 1, len(self.answers) - 1)
        return self.answers[index]


def _result(names: list[str], **extra) -> dict:
    return {"canons": [_canon(n, **extra) for n in names], "style_guide": "ink"}


def test_a_skipped_character_is_asked_about_once_more():
    """The measured bug: two of four people never got a canon."""
    chat = ScriptedChat(
        _result([NOVELIST, SERVANT]),
        _result([NOVELIST, SERVANT, GENTLEMAN, LADY]),
    )

    result = asyncio.run(reconcile_visual_bible(TEXT, CHARACTERS, None, chat=chat))

    assert chat.calls == 2, "a partial canon set must be asked about once more"
    names = {c.canonical_name for c in result.canons}
    assert names == {NOVELIST, SERVANT, GENTLEMAN, LADY}
    assert GENTLEMAN in chat.seen[1] or LADY in chat.seen[1], (
        "the retry must name the characters it is asking about"
    )
    assert "no canon" in chat.seen[1]


def test_a_complete_answer_costs_one_call():
    """The common case must not pay for a retry it does not need."""
    chat = ScriptedChat(_result([NOVELIST, SERVANT, GENTLEMAN, LADY]))

    asyncio.run(reconcile_visual_bible(TEXT, CHARACTERS, None, chat=chat))

    assert chat.calls == 1


def test_an_alias_counts_as_covering_the_character():
    """A person folded into another by alias is accounted for, not skipped."""
    chat = ScriptedChat(
        {
            "canons": [
                _canon(NOVELIST, aliases=[GENTLEMAN]),
                _canon(SERVANT, aliases=[LADY]),
            ],
            "style_guide": "ink",
        }
    )

    asyncio.run(reconcile_visual_bible(TEXT, CHARACTERS, None, chat=chat))

    assert chat.calls == 1, "every name was answered about, so no retry is owed"


def test_a_high_confidence_merge_counts_as_covering_the_character():
    """A merge says 'this is that person', which is an answer about them."""
    chat = ScriptedChat(
        {
            "canons": [_canon(NOVELIST), _canon(SERVANT)],
            "merges": [
                {"alias": GENTLEMAN, "canonical": NOVELIST, "confidence": "high", "reason": "same"},
                {"alias": LADY, "canonical": SERVANT, "confidence": "high", "reason": "same"},
            ],
            "style_guide": "ink",
        }
    )

    asyncio.run(reconcile_visual_bible(TEXT, CHARACTERS, None, chat=chat))

    assert chat.calls == 1


def test_a_low_confidence_merge_does_not_count():
    """A low-confidence merge is a suggestion for a human, not a described person."""
    chat = ScriptedChat(
        {
            "canons": [_canon(NOVELIST), _canon(SERVANT)],
            "merges": [
                {"alias": GENTLEMAN, "canonical": NOVELIST, "confidence": "low", "reason": "maybe"},
                {"alias": LADY, "canonical": SERVANT, "confidence": "low", "reason": "maybe"},
            ],
            "style_guide": "ink",
        }
    )

    asyncio.run(reconcile_visual_bible(TEXT, CHARACTERS, None, chat=chat))

    assert chat.calls == 2, "a low-confidence merge leaves the character undescribed"


def test_a_persistently_incomplete_answer_is_returned_not_raised():
    """One bounded retry, then the caller decides; the run must not abort."""
    chat = ScriptedChat(_result([NOVELIST, SERVANT]))

    result = asyncio.run(reconcile_visual_bible(TEXT, CHARACTERS, None, chat=chat))

    assert chat.calls == 2, "the retry is bounded to one extra call"
    assert {c.canonical_name for c in result.canons} == {NOVELIST, SERVANT}


def test_stage_rows_are_not_demanded_separately():
    """``Name@adult`` hangs off its base row; asking for it would be a false gap."""
    characters = dict(CHARACTERS)
    characters[f"{NOVELIST}@adult"] = CharacterAsset(name=f"{NOVELIST}@adult")
    chat = ScriptedChat(_result([NOVELIST, SERVANT, GENTLEMAN, LADY]))

    asyncio.run(reconcile_visual_bible(TEXT, characters, None, chat=chat))

    assert chat.calls == 1
