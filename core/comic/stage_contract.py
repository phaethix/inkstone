"""core.comic.stage_contract — what each stage may read (§5, phase 1).

A content key, when it exists, may contain only what a stage declares. Three
classes:

- Hard inputs change the bytes and belong in the key.
- Soft references (the L2 continuity image) do not. Only the selection policy
  would.
- A historical dependency is accumulated from other items. It is declared, then
  bounded. The bound is a window size plus the summarizer's source hash, never
  the raw collection and never the summary strings. Putting those strings in a
  key would re-plan every later chunk when one early layout changed.

Same key does not mean the provider will return the same pixels. Image
generation is stochastic. A hit means the stored bytes are accepted in place
of spending another call.

Render-only knobs ``{page_size, panel_continuity, l3_enabled}`` change pixels,
not the chat caches. They are absent from every chat stage's hard inputs.
``render_mode`` is deliberately not in that set: it selects a different plan.
"""

from __future__ import annotations

import hashlib
import inspect
from dataclasses import dataclass

RECENT_LAYOUT_LIMIT = 8
RENDER_ONLY_PARAMS = frozenset({"page_size", "panel_continuity", "l3_enabled"})


@dataclass(frozen=True)
class StageContract:
    """The inputs one stage is allowed to depend on."""

    stage: str
    reads_accumulated: bool
    hard_inputs: frozenset[str]
    soft_refs: frozenset[str] = frozenset()
    historical_limit: int | None = None

    def __post_init__(self) -> None:
        if self.reads_accumulated and self.historical_limit is None:
            raise ValueError(f"{self.stage} reads accumulated state but declares no bound")
        if not self.reads_accumulated and self.historical_limit is not None:
            raise ValueError(f"{self.stage} declares a bound but reads no accumulated state")
        if self.hard_inputs & self.soft_refs:
            raise ValueError(f"{self.stage} lists an input as both hard and soft")
        if not self.stage.startswith("render") and self.hard_inputs & RENDER_ONLY_PARAMS:
            raise ValueError(f"{self.stage} takes a render-only param as a hard input")


def _chat(stage: str, *hard: str) -> StageContract:
    return StageContract(stage=stage, reads_accumulated=False, hard_inputs=frozenset(hard))


_STAGES: dict[str, StageContract] = {
    "extract": _chat("extract", "source_chunk"),
    # The current bible is the one summary of earlier chunks. Prior chunk text
    # is not an input. The character table is this project's identities.
    "bible": StageContract(
        stage="bible",
        reads_accumulated=True,
        hard_inputs=frozenset({"source_chunk", "character_table", "bible_summary"}),
        historical_limit=1,
    ),
    "beats": _chat("beats", "source_chunk"),
    "storyboard": _chat("storyboard", "source_chunk", "bible_entries_for_chunk"),
    "page_plan": StageContract(
        stage="page_plan",
        reads_accumulated=True,
        hard_inputs=frozenset({"source_chunk", "beats", "bible_entries_for_page"}),
        historical_limit=RECENT_LAYOUT_LIMIT,
    ),
    "render.page": StageContract(
        stage="render.page",
        reads_accumulated=False,
        hard_inputs=frozenset({"page_plan", "bible_entries_for_page", "prompt_renderer"}),
        soft_refs=frozenset({"l2_reference"}),
    ),
    # One character. The earlier portrait of the same person is an i2i reference,
    # the same class as a page's L2 image: it changes pixels without being a key.
    "portrait": StageContract(
        stage="portrait",
        reads_accumulated=False,
        hard_inputs=frozenset({"bible_entry_for_character", "character_asset"}),
        soft_refs=frozenset({"canonical_portrait_ref"}),
    ),
    "page_script": _chat("page_script", "source_chunk", "storyboard", "extracted_elements"),
    "render.panel": StageContract(
        stage="render.panel",
        reads_accumulated=False,
        hard_inputs=frozenset({"storyboard_panel", "bible_entries_for_panel", "prompt_renderer"}),
        soft_refs=frozenset({"prev_panel", "portrait_ref"}),
    ),
    # Font bytes belong to h_env. The path string is not an input.
    "letter": StageContract(
        stage="letter",
        reads_accumulated=False,
        hard_inputs=frozenset({"blank_page", "page_plan", "source_text"}),
    ),
    # The sheets already contain lettering. Binding is layout, direction, and,
    # for a webtoon, the text drawn while stacking plus the strip width.
    "export": StageContract(
        stage="export",
        reads_accumulated=False,
        hard_inputs=frozenset({"bound_images", "binding"}),
    ),
    # One collage of the panels handed to this call. Earlier pages are not an input.
    "layout": StageContract(
        stage="layout",
        reads_accumulated=False,
        hard_inputs=frozenset({"panel_images", "drawn_text", "page_geometry"}),
    ),
}


def stage_contract(stage: str) -> StageContract:
    """Return the declared contract. An unknown stage is a registration miss."""
    try:
        return _STAGES[stage]
    except KeyError as exc:
        raise KeyError(f"no stage contract for {stage}") from exc


def _source_hash(fn) -> str:
    return hashlib.sha256(inspect.getsource(fn).encode("utf-8")).hexdigest()


def historical_identity(stage: str) -> dict:
    """The key-facing form of a historical dependency.

    Limit and summarizer source hashes only. The function takes no project
    state, so the recent intent strings and the bible body cannot enter it.
    """
    contract = stage_contract(stage)
    if contract.historical_limit is None:
        return {}
    if stage == "page_plan":
        return {"recent_layouts": _layout_window(contract.historical_limit)}
    if stage == "bible":
        return {"bible_summary": _bible_summary(contract.historical_limit)}
    raise KeyError(f"no historical identity for {stage}")


def _layout_window(limit: int) -> dict:
    # Imported lazily: creative_comic imports this module for the limit.
    from core.comic.layout_diversity import layout_diversity_instructions
    from core.pipelines.creative_comic import _recent_layout_intents

    return {
        "limit": limit,
        "summarizers": {
            "_recent_layout_intents": _source_hash(_recent_layout_intents),
            "layout_diversity_instructions": _source_hash(layout_diversity_instructions),
        },
    }


def _bible_summary(limit: int) -> dict:
    from core.comic.visual_bible import apply_reconcile
    from core.screenwriter import reconcile_visual_bible

    return {
        "limit": limit,
        "summarizers": {
            "reconcile_visual_bible": _source_hash(reconcile_visual_bible),
            "apply_reconcile": _source_hash(apply_reconcile),
        },
    }
