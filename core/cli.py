"""core.cli — Inkstone 统一命令行入口（D1）。

收编既有 ``examples/generate_comic.py`` 为 ``generate`` 子命令，并新增纯本地
的 ``plan`` 子命令（D1 密度预估）。 ``identity``/``coverage`` 为 D2/D3 占位，
本期不实现。

设计取舍：
- ``plan`` 完全离线（不读 AGNES_API_KEY、不调 API），复用 ``core.density.estimate``
  （内部用既有 ``segment_text``）给出格数/页数/成本/时长与 webtoon 体积预警，
  让用户在下单渲染前先知道体量，避免盲跑长任务。计费后端 / 并发 / 单价由
  ``--api`` / ``--concurrency`` / ``--price-per-panel`` 显式指定。
- ``generate`` 仅做薄壳转发，不改既有管线逻辑。
- 后向兼容：旧用法 ``inkstone <source> ...``（无子命令）默认走 ``generate``，
  使 ``scripts/start.sh`` 改为 ``python -m core.cli "$@"`` 后行为不变。
"""

import argparse
import sys
from pathlib import Path

from core.comic.coverage import compute_coverage_report, write_coverage_report
from core.density import DensityEstimate, estimate
from core.schemas import ProjectState


def _build_parser() -> argparse.ArgumentParser:
    """构造带 4 个子命令的顶层解析器。"""
    parser = argparse.ArgumentParser(prog="inkstone", description="Inkstone novel-to-comic generator")
    sub = parser.add_subparsers(dest="command", required=True)

    # generate：复用既有 examples/generate_comic.py，不改其逻辑。
    p_gen = sub.add_parser("generate", help="Run the full comic generation pipeline (calls Agnes API)")
    p_gen.add_argument(
        "source",
        nargs="?",
        default=None,
        help="Source text file path (default: examples/scene1.txt)",
    )
    p_gen.add_argument("--out", default=None, help="Output directory")
    p_gen.add_argument("--project", default=None, help="Stable project id (for resume)")
    p_gen.add_argument(
        "--format",
        choices=["page", "webtoon"],
        default="page",
        help="page=vertical PDF / webtoon=long-strip PNG",
    )

    # plan：D1 纯本地预估（不约束 generate）。
    p_plan = sub.add_parser(
        "plan",
        help="Offline density/cost/duration estimate (does not control generate).",
    )
    p_plan.add_argument("--book", required=True, help="Novel text file path")
    p_plan.add_argument(
        "--density",
        choices=["A", "B", "C"],
        default="B",
        help=(
            "A=main plot overview/主线概览 (sparse) "
            "B=chapter-complete/章级完整 (default) "
            "C=near-original/近原著 (dense); "
            "estimate only, does not control generate"
        ),
    )
    p_plan.add_argument(
        "--format",
        choices=["page", "webtoon"],
        default="page",
        help="page=vertical PDF / webtoon=long-strip PNG",
    )
    p_plan.add_argument(
        "--api",
        choices=["agnes", "openai-compat"],
        default="agnes",
        help="Billing backend: agnes (free tier) / openai-compat",
    )
    p_plan.add_argument(
        "--concurrency",
        type=int,
        default=4,
        help="Parallel render workers for duration estimate (default 4)",
    )
    p_plan.add_argument(
        "--price-per-panel",
        type=float,
        default=None,
        help="openai-compat unit price (CNY/panel); omit to show a price placeholder",
    )

    # identity：D3 占位；coverage：D2 实现。
    p_id = sub.add_parser("identity", help="[Coming in D3] Identity ledger visualization")
    p_id.add_argument("--view", action="store_true", help="Print alias/impact scope tree")
    p_id.add_argument("--merge", default=None, help="Merge alias: 'new:keep'")

    p_cov = sub.add_parser(
        "coverage",
        help="Legacy PageScript field report (prototype; not a readability/quality gate).",
    )
    p_cov.add_argument(
        "--out",
        default="comic_out",
        help="Generation output directory (contains state.json and source.txt; default comic_out)",
    )
    p_cov.add_argument(
        "--source",
        default=None,
        help="Original text path; defaults to <out>/source.txt",
    )
    p_cov.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format: text=human-readable three metrics / json=machine-readable (default text)",
    )
    p_cov.add_argument(
        "--strict",
        action="store_true",
        help="Exit code 1 if any metric fails; otherwise print warnings and exit 0",
    )
    p_cov.add_argument(
        "--threshold",
        type=float,
        default=None,
        help="Unified threshold for all three metrics (default None uses per-metric thresholds)",
    )
    p_cov.add_argument(
        "--req-threshold", type=float, default=0.90, help="Required information coverage threshold"
    )
    p_cov.add_argument(
        "--causal-threshold", type=float, default=0.85, help="Causal chain completeness threshold"
    )
    p_cov.add_argument(
        "--span-threshold", type=float, default=0.95, help="Source span backtrace threshold"
    )

    return parser


def _human_duration(minutes: int) -> str:
    """把分钟数格式化为人类可读时长：<1min / ~Nmin / ~Nh / ~Nd。"""
    if minutes < 1:
        return "<1min"
    if minutes < 60:
        return f"~{minutes}min"
    hours = minutes / 60
    if hours < 24:
        return f"~{round(hours)}h"
    return f"~{round(hours / 24, 1)}d"


