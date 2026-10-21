"""core.screenwriter — structured extraction and storyboard planning.

Turns raw novel text into the structured contracts (``StoryElements``,
``Storyboard``) the rest of the pipeline consumes, by driving a ChatProvider's
forced function calling. Also applies light content-safety hygiene: a policy
constraint is injected as the system prompt, and caller text can be sanitized
of banned terms before it is sent so the provider's filter is less likely to
reject the call.
"""

import json
import logging

import requests

from core.api import get_chat_provider
from core.comic.adaptation import assign_panel_beat_ids
from core.comic.lettering_lang import (
    lettering_field_mismatches,
    sanitize_plan_lettering,
    source_lettering_script,
    strip_mismatched_lettering,
)
from core.comic.runs import note_chat_attempt, note_chat_failure
from core.harness.request import ChatRequest
from core.harness.spec import Harness, genre_block, render_stage, system_prompt
from core.harness.tripwire import assert_no_quality_verdicts
from core.schemas import (
    ComicPagePlanSet,
    KeyBeatSet,
    PageScript,
    Storyboard,
    StoryElements,
    VisualBible,
    VisualBibleReconcileResult,
    to_tool_schema,
)

logger = logging.getLogger(__name__)


async def _audited_chat(chat, messages, tools, tool_choice):
    """Issue one forced call and audit it when a run has bound a scope."""
    system_text = "\n".join(
        str(message.get("content", "")) for message in messages if message.get("role") == "system"
    )
    assert_no_quality_verdicts(system_text=system_text, tools=tools)
    note_chat_attempt(json.dumps(messages, ensure_ascii=False))
    try:
        return await chat.chat_function_call(messages, tools, tool_choice)
    except Exception as exc:  # noqa: BLE001 — log the class, then keep the chat error
        note_chat_failure(exc)
        raise


# The invariant system prompt lives in ``core/harness/data/common/system.md``.
# It is read once at import; a prompt edit is a file diff, not a buried string.
SYSTEM_PROMPT = system_prompt()

EXTRACT_TOOL = to_tool_schema(
    StoryElements,
    "extract_story_elements",
    "Extract character and setting assets from a novel excerpt.",
)
STORYBOARD_TOOL = to_tool_schema(
    Storyboard,
    "plan_storyboard",
    "Plan the comic panels for one chunk of text as structured data. "
    "When key beats are provided, set beat_ids on the panels that stage them.",
)
PAGE_PLAN_TOOL = to_tool_schema(
    ComicPagePlanSet,
    "plan_comic_pages",
    "Plan finished comic pages for one text unit: per-page purpose, "
    "dynamic layout_intent, panel specs with source-language lettering, and "
    "lettering_boxes as normalized 0-1 page rectangles. "
    "When key beats are provided, set covers_beats on pages that stage them.",
)
KEY_BEATS_TOOL = to_tool_schema(
    KeyBeatSet,
    "extract_key_beats",
    "Extract dramatizable turning points (key beats) that must be shown as "
    "drawable comic scenes, not caption-only summaries. Cap at about 12 beats.",
)
RECONCILE_BIBLE_TOOL = to_tool_schema(
    VisualBibleReconcileResult,
    "reconcile_visual_bible",
    "Build or update the project visual bible: merge aliases, attach age stages, "
    "lock style_guide and color palette, emit CharacterCanon entries.",
)

# Optional local scrub list. Empty by default: content policy is enforced by the
# system prompt + upstream provider filters. Callers may pass a non-empty
# ``banned`` list (or replace this tuple) when project policy needs local redaction.
DEFAULT_BANNED_TERMS: tuple[str, ...] = ()


def _tool_choice(name: str) -> dict:
    return {"type": "function", "function": {"name": name}}


