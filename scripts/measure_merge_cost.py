"""Measure what one typical alias merge costs today (design §9 phase 0h).

Zero-quota: the pipeline is driven with counting fake providers, so this script
never calls a live API. Run it from the repo root:

    .venv/bin/python scripts/measure_merge_cost.py

It prints the row that belongs in the §9 threshold table's "current
architecture" column. Commit that value to the table and to CHANGELOG.md; an
empty column is equivalent to having no threshold at all (§13 resolved item 26).
"""

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.comic.merge_cost_probe import (  # noqa: E402
    GROWTH_CHUNKS,
    SAMPLE_AFFECTED_CHUNKS,
    SAMPLE_AFFECTED_PAGES_PER_CHUNK,
    SAMPLE_CHUNKS,
    SAMPLE_PAGES_PER_CHUNK,
    measure_merge_cost,
)


async def main() -> None:
    with tempfile.TemporaryDirectory() as workdir:
        root = Path(workdir)
        report = await measure_merge_cost(root / "base")
        grown = await measure_merge_cost(root / "grown", chunks=GROWTH_CHUNKS)

    print("alias-merge cost under the current architecture")
    print(
        f"  sample           : {report.pages} pages, "
        f"{report.affected_pages} affected "
        f"({SAMPLE_CHUNKS} chunks x {SAMPLE_PAGES_PER_CHUNK} pages; "
        f"alias on {SAMPLE_AFFECTED_PAGES_PER_CHUNK} pages of "
        f"chunks {list(SAMPLE_AFFECTED_CHUNKS)})"
    )
    print(f"  stale page keys  : {len(report.merged_keys)}")
    print(f"  build complete   : {report.build_was_complete} ({report.pages_completed} pages)")
    print(f"  chat calls       : {report.chat_calls}")
    print(f"  image calls      : {report.image_calls}")
    print(f"  total calls      : {report.total_calls}")
    print()
    print(
        "| Item | Current architecture | Migration target |\n"
        f"| Image calls (render) | {report.image_calls} | <= 17 |\n"
        f"| Chat calls (extract / plan) | {report.chat_calls} | <= 3 |\n"
        f"| Growth with book length | {grown.pages} pages, alias still on "
        f"{grown.affected_pages}: {len(grown.merged_keys)} stale, "
        f"{grown.image_calls} image, {grown.chat_calls} chat | O(affected pages) |"
    )


if __name__ == "__main__":
    asyncio.run(main())
