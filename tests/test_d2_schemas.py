"""tests.test_d2_schemas — D2 schema 往返与字段（不调网络）。"""

from core.schemas import (
    CausalLink,
    ChunkCache,
    CoverageMetric,
    CoverageReport,
    PageScript,
    PageScriptPage,
    SourceSpan,
    Storyboard,
    StoryElements,
)


def _sample_page_script() -> PageScript:
    return PageScript(
        chapter_id="chapter_1",
        pages=[
            PageScriptPage(
                page_index=0,
                required_information="方鸿渐在甲板上远眺。",
                causal_links=[CausalLink(cause="登船", effect="遇见苏小姐")],
                source_spans=[SourceSpan(start=0, end=3, text="方鸿渐", chapter_id="chapter_1")],
                panel_ids=["ch01_p01"],
            )
        ],
        skipped_pages=[],
    )


def test_page_script_roundtrip():
    ps = _sample_page_script()
    restored = PageScript.model_validate_json(ps.model_dump_json())
    assert restored.chapter_id == "chapter_1"
    assert restored.pages[0].required_information == "方鸿渐在甲板上远眺。"
    assert restored.pages[0].causal_links[0].cause == "登船"
    assert restored.pages[0].source_spans[0].text == "方鸿渐"
    assert restored.pages[0].panel_ids == ["ch01_p01"]


def test_skipped_pages_default_and_serializable():
    ps = PageScript(chapter_id="c")
    assert ps.skipped_pages == []
    ps2 = PageScript(chapter_id="c", skipped_pages=[0, 1])
    assert ps2.skipped_pages == [0, 1]
    assert PageScript.model_validate_json(ps2.model_dump_json()).skipped_pages == [0, 1]


def test_coverage_report_overall_passed():
    good = CoverageMetric(total=10, covered=10, coverage_ratio=1.0, threshold=0.9, passed=True)
    bad = CoverageMetric(total=10, covered=1, coverage_ratio=0.1, threshold=0.9, passed=False)
    rep = CoverageReport(required_coverage=good, causal_coverage=good, span_coverage=bad)
    assert rep.overall_passed is False  # span 不达标 → 整体不通过
    rep2 = CoverageReport(
        required_coverage=good, causal_coverage=good, span_coverage=good, overall_passed=True
    )
    assert rep2.overall_passed is True
    assert rep2.threshold == 0.95  # 默认值保留


def test_coverage_report_field_names():
    good = CoverageMetric(total=1, covered=1, coverage_ratio=1.0, threshold=0.9, passed=True)
    rep = CoverageReport(required_coverage=good, causal_coverage=good, span_coverage=good)
    dumped = rep.model_dump()
    assert set(["required_coverage", "causal_coverage", "span_coverage"]).issubset(dumped.keys())
    assert "below_threshold_pages" in dumped
    assert "overall_passed" in dumped


def test_chunk_cache_carries_page_script():
    cc = ChunkCache()
    assert cc.page_script is None
    cc.page_script = _sample_page_script()
    restored = ChunkCache.model_validate_json(cc.model_dump_json())
    assert restored.page_script is not None
    assert restored.page_script.chapter_id == "chapter_1"
    # 与既有 elements / storyboard 字段共存
    cc.elements = StoryElements()
    cc.storyboard = Storyboard(chapter_id="chapter_1")
    assert cc.elements is not None and cc.storyboard is not None