# Each builder is the single place a stage's messages, tools, and forced tool
# choice are assembled. The pipeline builds the request once, sends it, and
# uses ``request.digest()`` as the stage's cache identity — so a prompt or
# schema edit can never hit a stale plan.
def build_extract_request(
    text: str,
    *,
    harness: Harness | None = None,
    model: str = "",
) -> ChatRequest:
    """The extract request for one chunk."""
    values = {"source_chunk": sanitize_text(text)}
    user = (
        harness.render_stage("extract", values)
        if harness is not None
        else render_stage("extract", values)
    )
    system = harness.system_prompt() if harness is not None else SYSTEM_PROMPT
    return ChatRequest(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        tools=[EXTRACT_TOOL],
        tool_choice=_tool_choice("extract_story_elements"),
    )


def build_beats_request(
    text: str,
    elements: StoryElements,
    *,
    harness: Harness | None = None,
    model: str = "",
) -> ChatRequest:
    """The key-beats request for one text unit."""
    values = {
        "source_chunk": sanitize_text(text),
        "elements_json": elements.model_dump_json(),
    }
    user = (
        harness.render_stage("beats", values)
        if harness is not None
        else render_stage("beats", values)
    )
    system = harness.system_prompt() if harness is not None else SYSTEM_PROMPT
    return ChatRequest(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        tools=[KEY_BEATS_TOOL],
        tool_choice=_tool_choice("extract_key_beats"),
    )


def build_storyboard_request(
    text: str,
    elements: StoryElements,
    *,
    key_beats: KeyBeatSet | None = None,
    panel_budget: int | None = None,
    enforce_budget: bool = False,
    tier: str | None = None,
    genre: str | None = None,
    harness: Harness | None = None,
    model: str = "",
) -> ChatRequest:
    """The storyboard request for one chunk.

    ``tier`` reaches the budget sentence so B and C ask for different things;
    without it the two tiers send the same words and differ only by a number.
    """
    from core.config import genre as config_genre

    name = genre if genre is not None else config_genre()
    domain = harness.genre_block(name) if harness is not None else genre_block(name)
    system = harness.system_prompt() if harness is not None else SYSTEM_PROMPT
    beats_blob = ""
    if key_beats is not None and key_beats.beats:
        beats_blob = (
            "Key beats to stage (set beat_ids on the panels that show them):\n"
            f"{key_beats.model_dump_json()}\n\n"
        )
    values = {
        "source_chunk": sanitize_text(text),
        "elements_json": elements.model_dump_json(),
        "beats_blob": beats_blob,
        "budget_sentence": _budget_sentence(panel_budget, enforce=enforce_budget, tier=tier),
        "domain": domain,
    }
    context = (
        harness.render_stage("storyboard", values)
        if harness is not None
        else render_stage("storyboard", values)
    )
    return ChatRequest(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": context},
        ],
        tools=[STORYBOARD_TOOL],
        tool_choice=_tool_choice("plan_storyboard"),
    )


def build_page_plan_request(
    text: str,
    elements: StoryElements,
    *,
    recent_layouts: list[str] | None = None,
    visual_bible=None,
    key_beats: KeyBeatSet | None = None,
    extra_user_note: str = "",
    panel_budget: int | None = None,
    enforce_budget: bool = False,
    tier: str | None = None,
    genre: str | None = None,
    craft_notes: str = "",
    harness: Harness | None = None,
    model: str = "",
) -> ChatRequest:
    """The finished-page plan request for one chunk.

    ``harness`` selects a candidate tree for an A/B probe. ``None`` means the
    builtin tree, preserving the normal pipeline and its module-level
    ``SYSTEM_PROMPT`` override used by tests.
    """
    from core.comic.layout_diversity import layout_diversity_instructions
    from core.comic.voice import voice_timeline_plan_instructions
    from core.config import genre as config_genre

    name = genre if genre is not None else config_genre()
    domain = harness.genre_block(name) if harness is not None else genre_block(name)
    system = harness.system_prompt() if harness is not None else SYSTEM_PROMPT

    lang_reminder = (
        "Reminder: caption / dialogue / sfx must match the source language "
        "(if the excerpt is Chinese, lettering must be Chinese — never English translation). "
        "Do NOT add pinyin, romanization, or latin glosses in parentheses. "
        "Keep each caption/dialogue short (one breath). "
        "Place lettering_boxes near panel edges — never covering faces; keep boxes fully "
        "inside the page (0.05–0.95). "
        "Also emit lettering_boxes: normalized 0-1 page rectangles "
        "(kind, panel_id, x, y, w, h) for every non-null lettering field."
    )
    diversity = layout_diversity_instructions(recent_layouts)
    voice = voice_timeline_plan_instructions(visual_bible)
    beats_blob = ""
    if key_beats is not None and key_beats.beats:
        beats_blob = (
            "Key beats to cover (set covers_beats on staging pages):\n"
            f"{key_beats.model_dump_json()}\n\n"
        )
    note = f"\n\n{extra_user_note}" if extra_user_note else ""
    craft_block = f"{craft_notes.strip()}\n\n" if craft_notes.strip() else ""
    values = {
        "source_chunk": sanitize_text(text),
        "elements_json": elements.model_dump_json(),
        "beats_blob": beats_blob,
        "budget_sentence": _budget_sentence(panel_budget, enforce=enforce_budget, tier=tier),
        "diversity": diversity,
        "voice": voice,
        "lang_reminder": lang_reminder,
        "note": note,
        "domain": domain,
        "craft_notes": craft_block,
    }
    user = (
        harness.render_stage("page_plan", values)
        if harness is not None
        else render_stage("page_plan", values)
    )
    return ChatRequest(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        tools=[PAGE_PLAN_TOOL],
        tool_choice=_tool_choice("plan_comic_pages"),
    )


