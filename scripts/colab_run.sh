#!/usr/bin/env bash
# Inkstone + Google Colab CLI — run long comic jobs remotely so you can close
# the laptop.
#
# Prerequisites:
#   - `colab` on PATH (e.g. `conda activate google-colab`)
#   - AGNES_API_KEY in the environment or in repo-root `.env`
#   - Authenticated Colab CLI (`colab sessions` works)
#
# Usage (from repo root):
#   ./scripts/colab_run.sh new
#   ./scripts/colab_run.sh bootstrap              # clone GitHub on the VM
#   ./scripts/colab_run.sh bootstrap --from-local # upload this checkout as a tarball
#   ./scripts/colab_run.sh run novel.txt --project my-novel --format webtoon
#   ./scripts/colab_run.sh status
#   ./scripts/colab_run.sh logs
#   ./scripts/colab_run.sh restart
#   ./scripts/colab_run.sh download --project my-novel
#   ./scripts/colab_run.sh stop
#
# See docs/colab-cli.md for details and limitations.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

SESSION="${INKSTONE_COLAB_SESSION:-inkstone}"
REMOTE_ROOT="${INKSTONE_COLAB_REMOTE_ROOT:-/content/inkstone}"
REMOTE_KEY="${INKSTONE_COLAB_KEY_PATH:-/content/AGNES_API_KEY}"
REMOTE_LOG="${INKSTONE_COLAB_LOG:-/content/inkstone_run.log}"
REMOTE_PID="${INKSTONE_COLAB_PID:-/content/inkstone_run.pid}"
REPO_URL="${INKSTONE_COLAB_REPO:-https://github.com/phaethix/inkstone.git}"
BRANCH="${INKSTONE_COLAB_BRANCH:-main}"

usage() {
  awk '
    NR == 1 { next }
    /^#/ {
      sub(/^# ?/, "")
      print
      next
    }
    { exit }
  ' "$0"
  exit "${1:-0}"
}

need_colab() {
  if ! command -v colab >/dev/null 2>&1; then
    echo "ERROR: \`colab\` not found on PATH." >&2
    echo "Install Google Colab CLI and activate its env, e.g.:" >&2
    echo "  conda activate google-colab" >&2
    echo "Docs: https://github.com/googlecolab/google-colab-cli" >&2
    exit 1
  fi
}

load_key() {
  if [ -z "${AGNES_API_KEY:-}" ] && [ -f "$ROOT/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    source "$ROOT/.env"
    set +a
  fi
  if [ -z "${AGNES_API_KEY:-}" ]; then
    echo "ERROR: AGNES_API_KEY is not set. Put it in .env or export it." >&2
    exit 1
  fi
}

# Embed a local value as a shell-quoted literal in remote scripts. Expansion
# happens here before colab_sh/colab_py — not on the VM. (Writing '$var' inside
# the double-quoted colab_sh argument also expands locally; remote_q is explicit
# and safe for paths containing spaces or shell metacharacters.)
remote_q() {
  python3 -c 'import shlex,sys; print(shlex.quote(sys.argv[1]))' "$1"
}

# Embed a local value as a Python string literal for colab_py snippets.
py_str() {
  python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$1"
}

# Run Python on the Colab session via stdin. Default timeout is only 30s —
# long Inkstone jobs must be started with nohup (see cmd_run), not foreground exec.
colab_py() {
  local timeout="${1:-60}"
  shift
  local code="$1"
  printf '%s\n' "$code" | colab exec -s "$SESSION" --timeout "$timeout"
}

# Run a shell snippet on the Colab VM via Python subprocess (macOS bash 3.2 safe).
colab_sh() {
  local timeout="${1:-60}"
  shift
  local script="$1"
  local quoted
  quoted="$(printf '%s' "$script" | python3 -c 'import sys; print(repr(sys.stdin.read()))')"
  colab_py "$timeout" "import subprocess
r = subprocess.run($quoted, shell=True)
if r.returncode:
    raise RuntimeError(f'remote shell failed with exit {r.returncode}')
"
}

cmd_new() {
  need_colab
  echo "[colab] Creating CPU session '$SESSION' (Inkstone does not need a GPU)..."
  colab new -s "$SESSION"
  echo "[colab] Session ready. Next: ./scripts/colab_run.sh bootstrap"
}

cmd_bootstrap() {
  need_colab
  load_key
  local from_local=0
  while [ $# -gt 0 ]; do
    case "$1" in
      --from-local) from_local=1; shift ;;
      -h|--help) usage 0 ;;
      *) echo "Unknown bootstrap option: $1" >&2; usage 1 ;;
    esac
  done

  echo "[colab] Writing AGNES_API_KEY to $REMOTE_KEY on session '$SESSION'..."
  local keyfile
  keyfile="$(mktemp)"
  printf '%s' "$AGNES_API_KEY" >"$keyfile"
  colab upload -s "$SESSION" "$keyfile" "$REMOTE_KEY"
  rm -f "$keyfile"

  if [ "$from_local" -eq 1 ]; then
    local tarball
    tarball="$(mktemp -t inkstone-src.XXXXXX.tgz)"
    echo "[colab] Packing local checkout -> $tarball"
    tar -C "$ROOT" -czf "$tarball" \
      --exclude='.git' \
      --exclude='.venv' \
      --exclude='__pycache__' \
      --exclude='*.pyc' \
      --exclude='comic_out' \
      --exclude='logs' \
      --exclude='.DS_Store' \
      --exclude='.workbuddy' \
      .
    echo "[colab] Uploading source tarball..."
    colab upload -s "$SESSION" "$tarball" /content/inkstone-src.tgz
    rm -f "$tarball"
    colab_sh 300 "
