"""examples.generate_comic — thin wrapper around ``core.cli_generate``.

The implementation now lives in ``core.cli_generate`` so the ``inkstone`` CLI
works from any install mode. This script is kept as a runnable demo from a
source checkout:

    export AGNES_API_KEY=sk-xxx
    python examples/generate_comic.py scene.txt --out out --format webtoon
"""

import sys
from pathlib import Path

# Allow running as a standalone script from the repo root (python examples/...).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.cli_generate import main

if __name__ == "__main__":
    main()