def build_bible_request(
    text: str,
    state_characters: dict,
    bible: VisualBible | None,
    *,
    alias_hints: list[tuple[str, str, str]] = (),
    preferred_style: str = "",
    harness: Harness | None = None,
    model: str = "",
) -> ChatRequest:
    """The visual-bible reconcile request for one chunk."""
    system = harness.system_prompt() if harness is not None else SYSTEM_PROMPT
    char_blob = json.dumps(
        {k: v.model_dump() for k, v in state_characters.items()},
        ensure_ascii=False,
    )
    hints_blob = (
        "\n".join(f"- {alias} → {canonical} ({reason})" for alias, canonical, reason in alias_hints)
        or "(none)"
    )
    if bible is None:
        style_hint = (
            f" Use preferred_style={preferred_style!r} for style_guide when non-empty."
            if preferred_style
            else ""
        )
        bible_note = (
            "No visual bible yet. Fill style_guide, color (4–6 palette swatches), "
            f"and full canons for every canonical character.{style_hint}"
        )
        bible_blob = ""
    else:
        bible_note = (
            "Existing visual bible below. Keep the existing color palette unless "
            "color_patches are clearly justified. Still return merges/stages/keeps "
            "for any new names."
        )
        bible_blob = bible.model_dump_json()
    current_bible_block = f"Current visual bible:\n{bible_blob}\n\n" if bible_blob else ""
    values = {
        "source_chunk": sanitize_text(text),
        "characters_json": char_blob,
        "current_bible_block": current_bible_block,
        "bible_note": bible_note,
        "alias_hints": hints_blob,
    }
    user = (
        harness.render_stage("bible", values)
        if harness is not None
        else render_stage("bible", values)
    )
    return ChatRequest(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        tools=[RECONCILE_BIBLE_TOOL],
        tool_choice=_tool_choice("reconcile_visual_bible"),
    )


def build_page_script_request(
    board: Storyboard,
    elements: StoryElements,
    chunk: str,
    *,
    harness: Harness | None = None,
    model: str = "",
) -> ChatRequest:
    """The optional legacy PageScript request for one chunk."""
    values = {
        "source_chunk": sanitize_text(chunk),
        "elements_json": elements.model_dump_json(),
        "chapter_id": board.chapter_id,
        "board_json": board.model_dump_json(),
    }
    context = (
        harness.render_stage("page_script", values)
        if harness is not None
        else render_stage("page_script", values)
    )
    system = harness.system_prompt() if harness is not None else SYSTEM_PROMPT
    return ChatRequest(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": context},
        ],
        tools=[PAGE_SCRIPT_TOOL],
        tool_choice=_tool_choice("plan_page_script"),
    )


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


