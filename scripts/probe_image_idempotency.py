"""Probe whether the image provider honours request identity (design §13 item 27).

Costs exactly two image calls. Run it once and record the verdict in
CHANGELOG.md; if the bytes are identical, §8's duplicate-charge window can be
closed outright.

    .venv/bin/python scripts/probe_image_idempotency.py

Requires a configured provider (``AGNES_API_KEY``, or ``PROVIDER=openai_compat``
plus its base URL and key).
"""

import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# Minimal .env loader (no python-dotenv dependency), matching probe_m2.py.
def _load_dotenv(path: str = ".env") -> None:
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip().strip('"').strip("'")
            os.environ.setdefault(key, val)


_load_dotenv()

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
