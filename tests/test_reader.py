"""A browser reader over stored page images. It does not call a model."""

import argparse

import pytest

from core.cli import _run_read
from core.comic.reader import chapter_pages, write_reader


def _png(directory, name: str) -> None:
    (directory / name).write_bytes(b"\x89PNG")


def test_reader_groups_pages_by_chunk_and_skips_the_webtoon(tmp_path):
    pages = tmp_path / "pages"
    pages.mkdir()
    for name in (
        "page_c0001_p0000.png",
        "page_c0000_p0001.png",
        "webtoon.png",
        "page_c0000_p0000.png",
    ):
        _png(pages, name)
    groups = chapter_pages(pages)
    assert [label for label, _paths in groups] == ["c0000", "c0001"]
    assert [path.name for path in groups[0][1]] == [
        "page_c0000_p0000.png",
        "page_c0000_p0001.png",
    ]


def test_reader_html_lists_pages_in_filename_order(tmp_path):
    pages = tmp_path / "pages"
    pages.mkdir()
    _png(pages, "page_c0000_p0001.png")
    _png(pages, "page_c0000_p0000.png")
    _png(pages, "webtoon.png")
    html = write_reader(tmp_path).read_text(encoding="utf-8")
    assert html.index("pages/page_c0000_p0000.png") < html.index("pages/page_c0000_p0001.png")
    assert "webtoon.png" not in html
    assert "ArrowRight" in html
    assert "c0000" in html
    assert 'id="count"' in html
    assert "book.pages.length" in html


def test_read_without_pages_writes_nothing(tmp_path, capsys):
    code = _run_read(argparse.Namespace(out=str(tmp_path)))
    assert code == 1
    assert not (tmp_path / "reader.html").exists()
    assert "no page images" in capsys.readouterr().err


def test_reader_omits_pages_the_plan_dropped_or_skipped(tmp_path):
    from core.schemas import ComicPagePlan, ComicPagePlanSet, ProjectState

    pages = tmp_path / "pages"
    pages.mkdir()
    for name in (
        "page_c0000_p0000.png",
        "page_c0000_p0001.png",
        "page_c0000_p0002.png",
    ):
        _png(pages, name)
    state = ProjectState(project_id="p", render_mode="finished_page")
    state.page_cache["0"] = ComicPagePlanSet(
        unit_id="0",
        pages=[ComicPagePlan(page_id="a"), ComicPagePlan(page_id="b")],
    )
    state.skipped_pages = ["c0000-p0001"]
    state.save(tmp_path / "state.json")

    html = write_reader(tmp_path).read_text(encoding="utf-8")
    assert "page_c0000_p0000.png" in html
    assert "page_c0000_p0001.png" not in html
    assert "page_c0000_p0002.png" not in html


def test_read_refuses_to_break_out_of_the_script(tmp_path):
    pages = tmp_path / "pages"
    pages.mkdir()
    _png(pages, "page_<script>.png")
    html = write_reader(tmp_path).read_text(encoding="utf-8")
    assert "<script>.png" not in html
    assert "\\u003cscript\\u003e" in html or "\\u003c" in html


def test_read_command_is_not_rewritten_as_generate(monkeypatch, tmp_path, capsys):
    pages = tmp_path / "pages"
    pages.mkdir()
    _png(pages, "page_c0000_p0000.png")
    monkeypatch.setattr(
        "sys.argv",
        ["inkstone", "read", "--out", str(tmp_path)],
    )
    with pytest.raises(SystemExit) as raised:
        from core.cli import main

        main()
    assert raised.value.code == 0
    assert (tmp_path / "reader.html").is_file()
    assert "reader ->" in capsys.readouterr().out
