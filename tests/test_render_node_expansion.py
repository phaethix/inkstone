"""Render nodes must include plans created during the render pass.

Expansion runs once, before the render-phase chunk loop. A chunk with no
pageset or storyboard at that moment gets no nodes. The loop can still plan
that chunk. Dispatch and the outstanding-render check then only know the
original nodes, so the new pages or panels are never drawn and the run still
returns.
"""

import asyncio

from core.comic.export import ExportEngine
from core.pipelines import creative_comic as pipeline
from core.pipelines.creative_comic import creative_comic
from tests.test_creative_comic import FakeChat as PanelChat
from tests.test_creative_comic import FakeImage as PanelImage
from tests.test_creative_comic import _fake_export_pdf as _fake_panel_export
from tests.test_finished_page_pipeline import FakeChat, FakeImage, _fake_export_pdf


def test_finished_page_renders_pageset_planned_during_render(tmp_path, monkeypatch):
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "finished_page")
    real_expand = pipeline._expand_page_render_nodes

    def expand_with_empty_page_cache(graph, state, chunk_count):
        state.page_cache.clear()
        return real_expand(graph, state, chunk_count)

    monkeypatch.setattr(pipeline, "_expand_page_render_nodes", expand_with_empty_page_cache)
    monkeypatch.setattr(ExportEngine, "export_pdf", _fake_export_pdf)

    proj = asyncio.run(
        creative_comic(
            "第一章\n福贵在村口。",
            output_dir=str(tmp_path),
            chat=FakeChat(),
            image=FakeImage(),
        )
    )

    assert "c0000-p0000" in proj.state.generated.pages
    assert "c0000-p0000" in proj.state.pages_done


def test_panel_continuity_renders_storyboard_planned_during_render(tmp_path, monkeypatch):
    monkeypatch.setenv("INKSTONE_PANEL_CONTINUITY", "1")
    real_expand = pipeline._expand_panel_render_nodes

    def expand_with_empty_storyboards(graph, state, chunk_count, *, continuity):
        for cache in state.chunk_cache.values():
            cache.storyboard = None
        return real_expand(graph, state, chunk_count, continuity=continuity)

    monkeypatch.setattr(pipeline, "_expand_panel_render_nodes", expand_with_empty_storyboards)
    monkeypatch.setattr(ExportEngine, "export_pdf", _fake_panel_export)

    proj = asyncio.run(
        creative_comic(
            "第一章\n方鸿渐在甲板上。",
            output_dir=str(tmp_path),
            chat=PanelChat(),
            image=PanelImage(),
            render_mode="panel_compose",
        )
    )

    assert proj.state.panels_done == ["c0000-p0000"]
    assert "c0000-p0000" in proj.state.generated.panels


def test_panel_batch_renders_storyboard_planned_during_render(tmp_path, monkeypatch):
    monkeypatch.setenv("INKSTONE_PANEL_CONTINUITY", "0")
    real_expand = pipeline._expand_panel_render_nodes

    def expand_with_empty_storyboards(graph, state, chunk_count, *, continuity):
        for cache in state.chunk_cache.values():
            cache.storyboard = None
        return real_expand(graph, state, chunk_count, continuity=continuity)

    monkeypatch.setattr(pipeline, "_expand_panel_render_nodes", expand_with_empty_storyboards)
    monkeypatch.setattr(ExportEngine, "export_pdf", _fake_panel_export)

    proj = asyncio.run(
        creative_comic(
            "第一章\n方鸿渐在甲板上。",
            output_dir=str(tmp_path),
            chat=PanelChat(),
            image=PanelImage(),
            render_mode="panel_compose",
        )
    )

    assert proj.state.panels_done == ["c0000-p0000"]
    assert "c0000-p0000" in proj.state.generated.panels
