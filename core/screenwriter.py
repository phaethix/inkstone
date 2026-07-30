"""core.screenwriter — structured extraction and storyboard planning.

Turns raw novel text into the structured contracts (``StoryElements``,
``Storyboard``) the rest of the pipeline consumes, by driving a ChatProvider's
forced function calling. Also applies light content-safety hygiene: a policy
constraint is injected as the system prompt, and caller text can be sanitized
of banned terms before it is sent so the provider's filter is less likely to
reject the call.
"""

import logging

import requests

from core.api import get_chat_provider
from core.schemas import (
    ComicPagePlanSet,
    PageScript,
    Storyboard,
    StoryElements,
    to_tool_schema,
)

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are a comic scriptwriter. Extract story elements and plan storyboards "
    "as structured data. Every image prompt you write must target a manhua/comic "
    "art style: clean black ink line art, soft cel shading, flat colors, and "
    "cinematic panel composition. Follow content policy: depict characters decently; "
    "avoid nudity, sexual content, smoking, graphic violence, or other prohibited themes. "
    "Language rules: keep character names as they appear in the source text; "
    "write every caption / dialogue / sfx line in the same language as "
    "the source excerpt (do not translate Chinese source into English). "
    "Lettering: put narration/time-place in caption, spoken lines in dialogue, "
    "and onomatopoeia in sfx — leave a field null when unused. "
    "Art-direction fields (style_guide, scene_prompt, action, l1_prompt) may stay "
    "in English when that helps image models. "
    "When planning finished pages, describe manga geometry (splash/inset/diagonal), "
    "never only 2x2 or 3x2 grids."
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
PAGE_PLAN_TOOL = to_tool_schema(
    ComicPagePlanSet,
    "plan_comic_pages",
    "Plan finished comic pages for one text unit: per-page purpose, "
    "dynamic layout_intent, and panel specs with source-language lettering.",
)

# Optional local scrub list. Empty by default: content policy is enforced by the
# system prompt + upstream provider filters. Callers may pass a non-empty
# ``banned`` list (or replace this tuple) when project policy needs local redaction.
DEFAULT_BANNED_TERMS: tuple[str, ...] = ()


def _tool_choice(name: str) -> dict:
    return {"type": "function", "function": {"name": name}}


def sanitize_text(
    text: str,
    banned: "list[str] | tuple[str, ...]" = DEFAULT_BANNED_TERMS,
) -> str:
    """Replace each banned term with a redaction block.

    With the default empty ``banned`` list this is a no-op. Pass terms explicitly
    (or set ``DEFAULT_BANNED_TERMS``) when local scrubbing is desired; the system
    prompt and upstream provider remain the primary content-policy controls.
    """
    cleaned = text
    for term in banned:
        if term:
            cleaned = cleaned.replace(term, "■")
    return cleaned


def is_content_policy_rejection(exc: Exception) -> bool:
    """Return True only when a provider supplies explicit policy evidence.

    HTTP 400 is intentionally insufficient: providers also use it for invalid
    request parameters, unsupported tool schemas, and malformed payloads. Those
    operational failures must remain visible and retryable rather than being
    persisted as permanently skipped content.
    """
    evidence = [str(exc)]
    if isinstance(exc, requests.HTTPError):
        response = getattr(exc, "response", None)
        if response is not None:
            evidence.append(getattr(response, "text", "") or "")
    text = " ".join(evidence).lower()
    return any(k in text for k in ("content_policy", "content policy", "policy_violation"))


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
        f"{elements.model_dump_json()}\n\n"
        "Reminder: caption / dialogue / sfx must match the source language "
        "(if the excerpt is Chinese, lettering must be Chinese — never English translation). "
        "Split narration into caption, speech into dialogue, and onomatopoeia into sfx."
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


async def plan_comic_pages(
    text: str, elements: StoryElements, *, chat=None
) -> ComicPagePlanSet:
    """Plan finished readable pages for ``text`` given ``elements``."""
    chat = chat or get_chat_provider()
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"{sanitize_text(text)}\n\n"
                f"Known elements:\n{elements.model_dump_json()}\n\n"
                "Plan finished readable pages (not a flat 2x2 collage). "
                "Each page needs purpose, layout_intent, and panels with "
                "caption/dialogue/sfx in the source language."
            ),
        },
    ]
    args = await chat.chat_function_call(
        messages,
        [PAGE_PLAN_TOOL],
        _tool_choice("plan_comic_pages"),
    )
    return ComicPagePlanSet.model_validate(args)


PAGE_SCRIPT_TOOL = to_tool_schema(
    PageScript,
    "plan_page_script",
    "Fill optional per-page audit fields for one chunk's storyboard "
    "(required information, causal links, source spans). Not a quality gate.",
)


async def plan_page_script(
    board: Storyboard, elements: StoryElements, chunk: str, *, chat=None
) -> PageScript:
    """为 storyboard 后的单个 chunk 生成可选遗留 PageScript 审计元数据（page-script 阶段）。

    复用既有强制函数调用机制：把 ``board`` / ``elements`` / ``chunk`` 组装为上下
    文，强制模型以 ``PageScript`` 工具回传每页的 ``required_information`` /
    ``causal_links`` / ``source_spans``（span 偏移基于同一段 ``chunk`` 文本）。模型
    回传后，``span.text`` 由服务端反推为 ``chunk[start:end]``，保证与切片自洽
    （模型偏移略偏也不破坏一致性）。产物仅供原型审计，不是可读性/质量闸门。
    """
    chat = chat or get_chat_provider()
    context = (
        f"{sanitize_text(chunk)}\n\n"
        "Extracted story elements:\n"
        f"{elements.model_dump_json()}\n\n"
        f"Storyboard (chapter_id={board.chapter_id}):\n"
        f"{board.model_dump_json()}\n\n"
        "Instructions: group consecutive panels into layout pages (≈4 panels/page, "
        "in source order); emit one PageScriptPage per page with panel_ids; fill "
        "required_information / causal_links / source_spans quoted VERBATIM from the "
        "chunk text (char offsets into the chunk)."
    )
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": context},
    ]
    args = await chat.chat_function_call(
        messages, [PAGE_SCRIPT_TOOL], _tool_choice("plan_page_script")
    )
    ps = PageScript.model_validate(args)
    # 服务端反推 span.text，保证与 chunk 切片自洽（模型偏移略偏也不破坏一致性）。
    for page in ps.pages:
        for sp in page.source_spans:
            sp.text = chunk[sp.start : sp.end] if 0 <= sp.start <= sp.end <= len(chunk) else ""
    return ps
