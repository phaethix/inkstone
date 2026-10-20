# tests/test_state_split.py
"""Two-part ``state.json``: heavy planning caches live in the sibling ``cache.json``.

The checkpoint keeps the authoritative human state and the light progress
records; ``chunk_cache`` / ``page_cache`` / ``beat_cache`` / ``visual_bible``
travel in ``cache.json`` so the render hot path never rewrites them.
"""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

import core.schemas as schemas
from core.schemas import (
    CACHE_FILE_NAME,
    ChunkCache,
    ComicPagePlan,
    ComicPagePlanSet,
    KeyBeat,
    KeyBeatSet,
    ProjectState,
    VisualBible,
)

_CACHE_FIELDS = (
    "chunk_cache",
    "page_cache",
    "beat_cache",
    "visual_bible",
    "source_units",
    "adaptation_plans",
)


def _state_with_caches() -> ProjectState:
    state = ProjectState(project_id="p1")
    state.chunk_cache["0"] = ChunkCache(extract_action_key="extract-key")
    state.page_cache["0"] = ComicPagePlanSet(
        unit_id="c0000",
        pages=[ComicPagePlan(page_id="p1", purpose="open the scene")],
        action_key="plan-key",
    )
    state.beat_cache["0"] = KeyBeatSet(
        beats=[KeyBeat(beat_id="b1", summary="the turn")], action_key="beats-key"
    )
    state.visual_bible = VisualBible(version="bible_v1", style_guide="ink", content_hash="hash")
    return state


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_full_save_splits_the_files_and_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    state = _state_with_caches()
    state.pages_done.append("c0000-p0000")
    state.save(path)

    raw_state = _read_json(path)
    for name in _CACHE_FIELDS:
        assert name not in raw_state
    assert set(_read_json(tmp_path / CACHE_FILE_NAME)) == set(_CACHE_FIELDS)

    loaded = ProjectState.load(path)
    assert loaded.pages_done == ["c0000-p0000"]
    assert loaded.chunk_cache == state.chunk_cache
    assert loaded.page_cache == state.page_cache
    assert loaded.beat_cache == state.beat_cache
    assert loaded.visual_bible == state.visual_bible


def test_model_dump_still_contains_the_caches(tmp_path: Path) -> None:
    # The split lives in the file layer only; the model and its serialization
    # contract are unchanged.
    dumped = _state_with_caches().model_dump()
    for name in _CACHE_FIELDS:
        assert name in dumped


def test_full_save_writes_the_cache_before_the_checkpoint(tmp_path: Path, monkeypatch) -> None:
    calls: list[str] = []
    original = schemas._atomic_write_text

    def spy(path: Path, payload: str) -> None:
        calls.append(Path(path).name)
        original(path, payload)

    monkeypatch.setattr(schemas, "_atomic_write_text", spy)
    _state_with_caches().save(tmp_path / "state.json")
    assert calls == [CACHE_FILE_NAME, "state.json"]


def test_pre_split_state_carries_inline_caches_and_splits_on_save(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text(json.dumps(_state_with_caches().model_dump()), encoding="utf-8")
    assert not (tmp_path / CACHE_FILE_NAME).exists()

    loaded = ProjectState.load(path)
    assert set(loaded.chunk_cache) == {"0"}
    assert loaded.visual_bible is not None

    loaded.save(path)
    assert "chunk_cache" not in _read_json(path)
    again = ProjectState.load(path)
    assert again.chunk_cache == loaded.chunk_cache
    assert again.page_cache == loaded.page_cache
    assert again.beat_cache == loaded.beat_cache
    assert again.visual_bible == loaded.visual_bible


def test_projection_only_save_leaves_the_cache_file_byte_identical(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    state = _state_with_caches()
    state.save(path)
    cache_path = tmp_path / CACHE_FILE_NAME
    before = cache_path.read_bytes()

    state.pages_done.append("c0000-p0000")
    state.stage = "panels"
    state.save(path, caches=False)

    assert cache_path.read_bytes() == before
    loaded = ProjectState.load(path)
    assert loaded.pages_done == ["c0000-p0000"]
    assert loaded.stage == "panels"
    assert loaded.chunk_cache == state.chunk_cache


def test_cache_file_wins_when_both_files_carry_heavy_fields(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps({"project_id": "p1", "chunk_cache": {"inline": {"extract_action_key": "old"}}}),
        encoding="utf-8",
    )
    (tmp_path / CACHE_FILE_NAME).write_text(
        json.dumps({"chunk_cache": {"disk": {"extract_action_key": "new"}}}),
        encoding="utf-8",
    )

    loaded = ProjectState.load(path)
    assert list(loaded.chunk_cache) == ["disk"]
    assert loaded.chunk_cache["disk"].extract_action_key == "new"


def test_missing_cache_file_loads_with_empty_caches(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    ProjectState(project_id="p1").save(path, caches=False)
    assert not (tmp_path / CACHE_FILE_NAME).exists()

    loaded = ProjectState.load(path)
    assert loaded.chunk_cache == {}
    assert loaded.page_cache == {}
    assert loaded.beat_cache == {}
    assert loaded.visual_bible is None


def test_corrupt_cache_file_raises_by_default_and_is_ignored_for_projection_reads(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.json"
    _state_with_caches().save(path)
    (tmp_path / CACHE_FILE_NAME).write_text("{not json", encoding="utf-8")

    with pytest.raises(ValidationError):
        ProjectState.load(path)

    projection = ProjectState.load(path, caches=False)
    assert projection.chunk_cache == {}
    assert projection.visual_bible is None


def test_corrupt_state_json_still_raises_for_both_load_modes(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text("{not json", encoding="utf-8")
    for caches in (True, False):
        with pytest.raises(ValidationError):
            ProjectState.load(path, caches=caches)
