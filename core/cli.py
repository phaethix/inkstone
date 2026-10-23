"""core.cli — Inkstone 统一命令行入口。

``generate`` 跑整条管线。``plan`` 离线估计体量，不调用模型。``gate``、
``identity``、``prune``、``gc``、``verify`` 和 ``rebuild`` 是已发布的项目命令。
``coverage`` 只核对旧 PageScript 字段，不是成片质量门。``structure`` 核对
已存储的源文、节拍、对白、因果和拦截记录，缺记录记为 absent，也不是成片质量门。
``read`` 按已存页写出浏览器翻页文件，不调用模型。

``plan`` 复用 ``core.density.estimate``（内部用 ``segment_text``），给出格数、
页数、成本、时长和 webtoon 体积预警。计费后端、并发和单价由 ``--api``、
``--concurrency``、``--price-per-panel`` 显式指定。

旧用法 ``inkstone <source> ...``（无子命令）仍走 ``generate``。
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from core.comic.coverage import compute_coverage_report, write_coverage_report
from core.comic.identity import clear_tombstones
from core.comic.ledger import LEDGER_ESCALATION_THRESHOLD, ConsistencyLedger
from core.comic.prune import apply_prune, parse_older_than, plan_prune
from core.comic.reader import chapter_pages, write_reader
from core.comic.structure_report import chapter_validated, structural_report, write_structure_report
from core.density import DensityEstimate, estimate
from core.schemas import ProjectState


def _build_parser() -> argparse.ArgumentParser:
    """构造顶层解析器。"""
    parser = argparse.ArgumentParser(
        prog="inkstone",
        description="Inkstone novel-to-comic generator",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # generate：复用既有 examples/generate_comic.py，不改其逻辑。
    p_gen = sub.add_parser(
        "generate",
        help="Run the full comic generation pipeline (calls Agnes API)",
    )
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
        choices=["page", "webtoon", "cbz"],
        default="page",
        help="page=PDF / webtoon=long-strip PNG / cbz=zip of the pages",
    )
    p_gen.add_argument(
        "--budget",
        type=int,
        default=None,
        help="Max billable calls this run; the run pauses when the reservation is spent",
    )
    carry = p_gen.add_mutually_exclusive_group()
    carry.add_argument(
        "--carry-over",
        dest="carry_over",
        action="store_true",
        help="On a later calendar day, re-arm the same budget (default: do not)",
    )
    carry.add_argument(
        "--no-carry-over",
        dest="carry_over",
        action="store_false",
        help="Stop at the budget boundary and do not spend the next day's allowance (default)",
    )
    p_gen.set_defaults(carry_over=False)
    p_gen.add_argument(
        "--yes",
        action="store_true",
        help="Render past the sample gate without human confirmation; audited into runs.jsonl",
    )
    p_gen.add_argument(
        "--harness-dir",
        default=None,
        help="Run chat stages with this validated harness tree (default: builtin)",
    )
    p_gen.add_argument(
        "--density",
        choices=["A", "B", "C"],
        default=None,
        help=(
            "Panel budget for planning: A=main plot overview/主线概览, "
            "B=chapter-complete/章级完整 (default for a new project), "
            "C=near-original/近原著. Omit on resume to keep the stored tier; "
            "an explicit change replans the book."
        ),
    )

    p_gate = sub.add_parser(
        "gate",
        help="Sample-gate decisions (accept, redraw, accept-and-flag)",
    )
    p_gate.add_argument("--out", default="comic_out", help="Project output directory")
    p_gate.add_argument(
        "--project",
        default=None,
        help="Project id in the shared gate file (default: the output directory name)",
    )
    p_gate.add_argument("--key", default=None, help="Page key, e.g. c0000-p0000")
    p_gate.add_argument(
        "--decision",
        choices=["accept", "redraw", "accept-and-flag"],
        default=None,
    )
    p_gate.add_argument(
        "--reason",
        choices=["page", "bible"],
        default=None,
        help="Why a redraw was rejected: the page, or the visual bible",
    )
    p_gate.add_argument("--show", action="store_true", help="Print the stored sample and decisions")

    # plan：D1 纯本地预估（不约束 generate）。
    p_plan = sub.add_parser(
        "plan",
        help=(
            "Offline density/cost/duration estimate. Uncalibrated. "
            "generate --density uses the same tier."
        ),
    )
    p_plan.add_argument("--book", required=True, help="Novel text file path")
    p_plan.add_argument(
        "--out",
        default=None,
        help="Project directory. When it already has plans, the estimate uses those counts",
    )
    p_plan.add_argument(
        "--density",
        choices=["A", "B", "C"],
        default="B",
        help=(
            "A=main plot overview/主线概览 (sparse) "
            "B=chapter-complete/章级完整 (default) "
            "C=near-original/近原著 (dense); "
            "same tier as generate --density"
        ),
    )
    p_plan.add_argument(
        "--format",
        choices=["page", "webtoon", "cbz"],
        default="page",
        help="page=PDF / webtoon=long-strip PNG / cbz=zip of the pages",
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

    p_prompt = sub.add_parser(
        "prompt",
        help="Render the exact chat request for one stage (no model call)",
    )
    p_prompt.add_argument(
        "--stage",
        required=True,
        choices=["extract", "beats", "bible", "storyboard", "page_plan", "page_script"],
        help="Which stage's request to render",
    )
    p_prompt.add_argument("--text", default=None, help="Chunk text to render with")
    p_prompt.add_argument("--file", default=None, help="Read the chunk text from this file")
    p_prompt.add_argument("--from-run", default=None, help="Project dir for stored context")
    p_prompt.add_argument("--chunk", type=int, default=0, help="Chunk index for --from-run")
    p_prompt.add_argument("--model", default="inspect", help="Model id to fingerprint")
    p_prompt.add_argument(
        "--genre", default=None, help="Domain profile (general, wuxia); default: env"
    )
    p_prompt.add_argument(
        "--harness-dir", default=None, help="Render page_plan with this harness tree"
    )
    p_prompt.add_argument("--json", dest="as_json", action="store_true", help="Print request JSON")

    p_exp = sub.add_parser("experiment", help="Planning-only blind A/B harness probes")
    e_sub = p_exp.add_subparsers(dest="experiment_command", required=True)
    e_plan = e_sub.add_parser("plan", help="Probe base and candidate page plans")
    e_plan.add_argument("--book", required=True, help="Source text file")
    e_plan.add_argument("--out", required=True, help="Experiment directory")
    e_plan.add_argument(
        "--candidate",
        action="append",
        default=[],
        help="'base' or a harness directory (repeat for 2+); omit when using --tier",
    )
    e_plan.add_argument(
        "--tier",
        action="append",
        default=None,
        help="Density tier to probe (repeat for 2+): A, B, C. Switches the axis "
        "from harness to density; each tier uses its own budget and never clips",
    )
    e_plan.add_argument("--chunk", type=int, default=0, help="Chunk index (default 0)")
    e_plan.add_argument("--genre", default=None, help="Domain profile")
    e_plan.add_argument("--seed", default="", help="Extra anonymisation seed")
    e_plan.add_argument(
        "--shared-evidence",
        action="store_true",
        help="Run extract/beats once under the builtin harness (cheaper, page_plan-only)",
    )
    e_pick = e_sub.add_parser("pick", help="Record the human A/B choice")
    e_pick.add_argument("--run", required=True, help="Experiment directory")
    e_pick.add_argument("--choice", required=True, help="Anonymised candidate id (A/B/C)")
    e_pick.add_argument("--why", required=True, help="Why this candidate read better")
    e_seq = e_sub.add_parser("sequence", help="Sequential recursion over repeated rounds")
    s_sub = e_seq.add_subparsers(dest="sequence_command", required=True)
    s_plan = s_sub.add_parser("plan", help="Create round 1 of a sequence")
    s_plan.add_argument("--book", required=True, help="Source text file")
    s_plan.add_argument("--out", required=True, help="Sequence directory")
    s_plan.add_argument(
        "--candidate", action="append", required=True, help="Candidate harness directory"
    )
    s_plan.add_argument("--base", default=None, help="Starting checkpoint (default: base)")
    s_plan.add_argument("--rounds", type=int, default=2, help="Total rounds (default 2)")
    s_plan.add_argument("--chunk", type=int, default=0, help="Chunk index (default 0)")
    s_plan.add_argument("--genre", default=None, help="Domain profile")
    s_plan.add_argument("--seed", default="", help="Extra anonymisation seed")
    s_plan.add_argument(
        "--shared-evidence",
        action="store_true",
        help="Reuse one extract/beats pass (cheaper, page_plan-only)",
    )
    s_adv = s_sub.add_parser("advance", help="Compose the round pick and plan the next round")
    s_adv.add_argument("--run", required=True, help="Sequence directory")
    s_adv.add_argument(
        "--merge-from", action="append", default=[], help="Candidate id to merge from"
    )
    s_adv.add_argument("--component", action="append", default=[], help="Component id to merge")
    s_status = s_sub.add_parser("status", help="Show a sequence's progress")
    s_status.add_argument("--run", required=True, help="Sequence directory")

    e_compose = e_sub.add_parser("compose", help="Materialise the picked harness")
    e_compose.add_argument("--run", required=True, help="Experiment directory")
    e_compose.add_argument("--choice", required=True, help="The picked candidate id")
    e_compose.add_argument("--out", required=True, help="Output harness directory")
    e_compose.add_argument(
        "--merge-from",
        action="append",
        default=[],
        help="Candidate id or harness dir to merge components from (repeatable)",
    )
    e_compose.add_argument(
        "--component",
        action="append",
        default=[],
        help="Component id to take from --merge-from, e.g. stage.page_plan",
    )

    p_lesson = sub.add_parser("lesson", help="Approve, list, or retire craft lessons")
    l_sub = p_lesson.add_subparsers(dest="lesson_command", required=True)

    p_ladd = l_sub.add_parser("add", help="Record one human-approved lesson")
    p_ladd.add_argument("--from-run", required=True, help="Project directory with state.json")
    p_ladd.add_argument(
        "--page",
        action="append",
        default=[],
        help="Page key evidence, e.g. c0000-p0000 (repeatable)",
    )
    p_ladd.add_argument(
        "--decision",
        required=True,
        choices=["redraw", "accept_and_flag", "alias_merge", "alias_dismiss", "continuity_confirm"],
        help="The human decision this lesson comes from",
    )
    p_ladd.add_argument("--reason-tier", default=None, help="Gate reason tier, e.g. layout/bible")
    p_ladd.add_argument("--cue", required=True, help="The situation shape this lesson applies to")
    p_ladd.add_argument("--lesson", required=True, help="The transferable craft operation")
    p_ladd.add_argument("--confidence", default="medium", choices=["low", "medium", "high"])
    p_ladd.add_argument(
        "--applies-to",
        default="page_plan",
        choices=["page_plan", "storyboard", "bible", "system"],
    )
    p_ladd.add_argument("--genre", default=None, help="Defaults to the run's review manifest")
    p_ladd.add_argument("--id", default=None, help="Override the generated lesson id")
    p_ladd.add_argument("--proposed", action="store_true", help="Store as proposed, not active")
    p_ladd.add_argument(
        "--no-entry",
        action="store_true",
        help="Decline to record a lesson (NO_ENTRY); exits 0 without writing",
    )

    p_llist = l_sub.add_parser("list", help="List lessons")
    p_llist.add_argument("--genre", default=None)
    p_llist.add_argument("--all", action="store_true", help="Include proposed and retired")

    p_lret = l_sub.add_parser("retire", help="Retire a lesson by id")
    p_lret.add_argument("id")

    p_harness = sub.add_parser("harness", help="Inspect and validate prompt-harness components")
    h_sub = p_harness.add_subparsers(dest="harness_command", required=True)
    h_validate = h_sub.add_parser("validate", help="Validate every component in one pass")
    h_validate.add_argument("--dir", default=None, help="Validate this harness tree")
    h_list = h_sub.add_parser("list", help="List components and genres")
    h_list.add_argument("--dir", default=None, help="List this harness tree")

    p_id = sub.add_parser("identity", help="Identity ledger: view characters and pending pages")
    p_id.add_argument("--view", action="store_true", help="Print the consistency ledger")
    p_id.add_argument("--merge", default=None, help="Merge alias: 'new:keep'")
    p_id.add_argument(
        "--confirm",
        default=None,
        help="Confirm a continuity review: raise a character's reviewed version",
    )
    p_id.add_argument(
        "--page",
        action="append",
        default=None,
        help="Confirm only these positional page keys (repeatable)",
    )
    p_id.add_argument(
        "--out",
        default="comic_out",
        help="Generation output directory (contains consistency.json; default comic_out)",
    )

    # prune：最小回收器（§7 阶段 0g）。默认 dry-run，删除需显式 --apply。
    p_prune = sub.add_parser(
        "prune",
        help="Delete unreferenced generated assets older than a threshold (dry-run by default)",
    )
    p_prune.add_argument(
        "--out",
        default="comic_out",
        help="Generation output directory (contains state.json; default comic_out)",
    )
    p_prune.add_argument(
        "--older-than",
        required=True,
        help="Minimum age of a reclaimable object: e.g. 7d (days), 12h (hours), or a bare integer",
    )
    p_prune.add_argument(
        "--apply",
        action="store_true",
        help="Actually delete; omit for a dry-run report",
    )

    p_gc = sub.add_parser(
        "gc",
        help="Delete unreferenced cas objects older than a threshold (dry-run by default)",
    )
    p_gc.add_argument(
        "--out",
        default="comic_out",
        help="Project directory (contains cas/; default comic_out)",
    )
    p_gc.add_argument(
        "--older-than",
        required=True,
        help="Minimum age of a reclaimable object: e.g. 7d (days), 12h (hours), or a bare integer",
    )
    p_gc.add_argument(
        "--apply",
        action="store_true",
        help="Actually delete; omit for a dry-run report",
    )

    p_stages = sub.add_parser(
        "stages",
        help="List the declarative stage catalog",
    )
    p_stages.add_argument("--json", action="store_true", help="Print JSON")

    p_mig = sub.add_parser(
        "migrate",
        help="Check or apply manifest schema upgrades",
    )
    p_mig.add_argument(
        "--out",
        default="comic_out",
        help="Project directory that contains index/ (default comic_out)",
    )
    p_mig.add_argument(
        "--apply",
        action="store_true",
        help="Rewrite clean manifests; default is a dry run",
    )
    p_mig.add_argument(
        "--check",
        action="store_true",
        help="Forecast the billable cost of a re-run after a prompt or harness change",
    )
    p_mig.add_argument("--json", action="store_true", help="Print a JSON report")

    p_verify = sub.add_parser(
        "verify",
        help="Check that index manifests and cas objects agree",
    )
    p_verify.add_argument(
        "--out",
        default="comic_out",
        help="Project directory that contains index/ and cas/ (default comic_out)",
    )

    p_struct = sub.add_parser(
        "structure",
        help="Check stored source, beats, lettering, order, and blocks. Not a quality gate.",
    )
    p_struct.add_argument(
        "--out",
        default="comic_out",
        help="Project directory (contains state.json; default comic_out)",
    )
    p_struct.add_argument(
        "--source",
        default=None,
        help="Original text path; defaults to <out>/source.txt",
    )
    p_struct.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format: text or json (default text)",
    )
    p_struct.add_argument(
        "--strict",
        action="store_true",
        help="Exit code 1 if any check fails. An absent check does not fail",
    )

    p_read = sub.add_parser(
        "read",
        help="Write a browser reader for stored page images. Does not call a model.",
    )
    p_read.add_argument(
        "--out",
        default="comic_out",
        help="Project directory (contains pages/; default comic_out)",
    )

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
        help=(
            "Output format: text=human-readable three metrics / "
            "json=machine-readable (default text)"
        ),
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

    # rebuild：清除墓碑（§7）。render 清 state.json 里的页级墓碑；
    # letter、export、layout 清清单里的非 ok 记录。其余 stage 显式拒绝。
    p_rb = sub.add_parser(
        "rebuild",
        help="Force regeneration and clear tombstones for a stage subtree.",
    )
    p_rb.add_argument(
        "--out",
        default="comic_out",
        help="Generation output directory (contains state.json; default comic_out)",
    )
    p_rb.add_argument(
        "--stage",
        default="render",
        help="Stage to rebuild: render, letter, export, or layout (default render)",
    )
    p_rb.add_argument(
        "--key",
        action="append",
        default=[],
        help="Page state key to release, e.g. c0000-p0000 (repeatable)",
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
    """Print a structured density estimate (experimental / uncalibrated)."""
    if est.from_stored_plans:
        print("[experimental] inkstone plan — panel and page counts from the stored project")
    else:
        print("[experimental] inkstone plan — uncalibrated density estimate")
    # Agnes free-tier copy prefixes "Agnes 免费档 →"; openai-compat shows a price.
    cost_line = est.cost_label
    if "R6" in cost_line:
        cost_line = f"Agnes 免费档 → {cost_line}"
    print(f"档位 : {est.tier} {est.description}")
    if est.panels_low is not None and est.panels_high is not None:
        # The estimate is a prediction with a known error, so show the band it
        # came with rather than the point in the middle of it.
        print(
            f"预计 : {est.panels_low}–{est.panels_high} 格 / "
            f"{est.pages_low}–{est.pages_high} 页 / {est.output_name}"
            f"（预测 {est.panels} 格 / {est.pages} 页，区间为实测误差）"
        )
    else:
        print(f"预计 : {est.panels} 格 / {est.pages} 页 / {est.output_name}")
    print(f"成本 : {cost_line}")
    print(f"时长 : {_human_duration(est.estimated_minutes)}（支持断点续跑 state.json）")
    for w in est.warnings:
        print(f"提示 : {w}")


def _planned_counts(state: ProjectState) -> tuple[list[int], list[int]]:
    """Per-chunk panel and page counts already stored on a project.

    The two lists are aligned by chunk: index ``i`` of each describes the same
    chunk. A chunk that was skipped keeps its slot rather than being dropped, so
    a gap in ``page_cache`` cannot slide a later chunk's pages onto an earlier
    chunk's panels. Callers that only want the measured pairs have to skip the
    chunks whose page count is zero.
    """
    keys = set(state.page_cache) | {
        key for key, cache in state.chunk_cache.items() if cache.storyboard is not None
    }

    def _sort_key(key: str) -> tuple[int, str]:
        return (int(key), "") if str(key).isdigit() else (10**9, str(key))

    # Index by chunk so both lists share a slot for every chunk we know about.
    by_chunk: dict[str, tuple[int, int]] = {}
    for key in sorted(keys, key=_sort_key):
        pageset = state.page_cache.get(key)
        if pageset is not None and pageset.pages:
            by_chunk[key] = (
                sum(len(page.panels) for page in pageset.pages),
                len(pageset.pages),
            )
            continue
        cache = state.chunk_cache.get(key)
        if cache is not None and cache.storyboard is not None:
            by_chunk[key] = (len(cache.storyboard.panels), 0)

    ordered = [by_chunk[key] for key in sorted(by_chunk, key=_sort_key)]
    panels = [panel_count for panel_count, _ in ordered]
    pages = [page_count for _, page_count in ordered]
    # A run stopped between chunks has pages for the chunks it planned and none
    # for the rest. The measured pages are evidence, so they are kept: the gap is
    # filled from their average by ``_scale_observed``. Only a project with no
    # measured page at all falls back to the rate.
    return panels, pages


def _run_plan(args: argparse.Namespace) -> None:
    """plan 子命令：读 book → core.density.estimate → 打印（纯本地）。"""
    path = Path(args.book)
    if not path.exists():
        sys.exit(f"book file not found: {args.book}")
    observed_panels = None
    observed_pages = None
    out = getattr(args, "out", None)
    if out:
        state_path = Path(out) / "state.json"
        if state_path.is_file():
            planned_panels, planned_pages = _planned_counts(ProjectState.load(state_path))
            observed_panels = planned_panels or None
            observed_pages = planned_pages or None
        else:
            print(f"Note: {state_path} 不存在，格数仍按经验档位。")
    try:
        est = estimate(
            str(path),
            density=args.density,
            output_format=args.format,
            api=args.api,
            concurrency=args.concurrency,
            price_per_panel=args.price_per_panel,
            observed_panels=observed_panels,
            observed_pages=observed_pages,
        )
    except ValueError as exc:
        sys.exit(f"ERROR: {exc}")
    _print_plan(est)
    if est.from_stored_plans:
        print(
            "Note: panel and page counts come from the stored project. "
            "The tier constants stay the experience rate. "
            "inkstone generate --density uses this tier and replans when it changes."
        )
    else:
        print(
            "Note: uncalibrated estimate. "
            "inkstone generate --density uses this tier and replans when it changes."
        )
    print(
        "PageScript（原型，默认关闭）: 设置 INKSTONE_PAGE_SCRIPT=1 才会写入。"
        "inkstone coverage 只核对那些字段，不是成片质量门。"
    )


def _print_coverage(report) -> None:
    """以人类可读的三行百分比 + 不达标页清单打印 CoverageReport。"""
    print("PageScript prototype report. These ratios are not a readability or quality gate.")
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


def _print_structure(report, *, validated: bool) -> None:
    """Print the five structural checks. Absent is shown, not treated as a pass."""
    print("Structural record check. These checks are not a readability or quality gate.")
    for check in report.checks:
        print(f"{check.name} : {check.status} — {check.detail}")
    if all(check.status == "pass" for check in report.checks):
        print("全部通过。")
    elif report.ok:
        print("没有失败项。缺记录的检查记为 absent，不是通过。")
    else:
        print("警告: 存在失败项（--strict 下退出码 1）。")
    print("chapter : validated" if validated else "chapter : not validated")


def _run_structure(args: argparse.Namespace) -> None:
    """Read state.json and report five structural checks. No model call."""
    import json

    out = Path(args.out)
    state_path = out / "state.json"
    if not state_path.exists():
        sys.exit(f"state.json 未找到：{args.out}（请先运行 generate 或指定 --out）")
    state = ProjectState.load(state_path)
    source_path = Path(args.source) if getattr(args, "source", None) else out / "source.txt"
    source = source_path.read_text(encoding="utf-8") if source_path.is_file() else ""
    report = structural_report(state, source)
    validated = chapter_validated(report, state)
    write_structure_report(report, out, state=state)
    if args.format == "json":
        payload = report.to_json()
        payload["chapter_validated"] = validated
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        _print_structure(report, validated=validated)
    if args.strict and not report.ok:
        sys.exit(1)


def _run_read(args: argparse.Namespace) -> int:
    """List stored pages by chunk id and write ``reader.html``."""
    project = Path(args.out)
    try:
        written = write_reader(project)
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 1
    for label, paths in chapter_pages(project / "pages"):
        print(label)
        for path in paths:
            print(f"  {path.relative_to(project).as_posix()}")
    print(f"reader -> {written}")
    return 0


def _run_identity(args: argparse.Namespace) -> int:
    """identity 子命令：打印一致性账本（§12），或确认一个角色的待复核页（§4, 5b）。"""
    out = Path(args.out)
    state_path = out / "state.json"
    if not state_path.exists():
        print(f"state.json 未找到：{args.out}（请先运行 generate 或指定 --out）")
        return 1
    state = ProjectState.load(state_path)
    ledger = ConsistencyLedger.load_or_rebuild(out / "consistency.json", state)

    if args.confirm:
        name = args.confirm
        if name not in ledger.characters:
            print(f"未找到角色 {name}：无法确认复核。")
            return 1
        changed = ledger.confirm_review(name, pages=getattr(args, "page", None))
        if not changed:
            print(f"没有可确认的页面：{name} 的待复核页已清空或指定页面不在其中。")
            return 0
        ledger.save(out / "consistency.json")
        entry = ledger.characters[name]
        version = entry.reference.version
        remaining = len(entry.pending_pages)
        if remaining:
            print(f"已确认 {name} 的部分复核：仍待复核 {remaining} 页（已复核版本 v{version}）。")
        else:
            print(f"已确认 {name} 的复核：待复核页已清空（已复核版本升至 v{version}）。")
        return 0

    if not ledger.characters:
        print("账本为空：尚无可显示的角色页集。")
        return 0
    print(f"一致性账本：{len(ledger.characters)} 个角色")
    for name in sorted(ledger.characters):
        entry = ledger.characters[name]
        pending = len(entry.pending_pages)
        ref = entry.reference.version
        note = ""
        if pending > LEDGER_ESCALATION_THRESHOLD:
            note = "（超过升级阈值：下次运行将在渲染前暂停待确认）"
        print(f"  - {name}: {len(entry.pages)} 页，参考版本 v{ref}，待复核 {pending} 页{note}")
    return 0


def _run_rebuild_manifests(args: argparse.Namespace) -> int:
    """Drop non-ok letter or export manifests so the next run re-attempts them."""
    from core.comic.cas import release_tombstones

    out = Path(args.out)
    keys = list(args.key)
    released = release_tombstones(out, stage=args.stage, keys=keys or None)
    missing = [key for key in keys if key not in released]
    for key in released:
        print(f"已释放墓碑 {key}：下次运行将重做该步。")
    for key in missing:
        print(f"未找到墓碑 {key}：未做任何修改。")
    print(f"rebuild 完成：释放 {len(released)} 个墓碑。")
    return 1 if missing else 0


def _run_rebuild(args: argparse.Namespace) -> int:
    """rebuild 子命令：清除指定 stage 的墓碑，使其在下次运行时重做（§7）。

    render 的墓碑在 state.json。letter、export、layout 的墓碑是非 ok 清单。
    其余 stage 显式拒绝，否则用户会以为该步已释放。
    """
    if args.stage in {"letter", "export", "layout"}:
        return _run_rebuild_manifests(args)
    if args.stage != "render":
        print(
            f"rebuild：stage '{args.stage}' 尚无墓碑存储，当前支持 render、letter、export、layout。"
        )
        return 1

    out = Path(args.out)
    state_path = out / "state.json"
    if not state_path.exists():
        print(f"state.json 未找到：{args.out}（请先运行 generate 或指定 --out）")
        return 1

    state = ProjectState.load(state_path)
    keys = list(args.key)
    # §7: --stage alone targets the whole stage subtree; --key narrows it.
    if not keys:
        keys = list(state.tombstones)

    released = clear_tombstones(state, keys)
    missing = [k for k in keys if k not in released]
    state.save(state_path)

    for key in released:
        print(f"已释放墓碑 {key}：下次运行将重绘该页。")
    for key in missing:
        print(f"未找到墓碑 {key}：未做任何修改。")
    print(f"rebuild 完成：释放 {len(released)} 个墓碑。")
    return 1 if missing else 0


def _run_prune(args: argparse.Namespace) -> int:
    """prune 子命令：删除未被引用且超过存活时长的生成资产（§7 阶段 0g）。

    默认 dry-run，只有显式 --apply 才删除；删除条件是引用计数为零 **且**
    存活时长超过 --older-than（§13 已定第 3 条），两者缺一不可。
    """
    try:
        older_than = parse_older_than(args.older_than)
    except ValueError as exc:
        print(f"prune：{exc}")
        return 2

    out = Path(args.out)
    try:
        plan = plan_prune(out, older_than)
    except FileNotFoundError:
        print(f"state.json 未找到：{args.out}（请先运行 generate 或指定 --out）")
        return 1

    if not plan:
        print("没有可回收的资产：未被引用且超过存活时长的文件为零。")
        return 0

    for candidate in plan.candidates:
        print(f"  - {candidate.path}（{candidate.size_bytes} 字节）")
    if not args.apply:
        print(
            f"dry-run：将回收 {len(plan)} 个文件，共 {plan.total_bytes} 字节。加 --apply 才会删除。"
        )
        return 0

    result = apply_prune(plan)
    print(f"已回收 {result.deleted} 个文件，共 {result.reclaimed_bytes} 字节。")
    return 0


def _run_generate(args: argparse.Namespace) -> None:
    """generate 子命令：运行 core.cli_generate（实现收编于 core，任何安装方式可用）。"""
    from core.cli_generate import run_generate

    sys.exit(
        run_generate(
            source=args.source,
            out=args.out,
            fmt=args.format,
            project_id=args.project,
            budget=args.budget,
            carry_over=args.carry_over,
            gate_yes=args.yes,
            density=args.density,
            harness_dir=args.harness_dir,
        )
    )


def _chapter_pages(state: ProjectState | None) -> int | None:
    """Largest planned chapter, so a grown sample stops at one chapter."""
    if state is None:
        return None
    counts = [len(plan.pages) for plan in state.page_cache.values() if plan.pages]
    if not counts:
        return None
    return max(counts)


def _run_gate(args: argparse.Namespace) -> int:
    """Record or print one project's sample-gate decisions."""
    from core.comic.gate import GateSession
    from core.config import data_dir

    out = Path(args.out)
    project_id = args.project or out.name
    session = GateSession.load(
        data_dir=data_dir(),
        output_dir=out,
        project_id=project_id,
    )
    if session is None:
        print(f"no sample gate for project {project_id}")
        return 1
    if args.decision:
        if not args.key:
            print("gate --decision requires --key", file=sys.stderr)
            return 2
        state = None
        state_path = out / "state.json"
        if state_path.is_file():
            state = ProjectState.load(state_path)
        try:
            session.record(
                args.key,
                args.decision,
                reason_tier=args.reason,
                state=state,
                chapter_pages=_chapter_pages(state),
            )
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        if state is not None:
            state.save(state_path)
    project = session.project
    print(
        f"sample {len(project.sample_keys)}/{project.sample_size}"
        f" released={project.released} yes={project.yes}"
    )
    if project.paused_key:
        print(f"paused at {project.paused_key}")
    for key in project.sample_keys:
        decision = project.decisions.get(key)
        label = decision.choice if decision is not None else "undecided"
        tier = f" ({decision.reason_tier})" if decision is not None and decision.reason_tier else ""
        print(f"  {key}: {label}{tier}")
    return 0


