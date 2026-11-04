"""tests/test_cli_output_layout.py — 终端报告排版规整的回归约束。"""

import io
import logging

from core import cli_generate, perf


def test_progress_bar_has_no_postfix_comma():
    """The old bar_format rendered '[, stage]'; the stage now lives in desc."""
    stream = io.StringIO()
    with cli_generate._progress_display(stream=stream) as on_progress:
        on_progress("export", 0.9)
        on_progress("done", 1.0)
    out = stream.getvalue()
    assert "[export]" in out or "export" in out
    assert "[, export]" not in out
    assert "generating comic [export]" in out


def test_timing_summary_uses_aligned_columns(capsys):
    collector = perf.PerfCollector()
    with collector.measure("segment"):
        pass
    for _ in range(4):
        with collector.measure("page"):
            pass
    collector.log_summary()
    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert lines[0] == "timing summary:"
    assert "stage" in lines[1] and "total" in lines[1] and "pct" in lines[1]  # header
    # Every data row starts with two-space indent + a fixed-width label cell.
    for line in lines[2:]:
        assert line.startswith("  ")
        assert line.rstrip().endswith("%")
    assert any("TOTAL" in line for line in lines)
    assert any("page" in line and "4" in line for line in lines)


def test_safe_write_routes_report_through_writer():
    captured: list[str] = []
    perf.set_safe_write(captured.append)
    try:
        collector = perf.PerfCollector()
        with collector.measure("export"):
            pass
        collector.log_summary()
    finally:
        perf.set_safe_write(None)
    assert captured  # summary went through the writer, not print
    assert captured[0].startswith("timing summary:")


def test_tqdm_logging_handler_uses_writer():
    captured: list[str] = []
    handler = cli_generate._TqdmLoggingHandler(captured.append)
    handler.setFormatter(logging.Formatter("warning: %(message)s"))
    record = logging.LogRecord("x", logging.WARNING, "", 0, "manga2pdf missing", None, None)
    handler.emit(record)
    assert captured == ["warning: manga2pdf missing"]


def test_result_block_is_aligned():
    class Stale:
        pass

    class State:
        render_mode = "panel_compose"
        panels_done = [1, 2, 3]
        skipped: list = []
        needs_review: list = []
        stale_panels = [Stale()]

    class Proj:
        project_id = "demo"
        pages = [1, 2, 3, 4]
        pdf = "comic_out/comic.pdf"
        webtoon = None
        cbz = None
        state = State()

    lines = cli_generate._result_lines(Proj())
    assert lines[0] == ""  # blank line separates from timing block
    assert lines[1] == "result:"
    assert lines[2] == "  project   : demo"
    assert any("pdf       : comic_out/comic.pdf" in line for line in lines)
    assert any("stale     : 1 panel(s) need redraw" in line for line in lines)


def test_finished_page_result_reports_rendered_pages_not_panels():
    """A finished_page run draws whole pages; it must not report a '0 generated' panel count."""

    class State:
        render_mode = "finished_page"
        pages_done = [
            "c0000-p0000",
            "c0000-p0001",
            "c0000-p0002",
            "c0001-p0000",
            "c0001-p0001",
        ]
        skipped_pages: list = []
        panels_done: list = []
        skipped: list = []
        needs_review: list = []
        stale_panels: list = []

    class Proj:
        project_id = "demo"
        pages = [1, 2, 3, 4, 5]
        pdf = None
        webtoon = None
        cbz = None
        state = State()

    block = "\n".join(cli_generate._result_lines(Proj()))
    assert "panels" not in block
    assert "0 generated" not in block
    assert "5 pages rendered" in block
    assert "0 skipped (content filter)" in block


def test_panel_compose_result_still_reports_panels():
    """The panel_compose wording and counts stay unchanged."""

    class State:
        render_mode = "panel_compose"
        panels_done = [1, 2, 3]
        skipped = ["rejected"]
        pages_done: list = []
        skipped_pages: list = []
        needs_review: list = []
        stale_panels: list = []

    class Proj:
        project_id = "demo"
        pages = [1, 2]
        pdf = None
        webtoon = None
        cbz = None
        state = State()

    block = "\n".join(cli_generate._result_lines(Proj()))
    assert "panels    : 3 generated, 1 skipped (content filter)" in block
    assert "pages rendered" not in block
