"""core.cli_generate — terminal front-end for the comic generation pipeline.

This module owns the generate command's implementation (tqdm progress display,
PausedRun handling, result summary). It lives in ``core`` so the ``inkstone``
entry point works from any install mode — previously it was forwarded to
``examples/generate_comic.py``, which is not packaged, breaking non-editable
installs. ``examples/generate_comic.py`` is now a thin wrapper around this.

Usage via CLI: ``inkstone generate <source> [--out DIR] [--project ID] [--format page|webtoon]``
"""

import argparse
import asyncio
import logging
import sys
from contextlib import contextmanager
from pathlib import Path

from tqdm import tqdm

from core.api import get_chat_provider, get_image_provider
from core.comic.budget import allocate
from core.config import ImageConfig, data_dir, page_script_enabled, render_mode
from core.perf import set_safe_write
from core.pipelines.run_until_complete import PausedRun, run_until_complete

logger = logging.getLogger(__name__)

# Prefix shared by every report line so the final block reads as one section.
_REPORT_INDENT = "  "


class _TqdmLoggingHandler(logging.Handler):
    """Route warnings through tqdm.write so they sit above the live bar."""

    def __init__(self, write_fn) -> None:
        super().__init__(level=logging.WARNING)
        self._write = write_fn

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._write(self.format(record))
        except Exception:  # noqa: BLE001 — a broken report must not kill the run
            pass


def _report_block(lines: list[str]) -> None:
    """Print an aligned key/value result block (after the bar is gone)."""
    for line in lines:
        print(line)


def _result_lines(proj) -> list[str]:
    """Build the final summary as an aligned block."""
    lines = ["", "result:"]
    lines.append(f"{_REPORT_INDENT}project   : {proj.project_id}")
    lines.append(f"{_REPORT_INDENT}pages     : {len(proj.pages)}")
    if proj.pdf:
        lines.append(f"{_REPORT_INDENT}pdf       : {proj.pdf}")
    if proj.webtoon:
        lines.append(f"{_REPORT_INDENT}webtoon   : {proj.webtoon}")
    if proj.cbz:
        lines.append(f"{_REPORT_INDENT}cbz       : {proj.cbz}")
    lines.append(
        f"{_REPORT_INDENT}panels    : {len(proj.state.panels_done)} generated, "
        f"{len(proj.state.skipped)} skipped (content filter)"
    )
    if proj.state.needs_review:
        lines.append(
            f"{_REPORT_INDENT}review    : {len(proj.state.needs_review)} alias suggestion(s)"
        )
        for s in proj.state.needs_review:
            flag = "suggested" if s.suggested else "review"
            lines.append(
                f"{_REPORT_INDENT}            [{flag}] {s.new_name} -> {s.candidate} ({s.reason})"
            )
    if proj.state.stale_panels:
        lines.append(
            f"{_REPORT_INDENT}stale     : {len(proj.state.stale_panels)} panel(s) need redraw"
        )
    return lines


def _default_scene() -> Path | None:
    """Repo-bundled demo scene, when running from a source checkout.

    Returns None for installed packages (no examples/ directory on disk), in
    which case the caller must require an explicit source path.
    """
    candidate = Path(__file__).resolve().parents[1] / "examples" / "scene1.txt"
    return candidate if candidate.exists() else None


@contextmanager
def _progress_display(stream=None):
    """Dynamic bar on the bottom line; static reports are inserted above it.

    While the bar is live, ``perf`` summaries and warning logs are routed
    through ``tqdm.write`` (registered on ``core.perf``) so they never tear
    into the carriage-return redraw. ``leave=False`` clears the bar on exit.
    """
    stream = stream or sys.stderr
    root = logging.getLogger()
    pbar = tqdm(
        total=100,
        unit="%",
        bar_format="{l_bar}{bar}|",
        desc="generating comic [init]",
        file=stream,
        leave=False,
    )

    def safe_write(text: str) -> None:
        tqdm.write(text, file=stream)

    handler = _TqdmLoggingHandler(safe_write)
    handler.setFormatter(logging.Formatter("warning: %(message)s"))
    set_safe_write(safe_write)
    # Isolate root logging for the bar's lifetime: a plain StreamHandler would
    # write warnings straight to stderr and tear into the carriage-return
    # redraw. Our tqdm-aware handler is the only sink while the bar is live.
    saved_handlers = root.handlers[:]
    saved_level = root.level
    for h in saved_handlers:
        root.removeHandler(h)
    root.addHandler(handler)
    try:
        with pbar:

            def on_progress(stage: str, percent: float | None) -> None:
                pbar.set_description(f"generating comic [{stage}]")
                if percent is not None:
                    # A resumed run derives its fraction from a checkpoint; a later
                    # chunk can enlarge the canon and re-divide the portrait share,
                    # so the derived number may dip. Never rewind the bar the user
                    # is watching (the Web UI clamps the same way).
                    pbar.n = max(pbar.n, percent * 100)
                    pbar.update(0)
                pbar.refresh()

            yield on_progress
    finally:
        root.removeHandler(handler)
        for h in saved_handlers:
            root.addHandler(h)
        root.setLevel(saved_level)
        set_safe_write(None)


