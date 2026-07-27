#!/usr/bin/env bash
# Offline showcase helper: density plan for Journey to the West ch.1 excerpt.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BOOK="$ROOT/examples/showcase/journey-west-ch1/source.txt"

if [[ ! -f "$BOOK" ]]; then
  echo "ERROR: missing $BOOK" >&2
  exit 1
fi

if command -v inkstone >/dev/null 2>&1; then
  inkstone plan --book "$BOOK" --density B --format page
else
  python -m core.cli plan --book "$BOOK" --density B --format page
fi

cat <<'EOF'

Next (needs AGNES_API_KEY):
  python examples/generate_comic.py \
    examples/showcase/journey-west-ch1/source.txt \
    --project journey-west-ch1 --format page --out comic_out/journey-west-ch1

Or Colab:
  ./scripts/colab_run.sh run examples/showcase/journey-west-ch1/source.txt \
    --project journey-west-ch1 --format page
EOF
