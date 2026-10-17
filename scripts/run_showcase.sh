#!/usr/bin/env bash
# Offline showcase helper: density plan for Journey to the West ch.1 excerpt.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BOOK="$ROOT/examples/showcase/journey-west-ch1/source.txt"
PROJECT="${INKSTONE_SHOWCASE_OUT:-$ROOT/comic_out/journey-west-ch1}"

if [[ ! -f "$BOOK" ]]; then
  echo "ERROR: missing $BOOK" >&2
  exit 1
fi

plan_args=(plan --book "$BOOK" --density B --format page)
if [[ -f "$PROJECT/state.json" ]]; then
  plan_args+=(--out "$PROJECT")
fi

if command -v inkstone >/dev/null 2>&1; then
  inkstone "${plan_args[@]}"
  if [[ -f "$PROJECT/state.json" ]]; then
    inkstone structure --out "$PROJECT"
  fi
else
  python -m core.cli "${plan_args[@]}"
  if [[ -f "$PROJECT/state.json" ]]; then
    python -m core.cli structure --out "$PROJECT"
  fi
fi

cat <<'EOF'

Next (needs AGNES_API_KEY):
  python examples/generate_comic.py \
    examples/showcase/journey-west-ch1/source.txt \
    --project journey-west-ch1 --format page --out comic_out/journey-west-ch1

Or Colab:
  ./scripts/colab_run.sh run examples/showcase/journey-west-ch1/source.txt \
    --project journey-west-ch1 --format page

`--format cbz` zips the stored pages and writes a page count.
After pages exist, these read the project and do not call a model:
  inkstone structure --out comic_out/journey-west-ch1
  inkstone read --out comic_out/journey-west-ch1
A local project at that path passes every structural check and has 8 stored pages.
The PDF stays under comic_out/ and is not committed.
EOF
