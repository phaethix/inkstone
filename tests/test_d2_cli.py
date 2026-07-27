"""tests.test_d2_cli — coverage 子命令 text/json/strict 与 plan D2 行（不调网络）。"""

import argparse
import json

import pytest

from core.cli import _build_parser, _run_coverage, _run_plan
from core.schemas import (
    CausalLink,
    ChunkCache,
    PageScript,
    PageScriptPage,
    ProjectState,
    SourceSpan,
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
    assert "原型" in out or "optional" in out.lower()
    assert "信息完备分镜" in out
    assert "需 coverage 验收" not in out
    assert "D2 预期" not in out
