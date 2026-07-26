"""tests.test_d2_coverage — compute_coverage_report 纯函数（不调网络）。"""

from core.comic.coverage import compute_coverage_report
from core.schemas import CausalLink, PageScript, PageScriptPage, SourceSpan

SOURCE = "方鸿渐在甲板远眺大海。方鸿渐在书房读书。"


def _full_pass() -> list[PageScript]:
    """每页必含信息/因果/原文 span 齐备的合成样本。"""
    return [
        PageScript(
            chapter_id="chapter_1",
            pages=[
                PageScriptPage(
                    page_index=0,
                    required_information="方鸿渐在甲板远眺大海。",
                    causal_links=[CausalLink(cause="登船", effect="遇见苏小姐")],
                    source_spans=[
                        SourceSpan(start=0, end=3, text="方鸿渐", chapter_id="chapter_1")
                    ],
                    panel_ids=["p1"],
                )
            ],
        ),
        PageScript(
            chapter_id="chapter_2",
            pages=[
                PageScriptPage(
                    page_index=0,
                    required_information="方鸿渐在书房读书。",
                    causal_links=[CausalLink(cause="读书", effect="思考人生")],
                    source_spans=[
                        SourceSpan(start=0, end=3, text="方鸿渐", chapter_id="chapter_2")
                    ],
                    panel_ids=["p2"],
                )
            ],
        ),
    ]


def test_full_sample_three_metrics_one():
    report = compute_coverage_report(_full_pass(), SOURCE)
    assert report.required_coverage.coverage_ratio == 1.0
    assert report.causal_coverage.coverage_ratio == 1.0
    assert report.span_coverage.coverage_ratio == 1.0
    assert report.below_threshold_pages == []
    assert report.overall_passed is True


def test_missing_span_text_drops_span_coverage():
    """故意让 span.text 无法子串归属原文（缺失/不可回溯）→ 原文回溯率 < 0.95。"""
    bad = [
        PageScript(
            chapter_id="chapter_1",
            pages=[
                PageScriptPage(
                    page_index=0,
                    required_information="方鸿渐在甲板远眺。",  # 达标
                    causal_links=[CausalLink(cause="登船", effect="遇见苏小姐")],  # 达标
                    source_spans=[
                        SourceSpan(start=0, end=4, text="不存在于原文", chapter_id="chapter_1")
                    ],
                    panel_ids=["p1"],
                )
            ],
        )
    ]
    report = compute_coverage_report(bad, SOURCE)
    assert report.span_coverage.coverage_ratio < 0.95
    assert report.span_coverage.passed is False
    assert "c0000#chapter_1#p0" in report.below_threshold_pages
    # 其余两项仍达标
    assert report.required_coverage.coverage_ratio == 1.0
    assert report.causal_coverage.coverage_ratio == 1.0


def test_pure_function_deterministic():
    a = compute_coverage_report(_full_pass(), SOURCE)
    b = compute_coverage_report(_full_pass(), SOURCE)
    assert a.model_dump() == b.model_dump()


def test_skipped_pages_count_as_uncovered():
    ps = [
        PageScript(
            chapter_id="c1",
            pages=[
                PageScriptPage(
                    page_index=0,
                    required_information="x",
                    causal_links=[CausalLink(cause="a", effect="b")],
                    source_spans=[SourceSpan(start=0, end=3, text="方鸿渐")],
                )
            ],
            skipped_pages=[0],
        )
    ]
    report = compute_coverage_report(ps, SOURCE)
    # Still in the denominator; skipped ≠ success
    assert report.required_coverage.total == 1
    assert report.required_coverage.covered == 0
    assert report.required_coverage.coverage_ratio == 0.0
    assert report.required_coverage.passed is False
    assert any("p0" in k for k in report.below_threshold_pages)


def test_threshold_override_unifies():
    ok = [
        PageScript(
            chapter_id="c",
            pages=[
                PageScriptPage(
                    page_index=0,
                    required_information="ok",
                    causal_links=[CausalLink(cause="a", effect="b")],
                    source_spans=[SourceSpan(start=0, end=3, text="方鸿渐")],
                )
            ],
        )
    ]
    rep = compute_coverage_report(ok, SOURCE, threshold=0.5)
    assert rep.required_coverage.threshold == 0.5
    assert rep.causal_coverage.threshold == 0.5
    assert rep.span_coverage.threshold == 0.5
    assert rep.overall_passed is True


def test_crlf_normalized_in_source_attribution():
    ps = [
        PageScript(
            chapter_id="c",
            pages=[
                PageScriptPage(
                    page_index=0,
                    required_information="x",
                    causal_links=[CausalLink(cause="a", effect="b")],
                    source_spans=[SourceSpan(start=0, end=3, text="方鸿渐")],
                )
            ],
        )
    ]
    # 原文带 \r\n 也应被归一化后正确归属
    rep = compute_coverage_report(ps, "方鸿渐\r\n在甲板远眺。")
    assert rep.span_coverage.coverage_ratio == 1.0


def test_report_json_has_three_metrics():
    import json

    report = compute_coverage_report(_full_pass(), SOURCE)
    obj = json.loads(report.model_dump_json())
    assert set(["required_coverage", "causal_coverage", "span_coverage"]).issubset(obj.keys())
    assert "below_threshold_pages" in obj
    assert "overall_passed" in obj
