#!/usr/bin/env bash
# Inkstone one-click launcher.
#
# Sets up a local virtualenv (unless you are already inside a venv/conda env),
# installs the runtime + dev dependencies, loads your AGNES_API_KEY from .env,
# and runs the end-to-end comic generator on a txt novel.
#
# Usage (from repo root):
#   ./scripts/start.sh                              # examples/scene1.txt -> comic_out
#   ./scripts/start.sh my_novel.txt --out out --format webtoon
#   ./scripts/start.sh examples/sample_novel.txt --format webtoon
#
# Conda users: `conda activate inkstone` first, then run this script — it reuses that env.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# Reuse the active environment instead of nesting a venv inside one.
if [ -z "${VIRTUAL_ENV:-}" ] && [ -z "${CONDA_DEFAULT_ENV:-}" ]; then
  if [ ! -d .venv ]; then
    python3 -m venv .venv
  fi
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

python -m pip install -q -U pip
python -m pip install -e ".[dev]"

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

exec python examples/generate_comic.py "$@"
