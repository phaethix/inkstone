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
#   ./scripts/colab_run.sh pause                 # stop job only; VM stays up
#   ./scripts/colab_run.sh adopt                 # re-bind local name to orphan [?] VM
#   ./scripts/colab_run.sh stop                  # stop job + destroy Colab session
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

# Return 0 when session exists; print colab status unless --quiet.
colab_ensure_session() {
  local quiet="${1:-}"
  local out
  out="$(colab status -s "$SESSION" 2>&1)" || true
  if [[ "$quiet" != --quiet ]]; then
    printf '%s\n' "$out"
  fi
  if [[ "$out" == *"not found"* ]] || [[ "$out" == *"Not found"* ]]; then
    echo "[colab] Session '$SESSION' is not in local state." >&2
    echo "  If \`colab sessions\` shows [?] <endpoint>, reclaim: ./scripts/colab_run.sh adopt" >&2
    echo "  Else: ./scripts/colab_run.sh new && bootstrap && run … --project <id>" >&2
    return 1
  fi
  return 0
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

# Python helpers for cmd_logs / cmd_status (injected into colab_py).
_colab_logs_py_helpers() {
  cat <<'PY'
import json
import re
from pathlib import Path

_TQDM_RE = re.compile(r'^generating comic:\s*\d+%')
_INFO_PROGRESS_PARSE = re.compile(r'^\[\s*(\d{1,3})%\]\s*(?:stage=)?(.+)$')
_TQDM_PROGRESS_PARSE = re.compile(r'generating comic:\s*(\d+)%.*\[(.*)\]\s*$')
_INFO_MARKERS = (
    'PAUSED project',
    'Traceback',
    ' ERROR ',
    'WARNING',
    'ErrorCollector',
    '⏱',
    'started pid=',
    're-run with the same',
)


def tail_jsonl_records(path: Path, limit: int):
    if not path.is_file():
        return []
    records = []
    for ln in path.read_text(encoding='utf-8', errors='replace').splitlines():
        s = ln.strip()
        if not s:
            continue
        try:
            records.append(json.loads(s))
        except json.JSONDecodeError:
            continue
    return records[-limit:]


def format_api_error(rec):
    ts = str(rec.get('timestamp', ''))[:19]
    model = rec.get('model_type', '?')
    method = rec.get('api_method', '?')
    sc = rec.get('status_code')
    msg = str(rec.get('error_message', ''))[:240]
    retry = rec.get('retry_count', 0)
    sc_s = f' HTTP {sc}' if sc else ''
    return f'[ERROR] {ts} {model}.{method}{sc_s} retry={retry}: {msg}'


def normalize_stage(raw):
    s = (raw or '').strip().lstrip(',').strip()
    if s.startswith('stage='):
        s = s.split('=', 1)[1].strip()
    # "panels 12/100" -> panels
    head = s.split()[0] if s else s
    if head in ('panel', 'panels'):
        return 'panels'
    if head in ('portrait', 'portraits'):
        return 'portrait'
    return head or 'unknown'


# creative_comic runtime order (per-chunk until panels; then layout/export once).
_PIPELINE = ('extract', 'portrait', 'storyboard', 'panels', 'layout', 'export')


def parse_progress_line(s):
    s = s.strip()
    m = _INFO_PROGRESS_PARSE.match(s)
    if m:
        return int(m.group(1)), normalize_stage(m.group(2))
    m = _TQDM_PROGRESS_PARSE.search(s)
    if m:
        return int(m.group(1)), normalize_stage(m.group(2))
    return None


def last_progress_from_log(lines):
    for ln in reversed(lines):
        prog = parse_progress_line(ln)
        if prog:
            return prog
    return None


def format_progress_bar(pct, stage, label_width=None):
    bar_w = 28
    # Integer cells only — fractional ▏▎ chars break | alignment in many terminals.
    filled = min(bar_w, max(0, int(round(pct * bar_w / 100.0))))
    bar = ('█' * filled) + ('░' * (bar_w - filled))
    # Left-align label text; pad with spaces so closing ] lines up across rows.
    if label_width is not None:
        stage = f'{stage:<{label_width}}'
    return f'generating comic:  {pct:3d}%|{bar}| [{stage}]'


def format_pipeline_status(st, log_lines, total_chunks=None):
    """Always print all pipeline stages top→bottom in design/runtime order.

    extract→panels repeat per chunk. Once any panels exist, keep the checklist
    parked on panels (earlier rows 100%) until layout/export — do not bounce
    back to extract for each new chunk.
    """
    prog = last_progress_from_log(log_lines)
    live_pct = prog[0] if prog else 0
    # Prefer checkpoint stage over log postfix.
    cur = normalize_stage(str(st.get('stage') or ''))
    if cur not in _PIPELINE and prog:
        cur = prog[1]
    if cur not in _PIPELINE:
        cur = 'extract'

    done, planned = panel_counts_from_state(st, total_chunks)
    panel_pct = 0
    if planned > 0:
        panel_pct = min(95, int(round(100.0 * 0.9 * done / planned)))

    # Stabilize UI across per-chunk extract/portrait/storyboard resets.
    if done > 0 and cur in ('extract', 'portrait', 'storyboard', 'panels'):
        cur = 'panels'
    cur_idx = _PIPELINE.index(cur)

    labels = []
    for stage in _PIPELINE:
        if stage == 'panels' and planned:
            labels.append(f'panels {done}/{planned}')
        else:
            labels.append(stage)
    label_width = max(len(x) for x in labels)

    rows = []
    for i, (stage, label) in enumerate(zip(_PIPELINE, labels)):
        if i < cur_idx:
            rows.append(format_progress_bar(100, label, label_width))
        elif i == cur_idx:
            pct = max(live_pct, panel_pct) if stage == 'panels' else live_pct
            rows.append(format_progress_bar(pct, label, label_width))
        else:
            rows.append(format_progress_bar(0, label, label_width))
    return rows


def panel_counts_from_state(st, total_chunks=None):
    done = len(st.get('panels_done') or []) + len(st.get('skipped') or [])
    planned = 0.0
    panel_counts = []
    accounted = {str(k) for k in (st.get('skipped_chunks') or [])}
    for key, cache in (st.get('chunk_cache') or {}).items():
        sb = (cache or {}).get('storyboard') if isinstance(cache, dict) else None
        if isinstance(sb, dict) and isinstance(sb.get('panels'), list):
            n = len(sb['panels'])
            planned += n
            panel_counts.append(n)
            accounted.add(str(key))
    keys = {int(k) for k in (st.get('chunk_cache') or {}) if str(k).isdigit()}
    keys.update(int(k) for k in (st.get('skipped_chunks') or []) if str(k).isdigit())
    inferred = max(1, (max(keys) + 1) if keys else 1)
    total = max(inferred, int(total_chunks)) if total_chunks else inferred
    avg = (sum(panel_counts) / len(panel_counts)) if panel_counts else 8.0
    for i in range(total):
        if str(i) not in accounted:
            planned += avg
    return done, max(done, int(round(planned)))


def total_chunks_from_project(project_dir, root):
    source = project_dir / 'source.txt'
    if not source.is_file():
        return None
    try:
        import sys
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from core.comic.segmentation import segment_text
        return max(1, len(segment_text(source.read_text(encoding='utf-8'))))
    except Exception:
        return None


def read_project_stats(root):
    out = []
    panels_root = root / 'comic_out'
    if not panels_root.is_dir():
        return out
    for state_path in sorted(panels_root.glob('*/state.json')):
        try:
            st = json.loads(state_path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            continue
        panels_dir = state_path.parent / 'panels'
        n_files = sum(1 for p in panels_dir.iterdir() if p.is_file()) if panels_dir.is_dir() else 0
        out.append((state_path.parent.name, state_path.parent, st, n_files))
    return out


def meaningful_info_lines(lines):
    out = []
    for ln in lines:
        s = ln.strip()
        if not s or _TQDM_RE.match(s):
            continue
        if _INFO_PROGRESS_PARSE.match(s):
            out.append(s)
            continue
        if s.startswith('project ') or s.startswith('  PDF') or s.startswith('  WEBTOON'):
            out.append(s)
            continue
        if s.startswith('  panels') or s.startswith('  review') or s.startswith('  stale'):
            out.append(s)
            continue
        if any(m in s for m in _INFO_MARKERS):
            out.append(s)
            continue
        if s.startswith('INFO ') or s.startswith('WARNING ') or s.startswith('ERROR '):
            out.append(s)
    return out
PY
}

# Run Python on the Colab session via stdin. Default timeout is only 30s —
# long Inkstone jobs must be started with nohup (see cmd_run), not foreground exec.
# Sets COLAB_PY_ERR to captured stderr (also printed).
colab_py() {
  local timeout="${1:-60}"
  shift
  local code="$1"
  local err_file
  err_file="$(mktemp)"
  # shellcheck disable=SC2064
  trap "rm -f '$err_file'" RETURN
  COLAB_PY_ERR=""
  if printf '%s\n' "$code" | colab exec -s "$SESSION" --timeout "$timeout" 2>"$err_file"; then
    # colab may still print warnings to stderr on success
    if [[ -s "$err_file" ]]; then
      cat "$err_file" >&2
    fi
    return 0
  fi
  COLAB_PY_ERR="$(cat "$err_file")"
  cat "$err_file" >&2
  return 1
}

# Print recovery hints after a failed colab_py (uses COLAB_PY_ERR).
colab_py_fail_hint() {
  local what="${1:-Remote exec}"
  if [[ "${COLAB_PY_ERR:-}" == *"404"* ]] || [[ "${COLAB_PY_ERR:-}" == *"401"* ]] \
    || [[ "${COLAB_PY_ERR:-}" == *"appears to be lost"* ]]; then
    echo "[colab] $what failed: session lost (404/401). Local alias was pruned." >&2
    echo "  If \`colab sessions\` still shows [?] <endpoint>, reclaim it:" >&2
    echo "    ./scripts/colab_run.sh adopt" >&2
    echo "    ./scripts/colab_run.sh status" >&2
    echo "  Otherwise recreate (progress only if you downloaded comic_out):" >&2
    echo "    ./scripts/colab_run.sh new && ./scripts/colab_run.sh bootstrap --from-local" >&2
    echo "    ./scripts/colab_run.sh run <novel.txt> --project <id>" >&2
    return
  fi
  echo "[colab] $what failed (kernel busy or timed out)." >&2
  echo "  Try: ./scripts/colab_run.sh restart" >&2
  echo "  Then: ./scripts/colab_run.sh status" >&2
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
  colab_ensure_session --quiet || return 1
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
  colab_ensure_session --quiet || return 1
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
  colab_ensure_session || return 1
  if ! colab_py 90 "$(_colab_logs_py_helpers)
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
    print('log:', log_path)
    lines = log_path.read_text(encoding='utf-8', errors='replace').splitlines()
else:
    lines = []
    print('log: none yet')
stats = read_project_stats(root)
if stats:
    name, proj_dir, st, n_files = stats[0]
    n_chunks = total_chunks_from_project(proj_dir, root)
    for row in format_pipeline_status(st, lines, n_chunks):
        print(' ', row)
    done, planned = panel_counts_from_state(st, n_chunks)
    print(
        f'project {name}: state.stage={st.get(\"stage\", \"?\")} '
        f'panels {done}/{planned} on_disk={n_files}'
        + (f' chunks={n_chunks}' if n_chunks else '')
    )
else:
    prog = last_progress_from_log(lines)
    if prog:
        print(' ', format_progress_bar(prog[0], prog[1]))
    else:
        print('  (no progress yet)')
    print('comic_out: none yet')
"; then
    colab_py_fail_hint "status"
    return 1
  fi
}

cmd_restart() {
  need_colab
  colab_ensure_session || return 1
  echo "[colab] Restarting kernel for session '$SESSION'..."
  colab restart-kernel -s "$SESSION"
}

cmd_logs() {
  need_colab
  colab_ensure_session --quiet || return 1
  local lines="${1:-50}"
  colab_py 60 "$(_colab_logs_py_helpers)
from pathlib import Path

root = Path($(py_str "$REMOTE_ROOT"))
error_log = root / 'logs' / 'errors.jsonl'
run_log = Path($(py_str "$REMOTE_LOG"))

errs = tail_jsonl_records(error_log, $lines)
for rec in errs:
    print(format_api_error(rec))

if run_log.is_file():
    info = meaningful_info_lines(
        run_log.read_text(encoding='utf-8', errors='replace').splitlines()
    )
    for line in info[-$lines:]:
        print(line)
"
}

# Interpreter that provides the `colab` package (conda env), else python3.
colab_python() {
  local py
  py="$(dirname "$(command -v colab)")/python"
  if [[ -x "$py" ]]; then
    printf '%s\n' "$py"
  else
    printf '%s\n' python3
  fi
}

# Stream a remote file via Colab Contents API with a progress bar.
# Stock `colab download` buffers the whole base64 JSON silently — looks hung.
colab_download_progress() {
  local remote_path="$1"
  local local_path="$2"
  "$(colab_python)" - "$SESSION" "$remote_path" "$local_path" <<'PY'
import base64
import json
import sys
from pathlib import Path
from urllib.parse import quote

import requests

from colab_cli.common import state

name, remote_path, local_path = sys.argv[1], sys.argv[2], sys.argv[3]
s = state.store.get(name)
if not s:
    print(f"[colab] Session '{name}' not in local state. Try: ./scripts/colab_run.sh adopt", file=sys.stderr)
    sys.exit(1)

quoted = quote(remote_path.strip("/"), safe="/")
url = f"{s.url.rstrip('/')}/api/contents/{quoted}"
params = {
    "authuser": "0",
    "colab-runtime-proxy-token": s.token,
    "content": "1",
}

print(f"[colab] Fetching {remote_path} (streamed; may be large)...")
with requests.get(url, params=params, stream=True, timeout=(30, 3600)) as resp:
    if resp.status_code == 404:
        raise FileNotFoundError(f"Remote file not found: {remote_path}")
    resp.raise_for_status()
    total = int(resp.headers.get("Content-Length") or 0)
    chunks = []
    got = 0
    last_pct = -1
    for chunk in resp.iter_content(chunk_size=1024 * 256):
        if not chunk:
            continue
        chunks.append(chunk)
        got += len(chunk)
        if total > 0:
            pct = min(99, int(got * 100 / total))
            if pct != last_pct and (pct % 2 == 0 or got == total):
                bar_w = 28
                filled = int(round(pct * bar_w / 100.0))
                bar = ("█" * filled) + ("░" * (bar_w - filled))
                mb = got / (1024 * 1024)
                tot_mb = total / (1024 * 1024)
                print(
                    f"\r[colab] download  {pct:3d}%|{bar}| {mb:6.1f}/{tot_mb:6.1f} MB",
                    end="",
                    flush=True,
                )
                last_pct = pct
        else:
            mb = got / (1024 * 1024)
            print(f"\r[colab] download  … {mb:6.1f} MB received", end="", flush=True)

raw = b"".join(chunks)
if total > 0:
    bar = "█" * 28
    tot_mb = total / (1024 * 1024)
    print(f"\r[colab] download  100%|{bar}| {tot_mb:6.1f}/{tot_mb:6.1f} MB")
else:
    print()

print("[colab] Decoding payload...")
data = json.loads(raw.decode("utf-8"))
if data.get("type") == "directory":
    raise IsADirectoryError(f"Cannot download a directory: {remote_path}")
content = data.get("content") or ""
if data.get("format") == "base64":
    blob = base64.b64decode(content)
else:
    blob = str(content).encode("utf-8")

out = Path(local_path)
out.parent.mkdir(parents=True, exist_ok=True)
out.write_bytes(blob)
print(f"[colab] Wrote {out} ({len(blob) / (1024 * 1024):.1f} MB)")
PY
}

cmd_download() {
  need_colab
  colab_ensure_session --quiet || return 1
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
  colab_download_progress "$remote_tar" "$local_tar"
  echo "[colab] Extracting -> $local_dir"
  rm -rf "$local_dir"
  tar -xzf "$local_tar" -C "$ROOT/comic_out"
  echo "[colab] Done. Artifacts under $local_dir"
}

cmd_adopt() {
  # Re-bind SESSION to an orphan server assignment ([?] in `colab sessions`).
  # Colab CLI prunes local url/token on 404/401 even when the VM still exists.
  need_colab
  local endpoint="${1:-}"
  "$(colab_python)" - "$SESSION" "$endpoint" <<'PY'
import sys

from colab_cli.commands.session import spawn_keep_alive
from colab_cli.common import state
from colab_cli.state import SessionState

name = sys.argv[1]
want = sys.argv[2].strip() or None

existing = state.store.get(name)
if existing:
    print(f"[colab] Session '{name}' already bound to {existing.endpoint}")
    sys.exit(0)

assignments = state.client.list_assignments()
if not assignments:
    print("[colab] No server assignments to adopt. Run: ./scripts/colab_run.sh new")
    sys.exit(1)

if want:
    matches = [a for a in assignments if a.endpoint == want]
    if not matches:
        print(f"[colab] No assignment with endpoint {want}")
        for a in assignments:
            print(f"  {a.endpoint}")
        sys.exit(1)
    a = matches[0]
elif len(assignments) == 1:
    a = assignments[0]
else:
    print("[colab] Multiple assignments; pass endpoint explicitly:")
    print(f"  ./scripts/colab_run.sh adopt <endpoint>")
    for a in assignments:
        print(f"  {a.endpoint}")
    sys.exit(1)

proxy = a.runtime_proxy_info
s = SessionState(
    name=name,
    token=proxy.token,
    url=proxy.url,
    endpoint=a.endpoint,
    variant=a.variant.name,
    accelerator=a.accelerator.value,
)
state.store.add(s)
s.keep_alive_pid = spawn_keep_alive(
    a.endpoint,
    name,
    auth_provider=state.auth_provider,
    config_path=state.config_path,
)
state.store.add(s)
print(f"[colab] Adopted {a.endpoint} as session '{name}'.")
print("  Next: ./scripts/colab_run.sh status")
PY
}

cmd_pause() {
  need_colab
  colab_ensure_session || return 1
  echo "[colab] Stopping background job on session '$SESSION' (VM stays up)..."
  colab_py 60 "
import os
import signal
from pathlib import Path

pid_path = Path($(py_str "$REMOTE_PID"))
if not pid_path.is_file():
    print('background job: not running (no pid file)')
else:
    pid = int(pid_path.read_text().strip())
    try:
        os.kill(pid, 0)
    except OSError:
        print(f'background job: pid file stale (pid={pid})')
        pid_path.unlink(missing_ok=True)
    else:
        os.kill(pid, signal.SIGTERM)
        print(f'stopped background job pid={pid}')
        pid_path.unlink(missing_ok=True)
"
}

cmd_stop() {
  need_colab
  echo "[colab] Stopping background job (if any), then destroying session '$SESSION'..."
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
    pause) cmd_pause "$@" ;;
    adopt) cmd_adopt "$@" ;;
    stop) cmd_stop "$@" ;;
    -h|--help|help|"") usage 0 ;;
    *) echo "Unknown command: $cmd" >&2; usage 1 ;;
  esac
}

main "$@"
