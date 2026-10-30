"""An empty character extraction must be asked about again, never cached as final.

Measured on the real API: four consecutive runs of ``extract_story_elements`` on
a chunk that names four characters returned zero characters three times, with a
shape-valid answer each time (``{"characters": [], "settings": []}``). The
pipeline accepted it, cached it as the chunk's extraction, and painted a page
with no reference image, then reported success.

``extract_key_beats`` already treats an empty answer as a missed instruction and
asks once more. Character extraction had no such rule, so the same shape-valid
empty answer was final. These tests pin the matching rule: one extra call, a
retry that says what was missing, and the second answer is what the caller gets.
"""

import asyncio

from core.schemas import StoryElements
from core.screenwriter import extract_story_elements

TEXT = "著名小说家r·到山里去进行了一次为时三天的郊游之后，这天清晨返回维也纳。"

CHARACTER = {
    "name": "r",
    "role": "protagonist",
    "l1_prompt": "a novelist in a charcoal overcoat, clean line art",
    "portrait_prompt": "character sheet, clean line art",
}
SETTING = {"name": "火车站", "description": "a small station at dawn", "scene_prompt": "platform"}


class EmptyThenFullChat:
    """First call answers with nothing; the retry names the characters."""

    def __init__(self, *, always_empty: bool = False):
        self.calls = 0
        self.always_empty = always_empty
        self.seen_suffixes: list[str] = []

    async def chat_function_call(self, messages, tools, tool_choice):
        self.calls += 1
        last_user = next(
            (m.get("content", "") for m in reversed(messages) if m.get("role") == "user"), ""
        )
        if self.calls > 1:
            self.seen_suffixes.append(last_user)
        if self.always_empty:
            return {"characters": [], "settings": [], "style_guide": ""}
        if self.calls == 1:
            return {"characters": [], "settings": [], "style_guide": ""}
        return {"characters": [CHARACTER], "settings": [SETTING], "style_guide": "clean ink"}


def test_empty_characters_are_asked_for_again():
    chat = EmptyThenFullChat()

    elements = asyncio.run(extract_story_elements(TEXT, chat=chat))

    assert chat.calls == 2, "an empty answer must not be the final answer"
    assert [c.name for c in elements.characters] == ["r"]


def test_the_retry_says_what_was_missing():
    chat = EmptyThenFullChat()

    asyncio.run(extract_story_elements(TEXT, chat=chat))

    assert chat.seen_suffixes, "the retry must carry a corrective sentence"
    suffix = chat.seen_suffixes[-1]
    assert "no characters" in suffix.lower()


def test_a_second_empty_answer_is_returned_not_raised():
    """One extra call is the budget. The caller decides what an empty list means."""
    chat = EmptyThenFullChat(always_empty=True)

    elements = asyncio.run(extract_story_elements(TEXT, chat=chat))

    assert chat.calls == 2
    assert elements.characters == []


def test_a_non_empty_first_answer_costs_one_call():
    class FullChat:
        calls = 0

        async def chat_function_call(self, messages, tools, tool_choice):
            FullChat.calls += 1
            return {"characters": [CHARACTER], "settings": [SETTING], "style_guide": "ink"}

    asyncio.run(extract_story_elements(TEXT, chat=FullChat()))

    assert FullChat.calls == 1


def test_settings_only_answer_still_retries_because_no_character_was_found():
    """A scene-only chunk legitimately has no characters, but it must be asked once."""
    chat = EmptyThenFullChat(always_empty=True)

    elements = asyncio.run(extract_story_elements(TEXT, chat=chat))

    assert chat.calls == 2
    assert isinstance(elements, StoryElements)