async def extract_story_elements(
    text: str,
    *,
    chat=None,
    request: ChatRequest | None = None,
) -> StoryElements:
    """Extract characters / settings / style from ``text`` via forced call."""
    chat = chat or get_chat_provider()
    request = request or build_extract_request(text, model=getattr(chat, "model", ""))
    args = await _audited_chat(chat, request.messages, request.tools, request.tool_choice)
    return StoryElements.model_validate(args)


def _budget_sentence(panel_budget: int | None, *, enforce: bool, tier: str | None = None) -> str:
    """The panel budget sentence, worded to match what the tier promises.

    ``enforce`` decides whether the number is a ceiling. A clips the plan, so
    its sentence can forbid going over and mean it. B and C keep what the
    planner wrote, so "do not exceed" there would state a rule nothing applies
    and teach the planner that its instructions are advisory.

    ``tier`` is what makes B and C say different things. Both are uncapped, so
    before this the two sentences differed only by ``8`` versus ``14``, and a
    planner had no reason to treat the excerpts differently: it could return the
    same panel list for both and satisfy each sentence. B promises 章级完整, so
    it asks for the chapter's beats. C promises 近原著, so it also asks for the
    connective tissue between those beats — the transitions, reactions, and
    beats of business a summary drops. ``None`` keeps the tier-neutral wording,
    for callers that have no tier (a raw reproduction or a unit test).

    ``enforce`` comes from :func:`core.density.clips_plan` and the tier from
    :func:`core.density.get_density_plan`, so the sentence and the pipeline
    cannot disagree about what the tier will do.
    """
    if panel_budget is None:
        return ""
    if enforce:
        head = f"Panel budget for this excerpt: about {panel_budget} panels. "
        return (
            head + "This is a main-thread overview: keep the beats that carry the "
            "main thread and the turn, and do not exceed this budget. "
        )
    head = f"Panel budget for this excerpt: about {panel_budget} panels. "
    if tier == "C":
        return (
            head + "This is a near-original adaptation: cover every beat the excerpt "
            "actually stages, including the connective tissue between them — the "
            "transitions, reactions, and beats of business that a summary would "
            "drop. Use fewer only when the excerpt is genuinely thin; this number "
            "is a target, not a cap. "
        )
    if tier == "B":
        return (
            head + "This is a chapter-complete plan: cover the chapter's beats and let "
            "them decide when it needs more; this number is a target, not a cap. "
        )
    # No tier: say only what is true for every uncapped tier rather than claiming
    # to be the chapter-complete one.
    return (
        head + "Use fewer when the excerpt is thin, and let the chapter's beats decide "
        "when it needs more; this number is a target, not a cap. "
    )


def _evenly_spaced(count: int, budget: int) -> list[int]:
    """Indices of ``count`` items to keep when only ``budget`` may survive.

    The first and last are always kept: a chapter without its opening or its
    close is a different chapter, not a sparser one. The rest are spread across
    the middle so every stretch of the chapter is represented rather than the
    front half alone.

    A budget of one cannot keep both ends. The close is what survives, because
    a missing ending is the failure this spread exists to prevent.
    """
    if budget <= 0 or count <= 0:
        return []
    if budget >= count:
        return list(range(count))
    if budget == 1:
        return [count - 1]
    step = (count - 1) / (budget - 1)
    picked = [int(round(index * step)) for index in range(budget)]
    # Rounding can collide on short chapters; walk forward to keep the order and
    # the count exact.
    deduped: list[int] = []
    for index in picked:
        index = min(index, count - 1)
        while index in deduped and index < count - 1:
            index += 1
        if index not in deduped:
            deduped.append(index)
    while len(deduped) < budget:
        for index in range(count):
            if index not in deduped:
                deduped.append(index)
                break
        else:
            break
    return sorted(deduped)


