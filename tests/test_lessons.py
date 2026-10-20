"""Craft-lesson library: human evidence, retrieval caps, CLI lifecycle."""

import json
from pathlib import Path

import pytest

from core.cli import _build_parser, _run_lesson
from core.lessons import LessonError, LessonStore, format_craft_notes
from core.schemas import ProjectState, StoryElements
from core.screenwriter import build_page_plan_request


def _add(store: LessonStore, **overrides):
    data = {
        "cue": "章节开场人物多、对白少时",
        "lesson": "先用环境远景建立空间与时代，再切近景",
        "run": "demo",
        "decision": "redraw",
        "pages": ["c0000-p0000"],
        "genre": "wuxia",
    }
    data.update(overrides)
    return store.add(**data)


def _run(tmp_path: Path) -> Path:
    run = tmp_path / "comic_out" / "demo"
    run.mkdir(parents=True)
    state = ProjectState(
        project_id="demo",
        pages_done=["c0000-p0000", "c0000-p0001"],
        stale_pages=["c0000-p0001"],
    )
    state.save(run / "state.json")
    (run / "review").mkdir()
    (run / "review" / "manifest.json").write_text(
        json.dumps({"genre": "wuxia", "harness_digest": "sha256:fixture"}),
        encoding="utf-8",
    )
    return run


def test_add_requires_evidence_and_a_page_for_a_page_decision(tmp_path):
    store = LessonStore(tmp_path)
    with pytest.raises(LessonError, match="from-run"):
        _add(store, run="")
    with pytest.raises(LessonError, match="needs at least one page"):
        _add(store, pages=[])
    with pytest.raises(LessonError, match="unknown decision"):
        _add(store, decision="looks_nice")


def test_add_writes_a_json_file_and_an_index(tmp_path):
    store = LessonStore(tmp_path)
    lesson = _add(store)

    path = store.path_for("wuxia", lesson.id)
    assert path.is_file()
    assert lesson.status == "active"
    assert (tmp_path / "library" / "wuxia" / "INDEX.md").is_file()
    assert lesson.id in (tmp_path / "library" / "wuxia" / "INDEX.md").read_text()
    assert [entry.id for entry in store.list(genre="wuxia")] == [lesson.id]


def test_retrieve_ranks_relevant_lessons_and_caps(tmp_path):
    store = LessonStore(tmp_path)
    _add(store, cue="章节开场人物多、对白少时", lesson="先用远景建立空间", genre="wuxia")
    _add(store, cue="章节开场人物多、对白少时", lesson="第二页再切近景", genre="wuxia")
    _add(store, cue="人物少、对白多时", lesson="用近景与视线推进", genre="wuxia")
    _add(store, cue="章节开场人物多、对白少时", lesson="先给时代环境", genre="general")

    found = store.retrieve(genre="wuxia", query="章节开场 人物多 对白少", limit=3, max_chars=1500)
    assert len(found) == 3
    assert any(entry.genre == "general" for entry in found)
    assert all(
        "远景" in entry.lesson or "近景" in entry.lesson or "时代" in entry.lesson
        for entry in found
    )


def test_retrieve_returns_nothing_without_cue_overlap(tmp_path):
    store = LessonStore(tmp_path)
    _add(store, cue="章节开场人物多、对白少时")
    assert store.retrieve(genre="wuxia", query="完全无关的侦探密室") == []


def test_retired_lessons_are_not_retrieved(tmp_path):
    store = LessonStore(tmp_path)
    lesson = _add(store)
    assert store.retrieve(genre="wuxia", query="章节开场") == [lesson]

    store.retire(lesson.id)

    assert store.retrieve(genre="wuxia", query="章节开场") == []
    assert store.list(genre="wuxia", include_inactive=True)[0].status == "retired"


def test_lesson_id_cannot_escape_the_library(tmp_path):
    store = LessonStore(tmp_path)
    with pytest.raises(LessonError, match="lesson id must be"):
        _add(store, lesson_id="../escape")


def test_measurement_markers_are_rejected(tmp_path):
    store = LessonStore(tmp_path)
    with pytest.raises(LessonError, match="measurement marker"):
        _add(store, lesson="use the acceptance rate to pick the page")