def _print_plan(est: DensityEstimate) -> None:
    """按 PRD 样例格式打印结构化预估（对齐 core.density 的预估结果）。"""
    # Agnes 免费档在 PRD 文案里带 "Agnes 免费档 →" 前缀；openai-compat 直接给金额。
    cost_line = est.cost_label
    if "R6" in cost_line:
        cost_line = f"Agnes 免费档 → {cost_line}"
    print(f"档位 : {est.tier} {est.description}")
    print(f"预计 : {est.panels} 格 / {est.pages} 页 / {est.output_name}")
    print(f"成本 : {cost_line}")
    print(f"时长 : {_human_duration(est.estimated_minutes)}（支持断点续跑 state.json）")
    for w in est.warnings:
        print(f"提示 : {w}")


def _run_plan(args: argparse.Namespace) -> None:
    """plan 子命令：读 book → core.density.estimate → 打印（纯本地）。"""
    path = Path(args.book)
    if not path.exists():
        sys.exit(f"book file not found: {args.book}")
    try:
        est = estimate(
            str(path),
            density=args.density,
            output_format=args.format,
            api=args.api,
            concurrency=args.concurrency,
            price_per_panel=args.price_per_panel,
        )
    except ValueError as exc:
        sys.exit(f"ERROR: {exc}")
    _print_plan(est)
    print(
        "Note: uncalibrated estimate only; generate ignores --density "
        "until the density contract lands."
    )
    # PageScript 粗估（不依赖实际渲染）：以布局分页常数 4 估出分镜页数与必含信息条数。
    d2_pages = -(-est.panels // 4)  # ceil(panels / PANELS_PER_PAGE)
    print(f"PageScript 预估（原型，可选）: 信息完备分镜约 {d2_pages} 页，必含信息约 {d2_pages} 条")


def _print_coverage(report) -> None:
    """以人类可读的三行百分比 + 不达标页清单打印 CoverageReport。"""
    for label, metric in (
        ("必含信息覆盖率", report.required_coverage),
        ("因果链完整率", report.causal_coverage),
        ("原文回溯率", report.span_coverage),
    ):
        mark = "✓" if metric.passed else "✗"
        print(f"{label} : {metric.coverage_ratio:.1%} (阈值 {metric.threshold:.0%}) {mark}")
    if report.below_threshold_pages:
        print("不达标页:")
        for key in report.below_threshold_pages:
            print(f"  - {key}")
    else:
        print("全部达标。")
    if not report.overall_passed:
        print("警告: 存在未达标指标（--strict 下退出码 1）。")


def _run_coverage(args: argparse.Namespace) -> None:
    """coverage 子命令：从 <out>/state.json 收集 page_scripts，结合 source.txt
    核算三指标，落盘 coverage_report.json 并打印；--strict 不达标则退出码 1。"""
    out = Path(args.out)
    state_path = out / "state.json"
    if not state_path.exists():
        sys.exit(f"state.json 未找到：{args.out}（请先运行 generate 或指定 --out）")
    state = ProjectState.load(state_path)
    page_scripts = [
        cc.page_script for cc in state.chunk_cache.values() if cc.page_script is not None
    ]
    if not page_scripts:
        print(
            "Note: no PageScript metadata in state.json. "
            "Re-run generate with INKSTONE_PAGE_SCRIPT=1 if you need legacy "
            "PageScript fields for this prototype report."
        )
        sys.exit(0)

    src_path = Path(args.source) if args.source else (out / "source.txt")
    if not src_path.exists():
        sys.exit(f"原文未找到：{src_path}（请用 --source 指定 book.txt）")
    source_text = src_path.read_text(encoding="utf-8")

    report = compute_coverage_report(
        page_scripts,
        source_text,
        threshold=args.threshold,
        required_threshold=args.req_threshold,
        causal_threshold=args.causal_threshold,
        span_threshold=args.span_threshold,
    )

    write_coverage_report(report, out)

    if args.format == "json":
        print(report.model_dump_json(indent=2))
    else:
        _print_coverage(report)

    if args.strict and not report.overall_passed:
        sys.exit(1)


def _run_generate(args: argparse.Namespace) -> None:
    """generate 子命令：运行 core.cli_generate（实现收编于 core，任何安装方式可用）。"""
    from core.cli_generate import run_generate

    sys.exit(
        run_generate(
            source=args.source,
            out=args.out,
            fmt=args.format,
            project_id=args.project,
        )
    )


def _not_implemented(command: str, version: str) -> None:
    """identity/coverage 占位：本期(D1)未实现，给出明确提示并正常退出。"""
    print(f"子命令 `{command}` 尚未实现（{version}，本期 D1 不包含，敬请期待）。")
    sys.exit(0)


def main() -> None:
    """统一 CLI 入口。"""
    # 后向兼容：旧用法 `inkstone <source> ...` 无子命令时默认走 generate，
    # 使 scripts/start.sh 改为 `python -m core.cli "$@"` 后行为不变。
    # -h/--help 不插入，否则 `inkstone --help` 会误显示 generate 的帮助。
    first = sys.argv[1] if len(sys.argv) > 1 else ""
    if first not in ("generate", "plan", "identity", "coverage", "-h", "--help"):
        sys.argv.insert(1, "generate")

    parser = _build_parser()
    args = parser.parse_args()

    if args.command == "plan":
        _run_plan(args)
    elif args.command == "generate":
        _run_generate(args)
    elif args.command == "identity":
        _not_implemented("identity", "D3")
    elif args.command == "coverage":
        _run_coverage(args)


if __name__ == "__main__":
    main()
