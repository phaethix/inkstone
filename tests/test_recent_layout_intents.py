"""Regression: the recent-layout window must order chunks numerically.

``state.page_cache`` is keyed by ``str(chunk_index)``, so plain ``sorted()`` is
lexicographic and ranks ``"10"`` before ``"2"``. Past ten chunks the "last 8
layout intents" window therefore reads the wrong chunks, which silently widens
the anti-templating window to the whole book (design §9 phase 0e).
"""

from core.pipelines.creative_comic import _recent_layout_intents
from core.schemas import ComicPagePlan, ComicPagePlanSet, ProjectState


def _state_with_intents(chunk_indexes):
    """Build a ProjectState whose page_cache holds one intent per chunk."""
    state = ProjectState(project_id="p")
    for ci in chunk_indexes:
        state.page_cache[str(ci)] = ComicPagePlanSet(
            unit_id=f"u{ci}",
            pages=[ComicPagePlan(page_id=f"p{ci}", layout_intent=f"intent-{ci}")],
        )
    return state


def test_recent_layout_intents_orders_chunks_numerically():
    state = _state_with_intents([0, 1, 2, 9, 10, 11])

    assert _recent_layout_intents(state, limit=3) == [
        "intent-9",
        "intent-10",
        "intent-11",
    ]


def test_recent_layout_intents_keeps_non_numeric_keys_last():
    state = _state_with_intents([0, 1, 2, 10])
    state.page_cache["not-a-chunk"] = ComicPagePlanSet(
        unit_id="ux",
        pages=[ComicPagePlan(page_id="px", layout_intent="intent-weird")],
    )

    assert _recent_layout_intents(state, limit=2) == ["intent-10", "intent-weird"]