def test_format_craft_notes_is_scoped_and_compact(tmp_path):
    store = LessonStore(tmp_path)
    lesson = _add(store)
    text = format_craft_notes([lesson])
    assert text.startswith("Craft notes from earlier books")
    assert lesson.cue in text
    assert lesson.lesson in text
    assert format_craft_notes([]) == ""


def test_page_plan_includes_craft_notes_and_changes_its_digest():
    plain = build_page_plan_request("第一回", StoryElements(), model="m")
    noted = build_page_plan_request(
        "第一回",
        StoryElements(),
        craft_notes="Craft notes from earlier books:\n- When opening: use a wide shot",
        model="m",
    )
    assert "Craft notes from earlier books" in noted.messages[-1]["content"]
    assert "Craft notes from earlier books" not in plain.messages[-1]["content"]
    assert noted.digest() != plain.digest()


def test_cli_add_list_retire_and_no_entry(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("INKSTONE_DATA_DIR", str(tmp_path / "data"))
    run = _run(tmp_path)
    parser = _build_parser()

    add = parser.parse_args(
        [
            "lesson",
            "add",
            "--from-run",
            str(run),
            "--page",
            "c0000-p0001",
            "--decision",
            "redraw",
            "--reason-tier",
            "layout",
            "--cue",
            "章节开场人物多、对白少时",
            "--lesson",
            "先用环境远景建立空间",
        ]
    )
    assert _run_lesson(add) == 0
    out = capsys.readouterr().out
    assert "added l-" in out
    lesson_id = out.split("added ", 1)[1].split(" ", 1)[0]

    listed = parser.parse_args(["lesson", "list"])
    assert _run_lesson(listed) == 0
    assert lesson_id in capsys.readouterr().out

    retired = parser.parse_args(["lesson", "retire", lesson_id])
    assert _run_lesson(retired) == 0
    assert "retired" in capsys.readouterr().out

    no_entry = parser.parse_args(
        [
            "lesson",
            "add",
            "--from-run",
            str(run),
            "--decision",
            "redraw",
            "--cue",
            "x",
            "--lesson",
            "y",
            "--no-entry",
        ]
    )
    assert _run_lesson(no_entry) == 0
    assert "NO_ENTRY" in capsys.readouterr().out


def test_cli_rejects_a_page_that_is_not_stale(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("INKSTONE_DATA_DIR", str(tmp_path / "data"))
    run = _run(tmp_path)
    parser = _build_parser()
    args = parser.parse_args(
        [
            "lesson",
            "add",
            "--from-run",
            str(run),
            "--page",
            "c0000-p0000",
            "--decision",
            "redraw",
            "--cue",
            "x",
            "--lesson",
            "y",
        ]
    )
    assert _run_lesson(args) == 2
    assert "not marked stale" in capsys.readouterr().err


def test_a_lesson_reaches_the_page_plan_prompt(tmp_path, monkeypatch):
    import asyncio
    from unittest.mock import patch

    from core.pipelines.creative_comic import creative_comic
    from tests.test_finished_page_pipeline import FakeChat, FakeImage, _fake_export_pdf

    monkeypatch.setenv("INKSTONE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "finished_page")
    store = LessonStore(tmp_path / "data")
    lesson = store.add(
        cue="福贵在村口时",
        lesson="先给村口的远景，再切人物近景",
        run="demo",
        decision="redraw",
        pages=["c0000-p0000"],
        genre="general",
    )

    with patch("core.pipelines.creative_comic.ExportEngine.export_pdf", _fake_export_pdf):
        asyncio.run(
            creative_comic(
                "第一章\n福贵在村口。",
                output_dir=str(tmp_path / "out"),
                chat=FakeChat(),
                image=FakeImage(),
            )
        )

    prompt = json.loads(
        (tmp_path / "out" / "review" / "prompts" / "page_plan.c0000.json").read_text()
    )
    text = prompt["messages"][-1]["content"]
    assert "Craft notes from earlier books" in text
    assert lesson.lesson in text
