import asyncio
from unittest.mock import patch

from core.config import ImageConfig
from core.pipelines.creative_comic import (
    _input_fingerprint,
    _legacy_combined_fingerprint,
    _render_fingerprint,
    _soft_invalidate_render,
    _structure_fingerprint,
    creative_comic,
)
from core.schemas import CharacterAsset, ChunkCache, ModelSnapshot, ProjectState
from tests.test_creative_comic import FakeChat, FakeImage, _fake_export_pdf

_SNAPSHOT = ModelSnapshot(chat="chat|url|m", t2i="t2i|url|m", i2i="i2i|url|m")


def test_structure_fingerprint_stable_for_same_source():
    source = "chapter one\npanel two"
    assert _structure_fingerprint(source) == _structure_fingerprint(source)


def test_structure_fingerprint_normalizes_line_endings():
    assert _structure_fingerprint("a\r\nb") == _structure_fingerprint("a\nb")


def test_structure_fingerprint_changes_when_source_changes():
    assert _structure_fingerprint("alpha") != _structure_fingerprint("beta")


def test_style_change_same_structure_different_render():
    source = "same source text"
    struct_a = _structure_fingerprint(source)
    struct_b = _structure_fingerprint(source)
    render_a = _render_fingerprint(
        "watercolor",
        snapshot=_SNAPSHOT,
        panel_continuity=True,
        l3_enabled=False,
    )
    render_b = _render_fingerprint(
        "ink sketch",
        snapshot=_SNAPSHOT,
        panel_continuity=True,
        l3_enabled=False,
    )
    assert struct_a == struct_b
    assert render_a != render_b


def test_render_fingerprint_none_style_matches_empty_string():
    with_empty = _render_fingerprint(
        "",
        snapshot=_SNAPSHOT,
        panel_continuity=False,
        l3_enabled=False,
    )
    with_none = _render_fingerprint(
        None,
        snapshot=_SNAPSHOT,
        panel_continuity=False,
        l3_enabled=False,
    )
    assert with_empty == with_none


def test_legacy_combined_fingerprint_aliases_input_fingerprint():
    kwargs = {
        "source_txt": "story",
        "style_guide": "manga",
        "snapshot": _SNAPSHOT,
        "panel_continuity": True,
        "l3_enabled": False,
    }
    assert _input_fingerprint(**kwargs) == _legacy_combined_fingerprint(**kwargs)


def test_project_state_roundtrip_dual_fingerprints(tmp_path):
    state = ProjectState(
        project_id="p1",
        source_fingerprint="legacy",
        structure_fingerprint="struct",
        render_fingerprint="render",
    )
    path = tmp_path / "state.json"
    state.save(path)
    loaded = ProjectState.load(path)
    assert loaded.structure_fingerprint == "struct"
    assert loaded.render_fingerprint == "render"
    assert loaded.source_fingerprint == "legacy"


def test_project_state_missing_dual_fields_loads():
    raw = '{"project_id":"p1","source_fingerprint":"only-old"}'
    loaded = ProjectState.model_validate_json(raw)
    assert loaded.structure_fingerprint == ""
    assert loaded.render_fingerprint == ""
    assert loaded.source_fingerprint == "only-old"


