"""Phase 0d: the consistency ledger schema and persistence (§12)."""

from pathlib import Path

from core.comic.ledger import ConsistencyLedger, LedgerEntry, ReferenceVersion
from core.schemas import ComicPagePlan, ComicPagePlanSet, PagePanelSpec, ProjectState


def test_empty_ledger_round_trips(tmp_path: Path):
    led = ConsistencyLedger()
    path = tmp_path / "consistency.json"
    led.save(path)
    loaded = ConsistencyLedger.load(path)
    assert loaded == led
    assert loaded.schema_version == 1


def test_entry_round_trips(tmp_path: Path):
    led = ConsistencyLedger(
        characters={
            "方鸿渐": LedgerEntry(
                pages=["c0000-p0002", "c0001-p0005"],
                reference=ReferenceVersion(
                    path="assets/portraits/方鸿渐.png", content_hash=None, version=1
                ),
                pending_pages=["c0001-p0005"],
            )
        }
    )
    path = tmp_path / "consistency.json"
    led.save(path)
    loaded = ConsistencyLedger.load(path)
    assert loaded == led
    assert loaded.characters["方鸿渐"].reference.version == 1
    assert loaded.characters["方鸿渐"].reference.content_hash is None


def test_load_missing_file_returns_empty(tmp_path: Path):
    loaded = ConsistencyLedger.load(tmp_path / "absent.json")
    assert loaded.characters == {}


def _plan(names: list[str], page_id: str = "p") -> ComicPagePlan:
    return ComicPagePlan(
        page_id=page_id,
        reference_characters=list(names),
        panels=[PagePanelSpec(panel_id="pan1", characters=list(names))],
    )


def _state_with(chunks: dict[str, list[list[str]]]) -> ProjectState:
    state = ProjectState(project_id="t")
    for chunk_key, pages in chunks.items():
        state.page_cache[chunk_key] = ComicPagePlanSet(
            unit_id=chunk_key,
            pages=[_plan(names, page_id=f"pg{i}") for i, names in enumerate(pages)],
        )
    return state


def test_rebuild_derives_positional_page_sets():
    state = _state_with({"0": [["A", "B"], ["A"]], "1": [["B"]]})
    led = ConsistencyLedger()
    assert led.rebuild_from_state(state) is True
    assert led.characters["A"].pages == ["c0000-p0000", "c0000-p0001"]
    assert led.characters["B"].pages == ["c0000-p0000", "c0001-p0000"]


def test_rebuild_is_idempotent_and_returns_false_on_no_change():
    state = _state_with({"0": [["A"]]})
    led = ConsistencyLedger()
    assert led.rebuild_from_state(state) is True
    assert led.rebuild_from_state(state) is False


def test_rebuild_strips_stage_suffix_from_names():
    state = _state_with({"0": [["方鸿渐@adult"]]})
    led = ConsistencyLedger()
    led.rebuild_from_state(state)
    assert "方鸿渐" in led.characters
    assert "方鸿渐@adult" not in led.characters


def test_rebuild_refreshes_pending_only_above_reviewed_version():
    state = _state_with({"0": [["A"]]})
    led = ConsistencyLedger(characters={"A": LedgerEntry(pages=["c0000-p0000"])})
    led.characters["A"].reference = ReferenceVersion(path="p.png", version=2)
    led.characters["A"].reviewed_version = 1
    led.rebuild_from_state(state)
    assert led.characters["A"].pending_pages == ["c0000-p0000"]
    # A human reviewed version 2 -> pending clears without touching pages.
    led.characters["A"].reviewed_version = 2
    led.rebuild_from_state(state)
    assert led.characters["A"].pending_pages == []


def test_load_or_rebuild_recovers_from_corruption(tmp_path: Path):
    path = tmp_path / "consistency.json"
    path.write_text("{ this is not json", encoding="utf-8")
    state = _state_with({"0": [["A"]]})
    led = ConsistencyLedger.load_or_rebuild(path, state)
    assert led.characters["A"].pages == ["c0000-p0000"]


def test_load_or_rebuild_prefers_a_valid_file(tmp_path: Path):
    path = tmp_path / "consistency.json"
    ConsistencyLedger(characters={"A": LedgerEntry(pages=["c9999-p9999"])}).save(path)
    state = _state_with({"0": [["A"]]})
    led = ConsistencyLedger.load_or_rebuild(path, state)
    # A valid file is authoritative: it is not overwritten by a rebuild.
    assert led.characters["A"].pages == ["c9999-p9999"]