def _run_gc(args: argparse.Namespace) -> int:
    """gc 子命令：回收未被清单、账本或闸门引用且超过存活时长的 cas 对象。"""
    from core.comic.gc import GcError, apply_gc, plan_gc
    from core.comic.prune import parse_older_than
    from core.config import data_dir

    try:
        older_than = parse_older_than(args.older_than)
    except ValueError as exc:
        print(f"gc：{exc}")
        return 2

    shared = data_dir() / "gate.json"
    try:
        plan = plan_gc(
            Path(args.out),
            older_than,
            gate_files=[shared] if shared.is_file() else None,
        )
    except GcError as exc:
        print(f"gc：{exc}")
        return 1

    if not plan:
        print("没有可回收的对象：未被引用且超过存活时长的文件为零。")
        return 0

    for candidate in plan.candidates:
        print(f"  - {candidate.path}（{candidate.size_bytes} 字节）")
    if not args.apply:
        print(
            f"dry-run：将回收 {len(plan)} 个对象，共 {plan.total_bytes} 字节。加 --apply 才会删除。"
        )
        return 0

    result = apply_gc(plan)
    print(f"已回收 {result.deleted} 个对象，共 {result.reclaimed_bytes} 字节。")
    return 0


def _run_stages(args: argparse.Namespace) -> int:
    """stages 子命令：打印声明式阶段目录。"""
    import json as _json

    from core.pipelines.stage_registry import all_specs, validate_stage_registry

    problems = validate_stage_registry()
    rows = [
        {
            "stage": spec.name,
            "phase": spec.phase.value,
            "contract": spec.contract_stage,
            "manifest_stage": spec.manifest_stage,
        }
        for spec in all_specs()
    ]
    if args.json:
        print(_json.dumps({"stages": rows, "problems": problems}, ensure_ascii=False, indent=2))
    else:
        print(f"{'stage':<16} {'phase':<8} {'contract':<16} manifest")
        for row in rows:
            print(
                f"{row['stage']:<16} {row['phase']:<8} "
                f"{row['contract']:<16} {row['manifest_stage']}"
            )
        if problems:
            print("problems:")
            for problem in problems:
                print(f"  {problem}")
    return 1 if problems else 0