set -e
rm -rf $(remote_q "$REMOTE_ROOT")
mkdir -p $(remote_q "$REMOTE_ROOT")
tar -xzf /content/inkstone-src.tgz -C $(remote_q "$REMOTE_ROOT")
cd $(remote_q "$REMOTE_ROOT")
python -m pip install -q -U pip
python -m pip install -q -e '.[dev]'
python -c 'import core, json_repair; print(\"bootstrap ok\", core.__file__)'
"
  else
    echo "[colab] Cloning $REPO_URL ($BRANCH) on the VM..."
    colab_sh 300 "
set -e
rm -rf $(remote_q "$REMOTE_ROOT")
git clone --depth 1 --branch $(remote_q "$BRANCH") $(remote_q "$REPO_URL") $(remote_q "$REMOTE_ROOT")
cd $(remote_q "$REMOTE_ROOT")
python -m pip install -q -U pip
python -m pip install -q -e '.[dev]'
python -c 'import core, json_repair; print(\"bootstrap ok\", core.__file__)'
"
  fi
  echo "[colab] Bootstrap done. Next: ./scripts/colab_run.sh run <novel.txt> --project <id>"
}

cmd_run() {
  need_colab
  local source="" project="" format="webtoon"
  while [ $# -gt 0 ]; do
    case "$1" in
      --project) project="${2:-}"; shift 2 ;;
      --format) format="${2:-}"; shift 2 ;;
      -h|--help) usage 0 ;;
      --*) echo "Unknown run option: $1" >&2; usage 1 ;;
      *)
        if [ -z "$source" ]; then source="$1"; shift
        else echo "Unexpected arg: $1" >&2; usage 1
        fi
        ;;
    esac
  done
  if [ -z "$source" ] || [ -z "$project" ]; then
    echo "Usage: ./scripts/colab_run.sh run <novel.txt> --project <id> [--format webtoon|page]" >&2
    exit 1
  fi
  if [ ! -f "$source" ]; then
    echo "ERROR: source not found: $source" >&2
    exit 1
  fi
  case "$format" in
    webtoon|page) ;;
    *) echo "ERROR: --format must be webtoon or page" >&2; exit 1 ;;
  esac

  local remote_novel="$REMOTE_ROOT/novel.txt"
  local remote_out="$REMOTE_ROOT/comic_out/$project"
  echo "[colab] Uploading novel -> $remote_novel"
  colab upload -s "$SESSION" "$source" "$remote_novel"

  echo "[colab] Starting background generate_comic (project=$project format=$format)..."
  # Start nohup quickly; do not keep the local CLI attached to the long job.
  colab_sh 120 "
set -e
cd $(remote_q "$REMOTE_ROOT")
test -f $(remote_q "$REMOTE_KEY") || { echo 'missing API key on VM; run bootstrap'; exit 1; }
export AGNES_API_KEY=\$(cat $(remote_q "$REMOTE_KEY"))
mkdir -p $(remote_q "$remote_out")
if [ -f $(remote_q "$REMOTE_PID") ] && kill -0 \"\$(cat $(remote_q "$REMOTE_PID"))\" 2>/dev/null; then
  echo \"stopping previous pid \$(cat $(remote_q "$REMOTE_PID"))\"
  kill \"\$(cat $(remote_q "$REMOTE_PID"))\" || true
  sleep 1
fi
nohup python examples/generate_comic.py $(remote_q "$remote_novel") \
  --project $(remote_q "$project") \
  --format $(remote_q "$format") \
  --out $(remote_q "$remote_out") \
  > $(remote_q "$REMOTE_LOG") 2>&1 &
