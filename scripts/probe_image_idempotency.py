"""Probe whether the image provider honours request identity (design §13 item 27).

Costs exactly two image calls. Run it once and record the verdict in
CHANGELOG.md; if the bytes are identical, §8's duplicate-charge window can be
closed outright.

    .venv/bin/python scripts/probe_image_idempotency.py

Requires a configured provider (``AGNES_API_KEY``, or ``PROVIDER=openai_compat``
plus its base URL and key).
"""

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.api import get_image_provider  # noqa: E402
from core.api.idempotency_probe import probe_image_idempotency  # noqa: E402
from core.config import finished_page_size  # noqa: E402

PROBE_PROMPT = "a bespectacled cat, ink-wash manhua style, single character sheet"


async def main() -> None:
    provider = get_image_provider()
    size = finished_page_size()
    print(f"probing {type(provider).__name__} at {size} with two identical calls")

    with tempfile.TemporaryDirectory() as workdir:
        verdict = await probe_image_idempotency(
            provider, prompt=PROBE_PROMPT, size=size, workdir=Path(workdir)
        )

    print(f"  first  : {verdict.digest_a} ({verdict.size_a} bytes)")
    print(f"  second : {verdict.digest_b} ({verdict.size_b} bytes)")
    print(f"  verdict: {verdict.summary()}")


if __name__ == "__main__":
    asyncio.run(main())