def test_soft_invalidate_render_clears_panels_keeps_chunk_cache():
    state = ProjectState(
        project_id="p1",
        panels_done=["c0000-p0000"],
        stale_panels=["c0000-p0001"],
        skipped=["c0000-p0002"],
        chunks_done=["0"],
        chunk_cache={"0": ChunkCache()},
        characters={"方鸿渐": CharacterAsset(name="方鸿渐", portrait_local="/tmp/portrait.png")},
    )
    from core.schemas import GeneratedPanel

    state.generated.panels["c0000-p0000"] = GeneratedPanel(
        local="/tmp/panel.png", source_panel_id="ch01_p01"
    )
    state.generated.portraits["方鸿渐"] = "/tmp/portrait.png"
    _soft_invalidate_render(state)
    assert state.panels_done == []
    assert state.stale_panels == []
    assert state.skipped == []
    assert state.generated.panels == {}
    assert state.generated.portraits == {}
    assert state.characters["方鸿渐"].portrait_local is None
    assert state.chunks_done == ["0"]
    assert "0" in state.chunk_cache


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_style_change_reuses_chunk_cache_and_redraws_panels(tmp_path):
    src = "第一章\n方鸿渐在甲板上。\n第二章\n方鸿渐在读书。"
    chat, img = FakeChat(), FakeImage()
    asyncio.run(
        creative_comic(
            src,
            output_dir=str(tmp_path),
            chat=chat,
            image=img,
            style_guide="watercolor",
        )
    )
    assert chat.calls > 0
    assert img.calls > 0
    state = ProjectState.load(tmp_path / "state.json")
    assert state.chunk_cache
    assert state.panels_done
    cache_keys = set(state.chunk_cache)

    chat2, img2 = FakeChat(), FakeImage()
    asyncio.run(
        creative_comic(
            src,
            output_dir=str(tmp_path),
            chat=chat2,
            image=img2,
            style_guide="ink sketch",
        )
    )
    assert chat2.calls == 0
    assert img2.calls > 0
    state2 = ProjectState.load(tmp_path / "state.json")
    assert set(state2.chunk_cache) == cache_keys
    assert set(state2.panels_done) == {"c0000-p0000", "c0001-p0000"}


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_source_change_drops_chunk_cache(tmp_path):
    src_a = "第一章\n方鸿渐在甲板上。\n第二章\n方鸿渐在读书。"
    chat, img = FakeChat(), FakeImage()
    asyncio.run(creative_comic(src_a, output_dir=str(tmp_path), chat=chat, image=img))
    assert chat.calls > 0
    state = ProjectState.load(tmp_path / "state.json")
    assert state.chunk_cache

    src_b = "第一章\n方鸿渐改在图书馆读书。\n第二章\n方鸿渐在读书。"
    chat2, img2 = FakeChat(), FakeImage()
    asyncio.run(creative_comic(src_b, output_dir=str(tmp_path), chat=chat2, image=img2))
    assert chat2.calls > 0
    state2 = ProjectState.load(tmp_path / "state.json")
    assert state2.structure_fingerprint == _structure_fingerprint(src_b)


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_style_change_ignores_panel_keys_filter(tmp_path):
    src = "第一章\n方鸿渐在甲板上。\n第二章\n方鸿渐在读书。"
    asyncio.run(
        creative_comic(
            src,
            output_dir=str(tmp_path),
            chat=FakeChat(),
            image=FakeImage(),
            style_guide="watercolor",
        )
    )
    state = ProjectState.load(tmp_path / "state.json")
    assert len(state.panels_done) == 2

    chat2, img2 = FakeChat(), FakeImage()
    asyncio.run(
        creative_comic(
            src,
            output_dir=str(tmp_path),
            chat=chat2,
            image=img2,
            style_guide="ink sketch",
            panel_keys=["c0000-p0000"],
        )
    )
    state2 = ProjectState.load(tmp_path / "state.json")
    assert set(state2.panels_done) == {"c0000-p0000", "c0001-p0000"}
    assert img2.calls == 3  # portrait + 2 panels after soft-invalidate


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_legacy_state_with_matching_combined_fingerprint_migrates(tmp_path):
    src = "第一章\n方鸿渐在甲板上。"
    asyncio.run(creative_comic(src, output_dir=str(tmp_path), chat=FakeChat(), image=FakeImage()))
    state = ProjectState.load(tmp_path / "state.json")
    cache_keys = set(state.chunk_cache)
    image_config = ImageConfig()
    legacy = _input_fingerprint(
        src,
        None,
        snapshot=state.model_snapshot,
        panel_continuity=image_config.panel_continuity,
        l3_enabled=False,
    )
    state.structure_fingerprint = ""
    state.render_fingerprint = ""
    state.source_fingerprint = legacy
    state.save(tmp_path / "state.json")

    chat2, img2 = FakeChat(), FakeImage()
    asyncio.run(creative_comic(src, output_dir=str(tmp_path), chat=chat2, image=img2))
    assert chat2.calls == 0
    assert img2.calls == 0
    loaded = ProjectState.load(tmp_path / "state.json")
    assert loaded.structure_fingerprint == _structure_fingerprint(src)
    assert loaded.render_fingerprint
    assert loaded.source_fingerprint == loaded.structure_fingerprint
    assert set(loaded.chunk_cache) == cache_keys