def _clip_page_plan(
    pageset: ComicPagePlanSet,
    panel_budget: int | None,
    *,
    enforce: bool,
) -> ComicPagePlanSet:
    """Thin the plan to ``panel_budget`` panels without losing either end.

    Only tier A clips, and it is the one tier whose budget is a ceiling: the
    overview it promises is the plan thinned to the main thread. B and C keep
    what the planner wrote, and ``enforce`` is False for them, so this returns
    the plan untouched.

    Dropping the extra panels from the front kept the setup and threw away the
    turn and the close, so an overview tier delivered the opening of a story
    with no ending. The panels are spread instead, and the first and last always
    survive.
    """
    if panel_budget is None or not enforce:
        return pageset
    total = sum(len(page.panels) for page in pageset.pages)
    if total <= panel_budget:
        return pageset
    keep = set(_evenly_spaced(total, panel_budget))
    kept = []
    seen = 0
    for page in pageset.pages:
        panels = []
        for panel in page.panels:
            if seen in keep:
                panels.append(panel)
            seen += 1
        if not panels:
            continue
        ids = {panel.panel_id for panel in panels}
        boxes = [box for box in page.lettering_boxes if box.panel_id in ids]
        kept.append(page.model_copy(update={"panels": panels, "lettering_boxes": boxes}))
    if not kept:
        return pageset
    return pageset.model_copy(update={"pages": kept})


async def plan_storyboard(
    text: str,
    elements: StoryElements,
    *,
    chat=None,
    panel_budget: int | None = None,
    enforce_budget: bool = False,
    tier: str | None = None,
    key_beats: KeyBeatSet | None = None,
    request: ChatRequest | None = None,
) -> Storyboard:
    """Plan the panels for ``text`` given already-extracted ``elements``."""
    chat = chat or get_chat_provider()
    request = request or build_storyboard_request(
        text,
        elements,
        key_beats=key_beats,
        panel_budget=panel_budget,
        enforce_budget=enforce_budget,
        tier=tier,
        model=getattr(chat, "model", ""),
    )
    args = await _audited_chat(chat, request.messages, request.tools, request.tool_choice)
    board = Storyboard.model_validate(args)
    if panel_budget is not None and enforce_budget:
        # Spread the kept panels instead of taking the front: clipping by
        # position kept the opening and dropped the turn and the close.
        keep = set(_evenly_spaced(len(board.panels), panel_budget))
        board = board.model_copy(
            update={"panels": [p for i, p in enumerate(board.panels) if i in keep]}
        )
    elif panel_budget is not None and len(board.panels) > panel_budget:
        # Tier B keeps whatever came back on purpose, but the size of the overrun
        # is the one number that says the budget is un-calibrated. Log it.
        logger.warning(
            "storyboard planned %d panels against a budget of %d; keeping them",
            len(board.panels),
            panel_budget,
        )
    if key_beats is not None and key_beats.beats:
        assign_panel_beat_ids(board.panels, key_beats.beats)
    else:
        for panel in board.panels:
            panel.beat_ids = []
    return board


async def extract_key_beats(
    text: str,
    elements: StoryElements,
    *,
    chat=None,
    request: ChatRequest | None = None,
) -> KeyBeatSet:
    """Extract must-draw dramatizable beats for a text unit."""
    chat = chat or get_chat_provider()
    request = request or build_beats_request(text, elements, model=getattr(chat, "model", ""))
    args = await _audited_chat(chat, request.messages, request.tools, request.tool_choice)
    return KeyBeatSet.model_validate(args)


