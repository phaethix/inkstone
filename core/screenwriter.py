"""core.screenwriter — structured extraction and storyboard planning.

Turns raw novel text into the structured contracts (``StoryElements``,
``Storyboard``) the rest of the pipeline consumes, by driving a ChatProvider's
forced function calling. Also applies light content-safety hygiene: a policy
constraint is injected as the system prompt, and caller text can be sanitized
of banned terms before it is sent so the provider's filter is less likely to
reject the call.
"""

import logging

from core.api import get_chat_provider
from core.schemas import Storyboard, StoryElements, to_tool_schema

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are a comic scriptwriter. Extract story elements and plan storyboards "
    "as structured data. Follow content policy: depict characters decently; avoid "
    "nudity, sexual content, smoking, graphic violence, or other prohibited themes."
)

EXTRACT_TOOL = to_tool_schema(
    StoryElements,
    "extract_story_elements",
    "Extract character and setting assets from a novel excerpt.",
)
STORYBOARD_TOOL = to_tool_schema(
    Storyboard,
    "plan_storyboard",
    "Plan the comic panels for one chunk of text as structured data.",
)

# Terms scrubbed before sending text to the model. Extend per project policy.
DEFAULT_BANNED_TERMS: tuple[str, ...] = ()


def _tool_choice(name: str) -> dict:
    return {"type": "function", "function": {"name": name}}


def sanitize_text(
    text: str,
    banned: "list[str] | tuple[str, ...]" = DEFAULT_BANNED_TERMS,
) -> str:
    """Replace each banned term with a redaction block.

    Keeps obviously policy-sensitive words out of the prompt so the provider's
    content filter is less likely to reject the call.
    """
    cleaned = text
    for term in banned:
        if term:
            cleaned = cleaned.replace(term, "■")
    return cleaned


async def extract_story_elements(text: str, *, chat=None) -> StoryElements:
    """Extract characters / settings / style from ``text`` via forced call."""
    chat = chat or get_chat_provider()
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": sanitize_text(text)},
    ]
    args = await chat.chat_function_call(
        messages,
        [EXTRACT_TOOL],
        _tool_choice("extract_story_elements"),
    )
    return StoryElements.model_validate(args)


async def plan_storyboard(text: str, elements: StoryElements, *, chat=None) -> Storyboard:
    """Plan the panels for ``text`` given already-extracted ``elements``."""
    chat = chat or get_chat_provider()
    context = (
        f"{sanitize_text(text)}\n\n"
        "Extracted story elements (reuse these names and descriptions):\n"
        f"{elements.model_dump_json()}"
    )
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": context},
    ]
    args = await chat.chat_function_call(
        messages,
        [STORYBOARD_TOOL],
        _tool_choice("plan_storyboard"),
    )
    return Storyboard.model_validate(args)
