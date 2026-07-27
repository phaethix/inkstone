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
from core.config import ImageConfig
from core.pipelines.run_until_complete import PausedRun, run_until_complete

logger = logging.getLogger(__name__)


def _default_scene() -> Path | None:
    """Repo-bundled demo scene, when running from a source checkout.

    Returns None for installed packages (no examples/ directory on disk), in
    which case the caller must require an explicit source path.
    """
    candidate = Path(__file__).resolve().parents[1] / "examples" / "scene1.txt"
    return candidate if candidate.exists() else None


@contextmanager
def _progress_display(stream=None):
    """Always use tqdm (including nohup/Colab) so status log tail shows % + stage."""
    stream = stream or sys.stderr
    with tqdm(
        total=100,
        unit="%",
        bar_format="{l_bar}{bar}| [{postfix}]",
        desc="generating comic",
        file=stream,
    ) as pbar:

        def on_progress(stage: str, percent: float | None) -> None:
            pbar.set_postfix_str(stage, refresh=False)
            if percent is not None:
                pbar.n = percent * 100
                pbar.update(0)
            pbar.refresh()

        yield on_progress


async def _run(source: str, out: str, fmt: str, project_id: str | None = None) -> None:
    text = Path(source).read_text(encoding="utf-8")

    with _progress_display() as on_progress:
        result = await run_until_complete(
            text,
            output_dir=out,
            project_id=project_id,
            output_format=fmt,
            progress_callback=on_progress,
        )

    if isinstance(result, PausedRun):
        print(f"PAUSED project {result.project_id}: {result.reason}")
        print(f"  progress saved under {result.output_dir}")
        print("  re-run with the same --project to continue")
        sys.exit(2)

    proj = result
    print(f"project {proj.project_id}: {len(proj.pages)} page(s)")
    if proj.pdf:
        print(f"  PDF     -> {proj.pdf}")
    if proj.webtoon:
        print(f"  WEBTOON -> {proj.webtoon}")
    print(
        f"  panels  -> {len(proj.state.panels_done)} generated, "
        f"{len(proj.state.skipped)} skipped (content filter)"
    )
    if proj.state.needs_review:
        print(f"  review  -> {len(proj.state.needs_review)} alias suggestion(s):")
        for s in proj.state.needs_review:
            flag = "suggested" if s.suggested else "review"
            print(f"    [{flag}] {s.new_name} -> {s.candidate} ({s.reason})")
    if proj.state.stale_panels:
        print(f"  stale   -> {len(proj.state.stale_panels)} panel(s) need redraw")


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
) -> int:
    """Validate inputs and run the pipeline. Returns a process exit code."""
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
    asyncio.run(_run(source, out, fmt, project_id=project_id))
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
        choices=["page", "webtoon"],
        default="page",
        help="output format: flip-page PDF (default) or vertical webtoon PNG",
    )
    return p.parse_args(argv)


def main() -> None:
    """Standalone entry point (used by the examples/ thin wrapper)."""
    args = _parse_args()
    sys.exit(run_generate(args.source, args.out, args.format, args.project))


if __name__ == "__main__":
    main()
