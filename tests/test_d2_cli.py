"""tests.test_d2_cli — coverage 子命令 text/json/strict 与 plan D2 行（不调网络）。"""

import argparse
import json

import pytest

from core.cli import _build_parser, _run_coverage, _run_plan
from core.density import estimate
from core.schemas import (
    CausalLink,
    ChunkCache,
    ComicPagePlan,
    ComicPagePlanSet,
    PagePanelSpec,
    PageScript,
    PageScriptPage,
    Panel,
    ProjectState,
    SourceSpan,
    Storyboard,
)

SOURCE = "方鸿渐在甲板远眺大海。"


def _cov_args(out, **kw):
    ns = argparse.Namespace(
        out=str(out),
        source=None,
        format="text",
        strict=False,
        threshold=None,
        req_threshold=0.90,
        causal_threshold=0.85,
        span_threshold=0.95,
    )
    for key, value in kw.items():
        setattr(ns, key, value)
    return ns


def _make_state(out, page_scripts, source_text, source_name="source.txt"):
    """写出 state.json（含 page_scripts）与 source.txt。"""
    state = ProjectState(
        project_id="t",
        chunk_cache={str(i): ChunkCache(page_script=ps) for i, ps in enumerate(page_scripts)},
    )
    state.save(out / "state.json")
    (out / source_name).write_text(source_text, encoding="utf-8")


def _full_pass():
    return [
        PageScript(
            chapter_id="chapter_1",
            pages=[
                PageScriptPage(
                    page_index=0,
                    required_information="方鸿渐在甲板远眺。",
                    causal_links=[CausalLink(cause="登船", effect="遇见苏小姐")],
                    source_spans=[SourceSpan(start=0, end=3, text="方鸿渐")],
                    panel_ids=["p1"],
                )
            ],
        )
    ]


def _bad_span():
    return [
        PageScript(
            chapter_id="c",
            pages=[
                PageScriptPage(
                    page_index=0,
                    required_information="ok",
                    causal_links=[CausalLink(cause="a", effect="b")],
                    source_spans=[SourceSpan(start=0, end=4, text="缺失文本")],
                    panel_ids=["p1"],
                )
            ],
        )
    ]


def test_cli_coverage_text_pass(tmp_path, capsys):
    _make_state(tmp_path, _full_pass(), SOURCE)
    _run_coverage(_cov_args(tmp_path))  # 无 strict → 退出码 0
    out = capsys.readouterr().out
    assert "prototype report" in out
    assert "not a readability or quality gate" in out
    assert "必含信息覆盖率" in out
    assert "原文回溯率" in out
    assert "全部达标" in out
    assert (tmp_path / "coverage_report.json").exists()  # 落盘


def test_cli_coverage_json_valid(tmp_path, capsys):
    _make_state(tmp_path, _full_pass(), SOURCE)
    _run_coverage(_cov_args(tmp_path, format="json"))
    out = capsys.readouterr().out
    data = json.loads(out)  # 合法 JSON，可被 jq 解析
    assert set(["required_coverage", "causal_coverage", "span_coverage"]).issubset(data.keys())
    assert data["overall_passed"] is True


def test_cli_coverage_strict_fail_exits_1(tmp_path):
    _make_state(tmp_path, _bad_span(), SOURCE)
    with pytest.raises(SystemExit) as exc:
        _run_coverage(_cov_args(tmp_path, strict=True))
    assert exc.value.code == 1


def test_cli_coverage_no_strict_exits_0_with_warning(tmp_path, capsys):
    _make_state(tmp_path, _bad_span(), SOURCE)
    _run_coverage(_cov_args(tmp_path, strict=False))  # 不应抛 SystemExit
    out = capsys.readouterr().out
    assert "警告" in out  # 告警行


def test_cli_help_marks_prototypes_honestly():
    parser = _build_parser()
    top_help = parser.format_help()
    assert "estimate" in top_help.lower()
    assert "prototype" in top_help.lower() or "not a quality gate" in top_help.lower()
    plan = parser._subparsers._group_actions[0].choices["plan"]
    dens_help = next(a.help for a in plan._actions if "--density" in a.option_strings)
    assert "overview" in dens_help.lower() or "主线概览" in dens_help


def test_cli_plan_prints_estimate_only_warning(tmp_path, capsys):
    book = tmp_path / "book.txt"
    book.write_text("第一章\n方鸿渐在甲板上。", encoding="utf-8")
    _run_plan(
        argparse.Namespace(
            book=str(book),
            density="B",
            format="page",
            api="agnes",
            concurrency=4,
            price_per_panel=None,
        )
    )
    out = capsys.readouterr().out
    assert "uncalibrated estimate" in out.lower() or "未校准" in out
    assert "[experimental]" in out.lower()