echo \$! > $(remote_q "$REMOTE_PID")
echo started pid=\$(cat $(remote_q "$REMOTE_PID")) out=$(remote_q "$remote_out")
"
  echo "[colab] Job started in the background on session '$SESSION'."
  echo "  You can close the laptop. Check later with:"
  echo "    ./scripts/colab_run.sh status"
  echo "    ./scripts/colab_run.sh logs"
  echo "  When finished:"
  echo "    ./scripts/colab_run.sh download --project $project"
}

cmd_status() {
  need_colab
  colab status -s "$SESSION" || true
  if ! colab_py 90 "
import os
from pathlib import Path

root = Path($(py_str "$REMOTE_ROOT"))
pid_path = Path($(py_str "$REMOTE_PID"))
log_path = Path($(py_str "$REMOTE_LOG"))
print('inkstone checkout:', root.is_dir())
print('api key file:', Path($(py_str "$REMOTE_KEY")).is_file())
if pid_path.is_file():
    pid = int(pid_path.read_text().strip())
    try:
        os.kill(pid, 0)
        print(f'background job: running pid={pid}')
    except OSError:
        print(f'background job: pid file present but not running (pid={pid})')
else:
    print('background job: not started (no pid file)')
if log_path.is_file():
    print('log tail:', log_path)
    lines = log_path.read_text(encoding='utf-8', errors='replace').splitlines()
    for line in lines[-5:]:
        print('  ', line)
else:
    print('log: none yet')
panels_root = root / 'comic_out'
if panels_root.is_dir():
    for panels in sorted(panels_root.glob('*/panels')):
        n = sum(1 for p in panels.iterdir() if p.is_file())
        print(f'{panels}: {n} panel file(s)')
else:
    print('comic_out: none yet')
"; then
    echo "[colab] Remote exec failed (kernel busy or timed out)." >&2
    echo "  Try: ./scripts/colab_run.sh restart" >&2
    echo "  Then: ./scripts/colab_run.sh status" >&2
    return 1
  fi
}

cmd_restart() {
  need_colab
  echo "[colab] Restarting kernel for session '$SESSION'..."
  colab restart-kernel -s "$SESSION"
}

cmd_logs() {
  need_colab
  local lines="${1:-80}"
  colab_sh 60 "tail -n $lines $(remote_q "$REMOTE_LOG") 2>/dev/null || echo no log yet: $(remote_q "$REMOTE_LOG")"
}

cmd_download() {
  need_colab
  local project=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --project) project="${2:-}"; shift 2 ;;
      -h|--help) usage 0 ;;
      *) echo "Unknown download option: $1" >&2; usage 1 ;;
    esac
  done
  if [ -z "$project" ]; then
    echo "Usage: ./scripts/colab_run.sh download --project <id>" >&2
    exit 1
  fi
  local remote_out="$REMOTE_ROOT/comic_out/$project"
  local remote_tar="/content/${project}.tgz"
  local local_dir="$ROOT/comic_out/$project"
  local local_tar="$ROOT/comic_out/${project}.tgz"

  echo "[colab] Packing remote $remote_out ..."
  colab_sh 300 "
set -e
test -d $(remote_q "$remote_out") || { echo 'missing remote project dir'; exit 1; }
tar -czf $(remote_q "$remote_tar") -C $(remote_q "$REMOTE_ROOT/comic_out") $(remote_q "$project")
ls -lh $(remote_q "$remote_tar")
"
  mkdir -p "$ROOT/comic_out"
  echo "[colab] Downloading -> $local_tar"
  colab download -s "$SESSION" "$remote_tar" "$local_tar"
  echo "[colab] Extracting -> $local_dir"
  rm -rf "$local_dir"
  tar -xzf "$local_tar" -C "$ROOT/comic_out"
  echo "[colab] Done. Artifacts under $local_dir"
}

cmd_stop() {
  need_colab
  echo "[colab] Stopping remote job (if any), then session '$SESSION'..."
  colab_sh 60 "
set +e
if [ -f $(remote_q "$REMOTE_PID") ]; then
  kill \"\$(cat $(remote_q "$REMOTE_PID"))\" 2>/dev/null || true
  rm -f $(remote_q "$REMOTE_PID")
fi
true
" || true
  colab stop -s "$SESSION"
}

main() {
  local cmd="${1:-}"
  shift || true
  case "$cmd" in
    new) cmd_new "$@" ;;
    bootstrap) cmd_bootstrap "$@" ;;
    run) cmd_run "$@" ;;
    status) cmd_status "$@" ;;
    logs) cmd_logs "$@" ;;
    restart) cmd_restart "$@" ;;
    download) cmd_download "$@" ;;
    stop) cmd_stop "$@" ;;
    -h|--help|help|"") usage 0 ;;
    *) echo "Unknown command: $cmd" >&2; usage 1 ;;
  esac
}

main "$@"