async def plan_comic_pages(
    text: str,
    elements: StoryElements,
    *,
    chat=None,
    recent_layouts: list[str] | None = None,
    visual_bible=None,
    key_beats: KeyBeatSet | None = None,
    extra_user_note: str = "",
    panel_budget: int | None = None,
    enforce_budget: bool = False,
    tier: str | None = None,
    request: ChatRequest | None = None,
) -> ComicPagePlanSet:
    """Plan finished readable pages for ``text`` given ``elements``."""
    chat = chat or get_chat_provider()
    script = source_lettering_script(text)
    request = request or build_page_plan_request(
        text,
        elements,
        recent_layouts=recent_layouts,
        visual_bible=visual_bible,
        key_beats=key_beats,
        extra_user_note=extra_user_note,
        panel_budget=panel_budget,
        enforce_budget=enforce_budget,
        tier=tier,
        model=getattr(chat, "model", ""),
    )
    args = await _audited_chat(chat, request.messages, request.tools, request.tool_choice)
    pageset = ComicPagePlanSet.model_validate(args)

    def _any_mismatch(plan_set: ComicPagePlanSet) -> bool:
        return any(lettering_field_mismatches(page, script) for page in plan_set.pages)

    if script in ("cjk", "latin") and _any_mismatch(pageset):
        logger.warning(
            "plan_comic_pages language mismatch; retrying once (script=%s)",
            script,
        )
        retry = request.with_user_suffix(
            "\n\nCRITICAL: previous plan mixed languages. Fix lettering now."
        )
        args = await _audited_chat(chat, retry.messages, retry.tools, retry.tool_choice)
        pageset = ComicPagePlanSet.model_validate(args)

    if script in ("cjk", "latin"):
        pageset = pageset.model_copy(
            update={
                "pages": [
                    sanitize_plan_lettering(strip_mismatched_lettering(page, script), script)
                    for page in pageset.pages
                ],
            }
        )
    else:
        pageset = pageset.model_copy(
            update={
                "pages": [sanitize_plan_lettering(page, "cjk") for page in pageset.pages],
            }
        )
    kept = _clip_page_plan(pageset, panel_budget, enforce=enforce_budget)
    if panel_budget is not None and not enforce_budget:
        planned = sum(len(page.panels) for page in kept.pages)
        if planned > panel_budget:
            logger.warning(
                "page plan kept %d panels against a budget of %d; keeping them",
                planned,
                panel_budget,
            )
    return kept


async def reconcile_visual_bible(
    text: str,
    state_characters: dict,
    bible: VisualBible | None,
    *,
    alias_hints: list[tuple[str, str, str]] = (),
    preferred_style: str = "",
    chat=None,
    request: ChatRequest | None = None,
) -> VisualBibleReconcileResult:
    """Build or update the project visual bible from chunk text and known characters."""
    chat = chat or get_chat_provider()
    request = request or build_bible_request(
        text,
        state_characters,
        bible,
        alias_hints=alias_hints,
        preferred_style=preferred_style,
        model=getattr(chat, "model", ""),
    )
    args = await _audited_chat(chat, request.messages, request.tools, request.tool_choice)
    return VisualBibleReconcileResult.model_validate(args)


PAGE_SCRIPT_TOOL = to_tool_schema(
    PageScript,
    "plan_page_script",
    "Fill optional per-page audit fields for one chunk's storyboard "
    "(required information, causal links, source spans). Not a quality gate.",
)


async def plan_page_script(
    board: Storyboard,
    elements: StoryElements,
    chunk: str,
    *,
    chat=None,
    request: ChatRequest | None = None,
) -> PageScript:
    """为 storyboard 后的单个 chunk 生成可选遗留 PageScript 审计元数据（page-script 阶段）。

    复用既有强制函数调用机制：把 ``board`` / ``elements`` / ``chunk`` 组装为上下
    文，强制模型以 ``PageScript`` 工具回传每页的 ``required_information`` /
    ``causal_links`` / ``source_spans``（span 偏移基于同一段 ``chunk`` 文本）。模型
    回传后，``span.text`` 由服务端反推为 ``chunk[start:end]``，保证与切片自洽
    （模型偏移略偏也不破坏一致性）。产物仅供原型审计，不是可读性/质量闸门。
    """
    chat = chat or get_chat_provider()
    request = request or build_page_script_request(
        board, elements, chunk, model=getattr(chat, "model", "")
    )
    args = await _audited_chat(chat, request.messages, request.tools, request.tool_choice)
    ps = PageScript.model_validate(args)
    # 服务端反推 span.text，保证与 chunk 切片自洽（模型偏移略偏也不破坏一致性）。
    for page in ps.pages:
        for sp in page.source_spans:
            sp.text = chunk[sp.start : sp.end] if 0 <= sp.start <= sp.end <= len(chunk) else ""
    return ps
