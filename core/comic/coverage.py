"""core.comic.coverage — D2 三指标覆盖率定量门禁（纯本地，不调任何 chat API）。

``compute_coverage_report`` 接收一份 ``PageScript`` 列表与原文 ``source_text``，
核算三指标（必含信息覆盖率 / 因果链完整率 / 原文回溯率）并产出 ``CoverageReport``。
原文回溯率不依赖全局偏移，而用「子串归属」证明：``span.text.strip() in normalize(
source_text)`` 即视为可回溯到《Journey to the West》原文，规避 ``segment_text`` 的
overlap / 换行归一化破坏，且可被单测数学化证明。
"""

from pathlib import Path

from core.schemas import CoverageMetric, CoverageReport, PageScript


def _normalize(text: str) -> str:
    """把 ``\\r\\n`` / ``\\r`` 统一为 ``\\n``，对齐 density 的归一化口径。"""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def compute_coverage_report(
    page_scripts: list[PageScript],
    source_text: str,
    *,
    threshold: float | None = None,
    required_threshold: float = 0.90,
    causal_threshold: float = 0.85,
    span_threshold: float = 0.95,
) -> CoverageReport:
    """纯函数：核算三指标，不调任何 chat API（单测无需网络）。

    三指标口径（均按页面/条目聚合后归一化到 [0,1]）：
    - 必含信息覆盖率 = covered_required / total_required（每页 1 条）
    - 因果链完整率   = complete_causal / total_causal（每条 causal_link 需 cause&effect 非空）
    - 原文回溯率     = traceable_span / total_span（每个 source_span 的 text 需可子串归属原文）

    ``skipped_pages`` 中的页仍计入三项分母，视为未覆盖（不 vacuous 通过）。
    ``threshold`` 若非 ``None`` 则统一覆盖三项阈值。

    Args:
        page_scripts: 来自 ``state.chunk_cache[*].page_script`` 的产物列表。
        source_text: 原文文本（用于子串归属证明）。
        threshold: 统一覆盖三项的阈值（``None`` 时使用各自细分阈值）。
        required_threshold / causal_threshold / span_threshold: 三项细分告警线。

    Returns:
        ``CoverageReport``，三指标 + ``below_threshold_pages`` + ``overall_passed``。
    """
    req_t = threshold if threshold is not None else required_threshold
    cau_t = threshold if threshold is not None else causal_threshold
    spa_t = threshold if threshold is not None else span_threshold
    src = _normalize(source_text)

    req_total = req_cov = 0
    cau_total = cau_cov = 0
    spa_total = spa_cov = 0
    below: list[str] = []

    for ci, ps in enumerate(page_scripts):
        skipped = set(ps.skipped_pages)
        for pi, page in enumerate(ps.pages):
            key = f"c{ci:04d}#{ps.chapter_id}#p{pi}"
            if pi in skipped:
                req_total += 1
                below.append(key)
                # Skipped: causal/span in denominator, no credit.
                for _link in page.causal_links:
                    cau_total += 1
                for _sp in page.source_spans:
                    spa_total += 1
                continue
            # 必含信息
            req_total += 1
            req_ok = bool(page.required_information.strip())
            req_cov += 1 if req_ok else 0
            # 因果链
            for link in page.causal_links:
                cau_total += 1
                if link.cause.strip() and link.effect.strip():
                    cau_cov += 1
            # 原文回溯
            for sp in page.source_spans:
                spa_total += 1
                if sp.text.strip() and sp.text.strip() in src:
                    spa_cov += 1
            # 不达标页归因
            span_bad = any(
                not (sp.text.strip() and sp.text.strip() in src) for sp in page.source_spans
            )
            if (
                (not req_ok)
                or span_bad
                or any(
                    not (link.cause.strip() and link.effect.strip()) for link in page.causal_links
                )
            ):
                below.append(key)

        # After iterating pages, for any skipped index with no page object:
        for pi in sorted(skipped):
            if pi >= len(ps.pages):
                key = f"c{ci:04d}#{ps.chapter_id}#p{pi}"
                req_total += 1
                below.append(key)

    def _metric(total: int, covered: int, th: float) -> CoverageMetric:
        ratio = 1.0 if total == 0 else covered / total
        return CoverageMetric(
            total=total, covered=covered, coverage_ratio=ratio, threshold=th, passed=ratio >= th
        )

    report = CoverageReport(
        required_coverage=_metric(req_total, req_cov, req_t),
        causal_coverage=_metric(cau_total, cau_cov, cau_t),
        span_coverage=_metric(spa_total, spa_cov, spa_t),
        threshold=threshold if threshold is not None else spa_t,
        below_threshold_pages=below,
    )
    report.overall_passed = (
        report.required_coverage.passed
        and report.causal_coverage.passed
        and report.span_coverage.passed
    )
    return report


def write_coverage_report(report: CoverageReport, output_dir: str | Path) -> Path:
    """把 ``CoverageReport`` 落盘到 ``output_dir/coverage_report.json`` 并返回路径。"""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "coverage_report.json"
    path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    return path