def _run_migrate_check(root: Path, *, as_json: bool = False) -> int:
    """Print what a re-run would re-pay for, without calling any provider.

    This answers the two questions a prompt edit raises, and keeps them apart
    because they have different costs: a chat stage whose ``stage_src`` changed
    re-calls once per stored manifest, while page art repaints only when the new
    plan bytes actually differ (the render key does not carry the plan key).
    """
    from core.comic.migrate import check_recompute

    if not (root / "state.json").exists() and not (root / "index").is_dir():
        if as_json:
            print(json.dumps({"out": str(root), "error": "no project"}, ensure_ascii=False))
        else:
            print(f"{root}: 没有 state.json 也没有 index/，不是项目目录。")
        return 1

    forecast = check_recompute(root)
    if as_json:
        print(
            json.dumps(
                {
                    "out": str(root),
                    "chat_calls": forecast.chat_calls,
                    "local_redos": forecast.local_redos,
                    "repaints": forecast.repaints,
                    "pages": forecast.pages,
                    "pages_reusable": forecast.pages_reusable,
                    "unattributed": forecast.unattributed,
                    "stages": [
                        {
                            "stage": check.stage,
                            "current": check.current,
                            "stale": check.stale,
                            "missing": check.missing,
                        }
                        for check in forecast.stages
                        if check.current or check.stale or check.missing
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    print(f"{root}: 重算预言")
    for check in forecast.stages:
        if not (check.current or check.stale or check.missing):
            continue
        print(
            f"  {check.stage}: 可复用 {check.current}，失效 {check.stale}，未记录 {check.missing}"
        )
    if forecast.unattributed:
        # A stage outside the forecast's table cannot be proven reusable, so it
        # must not vanish from the report.
        print(f"  未能归属的阶段（未计入预测）：{', '.join(sorted(forecast.unattributed))}")
    print(f"聊天调用：{forecast.chat_calls} 次")
    if forecast.local_redos:
        print(f"本地重做：{forecast.local_redos} 个（不消耗配额）")
    print(
        f"页面：{forecast.pages} 页中 {forecast.pages_reusable} 页可直接复用，"
        f"最多重绘 {forecast.repaints} 页"
    )
    print("页面是否重绘取决于新计划的字节是否变化（render key 不含上游 plan key）。")
    return 0


def _run_migrate(args: argparse.Namespace) -> int:
    """migrate 子命令：检查或重写 manifest 到当前 schema（§6 升级路径）。"""
    from core.comic.migrate import apply_migrations, scan_manifests
    from core.pipelines.stage_registry import manifest_stages

    root = Path(args.out)
    if getattr(args, "check", False):
        return _run_migrate_check(root, as_json=args.json)
    report = scan_manifests(root, allowed_stages=manifest_stages())
    if not report.manifests:
        if args.json:
            print(json.dumps({"out": str(root), "manifests": 0, "changed": [], "problems": []}))
        else:
            print(f"{args.out}: 没有 manifest。")
        return 0
    changed = report.changed
    problems = report.problems
    if args.json:
        print(
            json.dumps(
                {
                    "out": str(root),
                    "manifests": len(report.manifests),
                    "changed": [item.path.name for item in changed],
                    "problems": [
                        {"file": item.path.name, "problems": item.problems} for item in problems
                    ],
                    "written": apply_migrations(report) if args.apply and changed else 0,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1 if problems else 0
    print(
        f"{args.out}: {len(report.manifests)} manifest，"
        f"{len(changed)} 个需要升级，{len(problems)} 个有问题。"
    )
    for item in changed:
        print(f"  upgrade {item.path.name}")
    for item in problems:
        print(f"  PROBLEM {item.path.name}: {'; '.join(item.problems)}")
    if args.apply and changed:
        written = apply_migrations(report)
        print(f"已重写 {written} 个 manifest。")
    elif changed:
        print("dry run：加 --apply 才会重写。")
    return 1 if problems else 0


def _run_verify(args: argparse.Namespace) -> int:
    """Report whether index/ and cas/ describe the same bytes, and whether the
    finished-page projection agrees with the checkpoint.

    The second half is read-only and no-provider. It answers a different
    question from the CAS check: not "are the bytes the ones we recorded" but
    "do the three records of a finished page still say the same thing".
    """
    from core.comic.artifact_projection import check_page_projection
    from core.comic.cas import verify
    from core.pipelines.stage_registry import manifest_stages
    from core.schemas import ProjectState

    root = Path(args.out)
    problems = verify(root, allowed_stages=manifest_stages())

    state_path = root / "state.json"
    if not state_path.is_file():
        print("page projection: absent — no state.json", file=sys.stderr)
    else:
        # ``caches=False``: these fields live in state.json, and a corrupt
        # cache.json must not stop a consistency check (it only costs a re-plan).
        try:
            state = ProjectState.load(state_path, caches=False)
        except (OSError, ValueError) as exc:
            problems.append(f"page projection: state.json unreadable ({exc})")
        else:
            for check in check_page_projection(state):
                if check.status == "mismatch":
                    problems.append(f"{check.name}: {check.detail}")
                elif check.status == "absent":
                    # Nothing to compare is reported, never counted as a pass.
                    print(f"{check.name}: absent — {check.detail}", file=sys.stderr)

    if problems:
        for problem in problems:
            print(problem, file=sys.stderr)
        return 1
    print("ok")
    return 0


def _load_prompt_context(args: argparse.Namespace):
    """Load the optional stored context for ``inkstone prompt``.

    Returns ``(chunk, elements, beats, bible, board, characters, recent_layouts,
    density)``. Missing state is not an error: the command still renders the
    stage template, and ``density`` falls back to the tier a new project gets.
    """
    from core.comic.adaptation import key_beats_for_chunk
    from core.comic.identity import page_state_key  # noqa: F401  (kept for parity)
    from core.comic.segmentation import segment_text
    from core.schemas import ProjectState, StoryElements

    chunk = args.text or ""
    if args.file:
        chunk = Path(args.file).read_text(encoding="utf-8")
    elements = StoryElements()
    beats = None
    bible = None
    board = None
    characters: dict = {}
    recent_layouts: list[str] = []
    # The budget sentence follows the tier, so the stored tier is part of the
    # request this command promises to reproduce. Default only when there is no
    # checkpoint to read one from.
    density = "B"
    if not args.from_run:
        return chunk, elements, beats, bible, board, characters, recent_layouts, density

    out = Path(args.from_run)
    state = ProjectState.load(out / "state.json")
    density = state.density
    cached = state.chunk_cache.get(str(args.chunk))
    if cached is not None:
        elements = cached.elements or elements
        board = cached.storyboard
        beats = cached.beats
    bible = state.visual_bible
    characters = state.characters
    if not chunk:
        units = state.source_units
        if units:
            chunk = "".join(unit.text for unit in units)
    from core.pipelines.creative_comic import _recent_layout_intents

    recent_layouts = _recent_layout_intents(state)
    if beats is None and chunk:
        beats = key_beats_for_chunk(state, str(args.chunk), segment_text(chunk))
    return chunk, elements, beats, bible, board, characters, recent_layouts, density


def _run_prompt(args: argparse.Namespace) -> int:
    """Print the exact request one stage would send. Never calls a provider."""
    from core.density import clips_plan, get_density_plan
    from core.screenwriter import (
        build_beats_request,
        build_bible_request,
        build_extract_request,
        build_page_plan_request,
        build_page_script_request,
        build_storyboard_request,
    )

    candidate = None
    if args.harness_dir:
        from core.harness.spec import HarnessError, harness_from_dir

        try:
            candidate = harness_from_dir(args.harness_dir)
        except HarnessError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        problems = candidate.validate()
        if problems:
            for problem in problems:
                print(problem, file=sys.stderr)
            return 1
    if args.from_run and not (Path(args.from_run) / "state.json").is_file():
        print(f"no state.json under {args.from_run}", file=sys.stderr)
        return 1
    if args.genre:
        from core.harness.spec import available_genres

        genres = candidate.available_genres if candidate is not None else available_genres()
        if args.genre not in genres:
            print(
                f"unknown genre {args.genre!r}; available: {', '.join(genres)}",
                file=sys.stderr,
            )
            return 1
    chunk, elements, beats, bible, board, characters, recent_layouts, density = (
        _load_prompt_context(args)
    )
    # Reproduction has to use the tier's real budget and the behaviour the
    # pipeline applies to it: only A clips, B and C keep the plan.
    density_plan = get_density_plan(density)
    model = args.model
    if args.stage == "extract":
        request = build_extract_request(chunk, harness=candidate, model=model)
    elif args.stage == "beats":
        request = build_beats_request(chunk, elements, harness=candidate, model=model)
    elif args.stage == "bible":
        request = build_bible_request(
            chunk,
            characters,
            bible,
            alias_hints=(),
            preferred_style="",
            harness=candidate,
            model=model,
        )
    elif args.stage == "storyboard":
        request = build_storyboard_request(
            chunk,
            elements,
            key_beats=beats,
            panel_budget=density_plan.panels_per_chunk,
            enforce_budget=clips_plan(density_plan.tier),
            tier=density_plan.tier,
            genre=args.genre,
            harness=candidate,
            model=model,
        )
    elif args.stage == "page_plan":
        from core.config import genre as config_genre
        from core.config import lessons_enabled
        from core.lessons import LessonStore, format_craft_notes

        craft_notes = ""
        if lessons_enabled():
            craft_notes = format_craft_notes(
                LessonStore().retrieve(
                    genre=args.genre or config_genre(),
                    query=chunk,
                    applies_to="page_plan",
                )
            )
        request = build_page_plan_request(
            chunk,
            elements,
            recent_layouts=recent_layouts,
            visual_bible=bible,
            key_beats=beats,
            panel_budget=density_plan.panels_per_chunk,
            enforce_budget=clips_plan(density_plan.tier),
            tier=density_plan.tier,
            genre=args.genre,
            craft_notes=craft_notes,
            harness=candidate,
            model=model,
        )
    elif args.stage == "page_script":
        if board is None:
            print("page_script needs a stored storyboard; pass --from-run", file=sys.stderr)
            return 1
        request = build_page_script_request(board, elements, chunk, harness=candidate, model=model)
    else:  # pragma: no cover - argparse restricts the choices
        raise ValueError(args.stage)

    if args.as_json:
        payload = {
            "stage": args.stage,
            "digest": request.digest(),
            "model": request.model,
            "messages": request.messages,
            "tools": request.tools,
            "tool_choice": request.tool_choice,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    tool = request.tool_choice.get("function", {}).get("name", "?")
    print(f"stage: {args.stage}")
    print(f"digest: {request.digest()}")
    print(f"model: {request.model}")
    print(f"tool: {tool}")
    for message in request.messages:
        print(f"--- {message.get('role', '?')} ---")
        print(message.get("content", ""))
    return 0


def _run_harness(args: argparse.Namespace) -> int:
    """Validate or list a prompt-harness tree (builtin unless ``--dir``)."""
    from core.harness.spec import HarnessError, builtin_harness, harness_from_dir

    try:
        harness = harness_from_dir(args.dir) if args.dir else builtin_harness()
    except HarnessError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if args.harness_command == "list":
        print(f"harness digest: {harness.digest()}")
        for component in harness.components:
            print(f"  {component.id:18} {component.kind:9} {component.path}")
        print(f"genres: {', '.join(harness.available_genres)}")
        return 0
    try:
        problems = harness.validate()
    except HarnessError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if problems:
        for problem in problems:
            print(problem, file=sys.stderr)
        return 1
    print(f"ok {harness.digest()}")
    return 0


def _run_lesson(args: argparse.Namespace) -> int:
    """Approve, list, or retire craft lessons. Only a human writes a lesson."""
    from core.config import data_dir as config_data_dir
    from core.config import genre as config_genre
    from core.harness.spec import harness_digest
    from core.lessons import LessonError, LessonStore

    store = LessonStore(config_data_dir())
    if args.lesson_command == "list":
        try:
            lessons = store.list(genre=args.genre, include_inactive=args.all)
        except LessonError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        for lesson in lessons:
            print(
                f"{lesson.id}  {lesson.genre:8}  {lesson.status:8}  "
                f"{lesson.confidence:6}  {lesson.cue}"
            )
        if not lessons:
            print("no lessons")
        return 0
    if args.lesson_command == "retire":
        try:
            lesson = store.retire(args.id)
        except LessonError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        print(f"retired {lesson.id} ({lesson.genre})")
        return 0

    if args.no_entry:
        print("NO_ENTRY: nothing recorded")
        return 0
    run = Path(args.from_run)
    state_path = run / "state.json"
    if not state_path.is_file():
        print(f"no state.json under {run}", file=sys.stderr)
        return 2
    state = ProjectState.load(state_path)
    known = set(state.pages_done) | set(state.stale_pages) | set(state.generated.pages)
    pages = [page for page in args.page if page]
    if args.decision in {"redraw", "accept_and_flag"} and not pages:
        print(f"--decision {args.decision} needs --page", file=sys.stderr)
        return 2
    for page in pages:
        if page not in known:
            print(f"page {page} is not recorded in {run}", file=sys.stderr)
            return 2
        if args.decision == "redraw" and page not in state.stale_pages:
            print(f"page {page} is not marked stale by a redraw", file=sys.stderr)
            return 2
        if args.decision == "accept_and_flag" and page not in state.pages_done:
            print(f"page {page} was not completed", file=sys.stderr)
            return 2

    manifest: dict = {}
    manifest_path = run / "review" / "manifest.json"
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            manifest = {}
    try:
        lesson = store.add(
            cue=args.cue,
            lesson=args.lesson,
            run=state.project_id or run.name,
            decision=args.decision,
            pages=pages,
            reason_tier=args.reason_tier,
            confidence=args.confidence,
            applies_to=args.applies_to,
            genre=args.genre or manifest.get("genre") or config_genre(),
            harness=manifest.get("harness_digest") or harness_digest(),
            proposed=args.proposed,
            lesson_id=args.id,
        )
    except (LessonError, ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"added {lesson.id} ({lesson.genre}) -> {store.path_for(lesson.genre, lesson.id)}")
    return 0


def _run_experiment(args: argparse.Namespace) -> int:
    """Plan a blind A/B probe or record its human choice."""
    from core.experiment import (
        ExperimentError,
        advance_sequence_async,
        compose_harness,
        pick_experiment,
        plan_experiment,
        plan_sequence_async,
        sequence_status,
    )

    try:
        if args.experiment_command == "sequence":
            if args.sequence_command == "status":
                state = sequence_status(args.run)
                print(f"status: {state['status']}")
                print(
                    f"round: {state['current_round']}/{state['rounds']}"
                    f" · completed: {state['completed_rounds']}"
                )
                print(f"checkpoint: {state['current_checkpoint']}")
                print(f"run: {state['run']}")
                return 0
            if args.sequence_command == "advance":
                state = asyncio.run(
                    advance_sequence_async(
                        args.run,
                        merge_from=args.merge_from,
                        components=args.component,
                    )
                )
                print(
                    f"advanced to round {state['current_round']}/{state['rounds']}"
                    f" · status: {state['status']}"
                )
                print(f"checkpoint: {state['current_checkpoint']}")
                if state["status"] != "done":
                    print(
                        "next: inkstone experiment pick --run "
                        f"{args.run}/rounds/{state['current_round']} "
                        '--choice A --why "..."'
                    )
                return 0
            source = Path(args.book).read_text(encoding="utf-8")
            state = asyncio.run(
                plan_sequence_async(
                    source,
                    out_dir=args.out,
                    candidates=args.candidate,
                    base=args.base,
                    rounds=args.rounds,
                    chunk_index=args.chunk,
                    genre=args.genre,
                    seed=args.seed,
                    shared_evidence=args.shared_evidence,
                )
            )
            print(f"wrote {args.out} ({state['rounds']} round(s))")
            print(
                f'next: inkstone experiment pick --run {args.out}/rounds/1 --choice A --why "..."'
            )
            return 0
        if args.experiment_command == "compose":
            record = compose_harness(
                args.run,
                choice=args.choice,
                out_dir=args.out,
                merge_from=args.merge_from,
                components=args.component,
            )
            print(
                f"composed {args.out} -> {record['harness_digest']} "
                f"(merged: {', '.join(record['components']) or 'none'})"
            )
            return 0
        if args.experiment_command == "pick":
            selection = pick_experiment(args.run, choice=args.choice, why=args.why)
            print(
                f"selected {selection['chosen']} -> {selection['chosen_label']}"
                f" ({selection['why']})"
            )
            return 0
        source = Path(args.book).read_text(encoding="utf-8")
        manifest = asyncio.run(
            plan_experiment(
                source,
                out_dir=args.out,
                candidates=args.candidate,
                tiers=args.tier,
                chunk_index=args.chunk,
                genre=args.genre,
                seed=args.seed,
                shared_evidence=args.shared_evidence,
            )
        )
    except (ExperimentError, OSError) as exc:
        print(f"experiment failed: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - a provider failure is a CLI error
        print(f"experiment failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    ids = ", ".join(sorted(manifest["candidates"]))
    cost = manifest["cost"]
    print(f"wrote {args.out}")
    print(f"candidates: {ids}")
    print(f"cost: {cost['chat_calls']} chat calls, {cost['image_calls']} image calls")
    print(f'next: inkstone experiment pick --run {args.out} --choice A --why "..."')
    return 0


def main() -> None:
    """统一 CLI 入口。"""
    # 后向兼容：旧用法 `inkstone <source> ...` 无子命令时默认走 generate，
    # 使 scripts/start.sh 改为 `python -m core.cli "$@"` 后行为不变。
    # -h/--help 不插入，否则 `inkstone --help` 会误显示 generate 的帮助。
    first = sys.argv[1] if len(sys.argv) > 1 else ""
    if first not in (
        "generate",
        "gate",
        "plan",
        "identity",
        "coverage",
        "structure",
        "read",
        "rebuild",
        "prune",
        "gc",
        "verify",
        "migrate",
        "stages",
        "prompt",
        "harness",
        "lesson",
        "experiment",
        "-h",
        "--help",
    ):
        sys.argv.insert(1, "generate")

    parser = _build_parser()
    args = parser.parse_args()

    if args.command == "plan":
        _run_plan(args)
    elif args.command == "generate":
        _run_generate(args)
    elif args.command == "gate":
        sys.exit(_run_gate(args))
    elif args.command == "identity":
        sys.exit(_run_identity(args))
    elif args.command == "coverage":
        _run_coverage(args)
    elif args.command == "structure":
        _run_structure(args)
    elif args.command == "read":
        sys.exit(_run_read(args))
    elif args.command == "rebuild":
        sys.exit(_run_rebuild(args))
    elif args.command == "prune":
        sys.exit(_run_prune(args))
    elif args.command == "gc":
        sys.exit(_run_gc(args))
    elif args.command == "verify":
        sys.exit(_run_verify(args))
    elif args.command == "migrate":
        sys.exit(_run_migrate(args))
    elif args.command == "stages":
        sys.exit(_run_stages(args))
    elif args.command == "prompt":
        sys.exit(_run_prompt(args))
    elif args.command == "harness":
        sys.exit(_run_harness(args))
    elif args.command == "lesson":
        sys.exit(_run_lesson(args))
    elif args.command == "experiment":
        sys.exit(_run_experiment(args))


if __name__ == "__main__":
    main()