async def _run(
    source: str,
    out: str,
    fmt: str,
    project_id: str | None = None,
    *,
    budget: int | None = None,
    carry_over: bool = False,
    gate_yes: bool = False,
    density: str | None = None,
    harness_dir: str | None = None,
) -> None:
    text = Path(source).read_text(encoding="utf-8")
    spec = None
    if budget is not None:
        # page_script is funded only when that prototype path is on, so an
        # unused reservation does not shrink the render allowance.
        spec = allocate(
            budget,
            carry_over=carry_over,
            render_mode=render_mode(),
            page_script=page_script_enabled(),
        )

    with _progress_display() as on_progress:
        result = await run_until_complete(
            text,
            output_dir=out,
            project_id=project_id,
            output_format=fmt,
            progress_callback=on_progress,
            budget=spec,
            data_dir=str(data_dir()),
            gate_yes=gate_yes,
            density=density,
            harness_dir=harness_dir,
        )

    if isinstance(result, PausedRun):
        _report_block(
            [
                "paused:",
                f"{_REPORT_INDENT}project   : {result.project_id}",
                f"{_REPORT_INDENT}reason    : {result.reason}",
                f"{_REPORT_INDENT}saved     : {result.output_dir}",
            ]
        )
        if "continuity review" in result.reason:
            _report_block(
                [
                    f"{_REPORT_INDENT}confirm   : inkstone identity --out "
                    f"{result.output_dir} --confirm <character>",
                    f"{_REPORT_INDENT}            or click Confirm in the web UI",
                ]
            )
        elif "awaiting_human" in result.reason:
            _report_block(
                [
                    f"{_REPORT_INDENT}decide    : inkstone gate --out {result.output_dir} "
                    "--key <page> --decision accept|redraw|accept-and-flag",
                    f"{_REPORT_INDENT}            or re-run with --yes (audited)",
                ]
            )
        else:
            _report_block([f"{_REPORT_INDENT}resume    : re-run with the same --project"])
        sys.exit(2)

    _report_block(_result_lines(result))


def _configure_background_logging() -> None:
    """Warnings/errors only when not a TTY — keeps Colab status log tail readable."""
    if sys.stderr.isatty():
        return
    logging.basicConfig(
        level=logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
        force=True,
    )


def _providers_configured() -> bool:
    """Validate the selected provider pair without making an upstream request."""
    try:
        get_chat_provider()
        get_image_provider()
    except (RuntimeError, ValueError):
        return False
    return True


def _missing_credentials_message() -> str:
    provider = (ImageConfig().provider or "agnes").lower()
    if provider in ("openai_compat", "openai-compatible", "openai", "gemini"):
        return (
            "OpenAI-compat credentials are incomplete. Set OPENAI_COMPAT_BASE_URL, "
            "OPENAI_COMPAT_API_KEY, OPENAI_COMPAT_CHAT_BASE_URL, and "
            "OPENAI_COMPAT_CHAT_API_KEY (or switch PROVIDER=agnes)."
        )
    return "AGNES_API_KEY is not set. Export it (e.g. `export AGNES_API_KEY=sk-xxx`) first."


def run_generate(
    source: str | None,
    out: str | None,
    fmt: str,
    project_id: str | None,
    *,
    budget: int | None = None,
    carry_over: bool = False,
    gate_yes: bool = False,
    density: str | None = None,
    harness_dir: str | None = None,
) -> int:
    """Validate inputs and run the pipeline. Returns a process exit code."""
    if budget is not None and budget < 0:
        print("budget must be >= 0", file=sys.stderr)
        return 2
    if not _providers_configured():
        print(_missing_credentials_message(), file=sys.stderr)
        return 1

    if source is None:
        scene = _default_scene()
        if scene is None:
            print(
                "source file is required (no bundled demo scene in this install).",
                file=sys.stderr,
            )
            return 1
        source = str(scene)
    if not Path(source).exists():
        print(f"source file not found: {source}", file=sys.stderr)
        return 1

    if out is None:
        out = str(Path("comic_out") / project_id) if project_id else "comic_out"

    _configure_background_logging()
    asyncio.run(
        _run(
            source,
            out,
            fmt,
            project_id=project_id,
            budget=budget,
            carry_over=carry_over,
            gate_yes=gate_yes,
            density=density,
            harness_dir=harness_dir,
        )
    )
    return 0


def _parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate a comic from a text file (M2 pipeline).")
    p.add_argument(
        "source",
        nargs="?",
        default=None,
        help="path to the source text file (defaults to the bundled demo scene when present)",
    )
    p.add_argument(
        "--out",
        default=None,
        help="output directory (default: comic_out or comic_out/<project>)",
    )
    p.add_argument(
        "--project",
        default=None,
        help="stable project id; stores under comic_out/<id> for resume unless --out is set",
    )
    p.add_argument(
        "--format",
        choices=["page", "webtoon", "cbz"],
        default="page",
        help="page=PDF (default), cbz=zip of the pages, or vertical webtoon PNG",
    )
    p.add_argument(
        "--budget",
        type=int,
        default=None,
        help="Max billable calls this run; the run pauses when the reservation is spent",
    )
    carry = p.add_mutually_exclusive_group()
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
    p.set_defaults(carry_over=False)
    p.add_argument(
        "--yes",
        action="store_true",
        help="Render past the sample gate without human confirmation; audited into runs.jsonl",
    )
    p.add_argument(
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
    return p.parse_args(argv)


def main() -> None:
    """Standalone entry point (used by the examples/ thin wrapper)."""
    args = _parse_args()
    sys.exit(
        run_generate(
            args.source,
            args.out,
            args.format,
            args.project,
            budget=args.budget,
            carry_over=args.carry_over,
            gate_yes=args.yes,
            density=args.density,
        )
    )


if __name__ == "__main__":
    main()
