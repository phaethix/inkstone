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

# Allow running as a standalone script from the repo root (python examples/...).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.pipelines.creative_comic import creative_comic

_DEFAULT_SCENE = Path(__file__).resolve().parent / "scene1.txt"


async def _run(source: str, out: str, fmt: str) -> None:
    text = Path(source).read_text(encoding="utf-8")
    proj = await creative_comic(text, output_dir=out, output_format=fmt)
    print(f"project {proj.project_id}: {len(proj.pages)} page(s)")
    if proj.pdf:
        print(f"  PDF     -> {proj.pdf}")
    if proj.webtoon:
        print(f"  WEBTOON -> {proj.webtoon}")
    print(
        f"  panels  -> {len(proj.state.panels_done)} generated, "
        f"{len(proj.state.skipped)} skipped (content filter)"
    )


def _parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate a comic from a text file (M2 pipeline).")
    p.add_argument(
        "source",
        nargs="?",
        default=str(_DEFAULT_SCENE),
        help="path to the source text file (defaults to examples/scene1.txt)",
    )
    p.add_argument("--out", default="comic_out", help="output directory")
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
    asyncio.run(_run(args.source, args.out, args.format))


if __name__ == "__main__":
    main()
