#!/usr/bin/env bash
# Inkstone one-click launcher.
#
# Uses uv to ensure dependencies are installed, loads your AGNES_API_KEY from .env,
# and runs the end-to-end comic generator on a txt novel.
#
# Usage (from repo root):
#   ./scripts/start.sh                              # examples/scene1.txt -> comic_out
#   ./scripts/start.sh my_novel.txt --out out --format webtoon
#   ./scripts/start.sh examples/sample_novel.txt --format webtoon
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if ! command -v uv >/dev/null 2>&1; then
  echo "ERROR: uv is required. Install from https://docs.astral.sh/uv/" >&2
  exit 1
fi

uv sync --extra dev --quiet

# Load AGNES_API_KEY from .env if it isn't already exported.
if [ -z "${AGNES_API_KEY:-}" ] && [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

if [ -z "${AGNES_API_KEY:-}" ]; then
  echo "ERROR: AGNES_API_KEY is not set. Put it in .env (AGNES_API_KEY=sk-xxx) or export it." >&2
  exit 1
fi

# Delegate to the unified CLI entry point (backward compatible: a bare source
# path is treated as `generate`). `inkstone plan` also works here, though it
# still requires AGNES_API_KEY because this launcher guards on it above.
exec uv run python -m core.cli "$@"