def test_cli_coverage_no_page_script_hints_env(tmp_path, capsys):
    """无 page_script 时不应 vacuous 通过，应提示 INKSTONE_PAGE_SCRIPT。"""
    state = ProjectState(project_id="t", chunk_cache={"0": ChunkCache()})
    state.save(tmp_path / "state.json")
    (tmp_path / "source.txt").write_text(SOURCE, encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        _run_coverage(_cov_args(tmp_path))
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "INKSTONE_PAGE_SCRIPT" in out
    assert "全部达标" not in out


def test_cli_plan_prints_d2_expectation(tmp_path, capsys):
    book = tmp_path / "book.txt"
    book.write_text("第一章\n方鸿渐在甲板上。\n第二章\n方鸿渐在读书。", encoding="utf-8")
    _run_plan(
        argparse.Namespace(
            book=str(book),
            density="B",
            format="page",
            api="agnes",
            concurrency=4,
            price_per_panel=None,
        )
    )
    out = capsys.readouterr().out
    assert "PageScript" in out
    assert "原型" in out
    assert "INKSTONE_PAGE_SCRIPT" in out
    assert "不是成片质量门" in out
    assert "信息完备分镜" not in out
    assert "需 coverage 验收" not in out
    assert "D2 预期" not in out


def test_plan_uses_counts_already_stored_on_the_project(tmp_path, capsys):
    book = tmp_path / "book.txt"
    book.write_text("第一章\n石猴出世。", encoding="utf-8")
    state = ProjectState(
        project_id="p",
        page_cache={
            "0": ComicPagePlanSet(
                pages=[
                    ComicPagePlan(
                        page_id="p1",
                        panels=[
                            PagePanelSpec(panel_id="a"),
                            PagePanelSpec(panel_id="b"),
                        ],
                    ),
                    ComicPagePlan(
                        page_id="p2",
                        panels=[PagePanelSpec(panel_id="c")],
                    ),
                ]
            )
        },
    )
    state.save(tmp_path / "state.json")
    _run_plan(
        argparse.Namespace(
            book=str(book),
            density="B",
            format="page",
            api="agnes",
            concurrency=4,
            price_per_panel=None,
            out=str(tmp_path),
        )
    )
    est = estimate(book, density="B", observed_panels=[3], observed_pages=[2])
    out = capsys.readouterr().out
    assert f"{est.panels} 格 / {est.pages} 页" in out
    assert "已规划" in out
    assert "counts from the stored project" in out
    assert "tier constants stay the experience rate" in out
    assert "uncalibrated estimate" not in out.lower()


def test_a_half_planned_project_keeps_the_pages_it_did_measure(tmp_path, capsys):
    """One chunk with a page plan and one with only a storyboard still has pages.

    ``_planned_counts`` used to drop every page count when the page list and the
    panel list ended up different lengths, which is exactly what a run stopped
    between two chunks looks like. The measured pages of the finished chunk are
    evidence and must survive; the chunk without a page plan is projected from
    the average of the ones that have it, the same rule ``_scale_observed``
    already applies to panels.

    The chunk measured one panel per page, so keeping its pages reports six
    pages where dropping them would fall back to ``panels / 4`` and report two.
    """
    book = tmp_path / "book.txt"
    book.write_text("第一章\n石猴出世。\n第二章\n方鸿渐在读书。", encoding="utf-8")
    state = ProjectState(
        project_id="p",
        page_cache={
            "0": ComicPagePlanSet(
                pages=[
                    ComicPagePlan(page_id="p1", panels=[PagePanelSpec(panel_id="a")]),
                    ComicPagePlan(page_id="p2", panels=[PagePanelSpec(panel_id="b")]),
                    ComicPagePlan(page_id="p3", panels=[PagePanelSpec(panel_id="c")]),
                ]
            )
        },
        chunk_cache={
            "1": ChunkCache(
                storyboard=Storyboard(
                    chapter_id="1",
                    panels=[Panel(panel_id=f"x{i}") for i in range(3)],
                )
            )
        },
    )
    state.save(tmp_path / "state.json")
    _run_plan(
        argparse.Namespace(
            book=str(book),
            density="B",
            format="page",
            api="agnes",
            concurrency=4,
            price_per_panel=None,
            out=str(tmp_path),
        )
    )
    out = capsys.readouterr().out
    # 3 measured panels on 3 measured pages, plus 3 storyboarded panels in the
    # other chunk, gives 6 panels and 6 pages rather than the 2 a rate implies.
    assert "counts from the stored project" in out
    assert "6 格 / 6 页" in out


def test_a_gap_in_the_page_plans_does_not_pair_the_wrong_chunk_counts(tmp_path, capsys):
    """Chunk 0 skipped its page plan, so its panels must not pair with chunk 1's pages.

    A content-policy rejection skips a chunk's page plan and the run continues, so
    ``page_cache`` can hold chunk 1 while chunk 0 has only a storyboard. The two
    lists then describe different chunks, and zipping them by position makes the
    measured rate nonsense: chunk 0's three panels get divided by chunk 1's four
    pages, a rate of 0.75 panels per page that then needs 19 pages for chunk 1.
    The rate has to come from the chunk that has both numbers.
    """
    book = tmp_path / "book.txt"
    book.write_text("第一章\n石猴出世。\n第二章\n方鸿渐在读书。", encoding="utf-8")
    # 14 panels across four pages stays inside the four-panels-per-page packing
    # limit, so the estimate is not being carried by the capacity floor.
    per_page = [4, 4, 3, 3]
    state = ProjectState(
        project_id="p",
        page_cache={
            "1": ComicPagePlanSet(
                pages=[
                    ComicPagePlan(
                        page_id=f"p{index}",
                        panels=[PagePanelSpec(panel_id=f"a{index}-{n}") for n in range(count)],
                    )
                    for index, count in enumerate(per_page)
                ]
            )
        },
        chunk_cache={
            "0": ChunkCache(
                storyboard=Storyboard(
                    chapter_id="0",
                    panels=[Panel(panel_id=f"x{i}") for i in range(3)],
                )
            )
        },
    )
    state.save(tmp_path / "state.json")
    _run_plan(
        argparse.Namespace(
            book=str(book),
            density="B",
            format="page",
            api="agnes",
            concurrency=4,
            price_per_panel=None,
            out=str(tmp_path),
        )
    )
    out = capsys.readouterr().out
    # Chunk 1 measured 14 panels on 4 pages, so the rate is 3.5 panels per page.
    # Chunk 0's 3 panels need one page at that rate: 4 + 1 = 5 pages. Pairing the
    # numbers by position instead gave 23 pages.
    assert "17 格 / 5 页" in out
