"""examples.generate_comic — end-to-end M2 comic generation demo.

Runs the full pipeline (segment -> extract -> merge characters -> portraits ->
storyboard -> per-panel generate -> layout -> export) on a novel/scene text file
and writes the artifacts (panels, portraits, state.json, and a PDF or webtoon
PNG) into an output directory.

This script talks to the real upstream Agnes API, so it needs ``AGNES_API_KEY``
in the environment and is meant to be run **manually** — it is intentionally NOT
part of the pytest suite.

Usage:
    export AGNES_API_KEY=sk-xxx
    python examples/generate_comic.py                 # uses examples/scene1.txt
    python examples/generate_comic.py scene.txt --out out --format webtoon
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

from tqdm import tqdm

# Allow running as a standalone script from the repo root (python examples/...).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.pipelines.creative_comic import creative_comic

_DEFAULT_SCENE = Path(__file__).resolve().parent / "scene1.txt"


async def _run(source: str, out: str, fmt: str, project_id: str | None = None) -> None:
    text = Path(source).read_text(encoding="utf-8")

    with tqdm(
        total=100,
        unit="%",
        bar_format="{l_bar}{bar}| {n:3.0f}% [{postfix}]",
        desc="generating comic",
    ) as pbar:

        def _on_progress(stage: str, percent: float | None) -> None:
            pbar.set_postfix(stage=stage)
            if percent is not None:
                pbar.n = percent * 100
                pbar.update(0)
            pbar.refresh()

        proj = await creative_comic(
            text,
            output_dir=out,
            project_id=project_id,
            output_format=fmt,
            progress_callback=_on_progress,
        )

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


def _parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate a comic from a text file (M2 pipeline).")
    p.add_argument(
        "source",
        nargs="?",
        default=str(_DEFAULT_SCENE),
        help="path to the source text file (defaults to examples/scene1.txt)",
    )
    p.add_argument("--out", default=None, help="output directory (default: comic_out or comic_out/<project>)")
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
    args = _parse_args()
    if not os.environ.get("AGNES_API_KEY"):
        sys.exit("AGNES_API_KEY is not set. Export it (e.g. `export AGNES_API_KEY=sk-xxx`) first.")
    if not Path(args.source).exists():
        sys.exit(f"source file not found: {args.source}")
    out = args.out
    if out is None:
        out = str(Path("comic_out") / args.project) if args.project else "comic_out"
    asyncio.run(_run(args.source, out, args.format, project_id=args.project))


if __name__ == "__main__":
    main()
