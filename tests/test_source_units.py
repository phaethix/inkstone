"""Source units are positional slices of the normalized source."""

import asyncio
from pathlib import Path
from unittest.mock import patch

from core.comic.segmentation import source_units
from core.pipelines.creative_comic import _structure_fingerprint, creative_comic
from core.schemas import ProjectState
from tests.test_creative_comic import FakeChat, FakeImage, _fake_export_pdf


def test_units_are_slices_that_concatenate_to_the_normalized_source():
    raw = "前言\n第一章\r\n甲板上。\r\n第二章\r\n读书。"
    canonical = raw.replace("\r\n", "\n")
    units = source_units(raw)
    assert [unit.unit_id for unit in units] == ["u0000", "u0001", "u0002"]
    assert "".join(unit.text for unit in units) == canonical
    assert [(unit.start, unit.end) for unit in units] == [
        (0, units[0].end),
        (units[0].end, units[1].end),
        (units[1].end, len(canonical)),
    ]
    assert units[1].heading.startswith("第一章")
    assert units[2].heading.startswith("第二章")
    assert canonical[units[1].start : units[1].end] == units[1].text


def test_a_headingless_text_is_one_unit():
    units = source_units("甲板上。")
    assert len(units) == 1
    assert units[0].unit_id == "u0000"
    assert units[0].text == "甲板上。"
    assert units[0].heading == ""


def test_a_hui_heading_stays_one_unit():
    path = Path(__file__).resolve().parents[1] / "examples/showcase/journey-west-ch1/source.txt"
    units = source_units(path.read_text(encoding="utf-8"))
    assert len(units) == 1
    assert units[0].unit_id == "u0000"


def test_an_older_state_loads_without_units():
    loaded = ProjectState.model_validate_json('{"project_id":"p1"}')
    assert loaded.source_units == []


@patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf)
def test_a_run_stores_units_without_changing_the_fingerprint(tmp_path):
    src = "第一章\n方鸿渐在甲板上。\n第二章\n方鸿渐在读书。"
    chat = FakeChat()
    proj = asyncio.run(creative_comic(src, output_dir=str(tmp_path), chat=chat, image=FakeImage()))
    assert [unit.unit_id for unit in proj.state.source_units] == ["u0000", "u0001"]
    assert "".join(unit.text for unit in proj.state.source_units) == src
    assert proj.state.structure_fingerprint == _structure_fingerprint(src)

    chat2 = FakeChat()
    proj2 = asyncio.run(
        creative_comic(src, output_dir=str(tmp_path), chat=chat2, image=FakeImage())
    )
    assert chat2.calls == 0
    assert proj2.state.source_units == proj.state.source_units
