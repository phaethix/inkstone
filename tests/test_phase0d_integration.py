"""Phase 0d: ledger integration with alias merge, pipeline, and the web snapshot."""

from core.comic.identity import merge_character_alias
from core.comic.ledger import ConsistencyLedger, LedgerEntry
from core.schemas import CharacterAsset, ProjectState


def test_alias_merge_lists_shared_pages_from_ledger():
    state = ProjectState(project_id="t")
    state.characters["鸿渐"] = CharacterAsset(name="鸿渐")
    state.characters["方鸿渐"] = CharacterAsset(name="方鸿渐")
    led = ConsistencyLedger(
        characters={
            "鸿渐": LedgerEntry(pages=["c0000-p0003"]),
            "方鸿渐": LedgerEntry(pages=["c0001-p0000"]),
        }
    )
    stale = merge_character_alias(state, "鸿渐", "方鸿渐", ledger=led)
    assert stale == []
    assert "c0000-p0003" in state.stale_pages
    assert "鸿渐" not in led.characters
    assert led.characters["方鸿渐"].pages == ["c0001-p0000", "c0000-p0003"]


def test_alias_merge_without_ledger_keeps_cache_walk():
    state = ProjectState(project_id="t")
    state.characters["a"] = CharacterAsset(name="a")
    state.characters["b"] = CharacterAsset(name="b")
    # No page_cache entry -> nothing to invalidate; must not raise.
    assert merge_character_alias(state, "a", "b") == []