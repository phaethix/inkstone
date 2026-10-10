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
from core.comic.identity import clear_tombstones
from core.comic.ledger import ConsistencyLedger
from core.comic.prune import apply_prune, parse_older_than, plan_prune
from core.density import DensityEstimate, estimate
from core.schemas import ProjectState


def _build_parser() -> argparse.ArgumentParser:
    """构造带 4 个子命令的顶层解析器。"""
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
        choices=["page", "webtoon"],
        default="page",
        help="page=vertical PDF / webtoon=long-strip PNG",
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
    p_id = sub.add_parser("identity", help="Identity ledger: view characters and pending pages")
    p_id.add_argument("--view", action="store_true", help="Print the consistency ledger")
    p_id.add_argument("--merge", default=None, help="Merge alias: 'new:keep'")
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

    p_verify = sub.add_parser(
        "verify",
        help="Check that index manifests and cas objects agree",
    )
    p_verify.add_argument(
        "--out",
        default="comic_out",
        help="Project directory that contains index/ and cas/ (default comic_out)",
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
    print("[experimental] inkstone plan — density estimate (does not control generate)")
    # Agnes free-tier copy prefixes "Agnes 免费档 →"; openai-compat shows a price.
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
    state.migrate_legacy_page_keys()
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


def _run_identity(args: argparse.Namespace) -> int:
    """identity 子命令：打印一致性账本（§12）。"""
    out = Path(args.out)
    state_path = out / "state.json"
    if not state_path.exists():
        print(f"state.json 未找到：{args.out}（请先运行 generate 或指定 --out）")
        return 1
    state = ProjectState.load(state_path)
    state.migrate_legacy_page_keys()
    ledger = ConsistencyLedger.load_or_rebuild(out / "consistency.json", state)
    if not ledger.characters:
        print("账本为空：尚无可显示的角色页集。")
        return 0
    print(f"一致性账本：{len(ledger.characters)} 个角色")
    for name in sorted(ledger.characters):
        entry = ledger.characters[name]
        pending = len(entry.pending_pages)
        ref = entry.reference.version
        print(f"  - {name}: {len(entry.pages)} 页，参考版本 v{ref}，待复核 {pending} 页")
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
    state.migrate_legacy_page_keys()
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


def _not_implemented(command: str, version: str) -> None:
    """identity/coverage 占位：本期(D1)未实现，给出明确提示并正常退出。"""
    print(f"子命令 `{command}` 尚未实现（{version}，本期 D1 不包含，敬请期待）。")
    sys.exit(0)


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


def _run_verify(args: argparse.Namespace) -> int:
    """Report whether index/ and cas/ describe the same bytes."""
    from core.comic.cas import verify

    problems = verify(Path(args.out))
    if problems:
        for problem in problems:
            print(problem, file=sys.stderr)
        return 1
    print("ok")
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
        "rebuild",
        "prune",
        "gc",
        "verify",
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
    elif args.command == "rebuild":
        sys.exit(_run_rebuild(args))
    elif args.command == "prune":
        sys.exit(_run_prune(args))
    elif args.command == "gc":
        sys.exit(_run_gc(args))
    elif args.command == "verify":
        sys.exit(_run_verify(args))


if __name__ == "__main__":
    main()
