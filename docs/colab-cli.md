# Running Inkstone on Google Colab CLI

Long comic jobs can take hours (Agnes free-tier rate limits, retries, queues).
Keeping `python web/server.py` on a laptop means you cannot close the lid.
Use the [Google Colab CLI](https://developers.googleblog.com/introducing-the-google-colab-cli/)
to run the **headless** pipeline on a remote VM, then download `comic_out/`.

Inkstone does **not** need a GPU — generation calls Agnes over the network.
Prefer a **CPU** Colab runtime to save quota.

Helper script: [`scripts/colab_run.sh`](../scripts/colab_run.sh).

## Prerequisites

1. Install and authenticate the Colab CLI ([github.com/googlecolab/google-colab-cli](https://github.com/googlecolab/google-colab-cli)).
2. Put `colab` on your `PATH` (e.g. `conda activate google-colab`).
3. Set `AGNES_API_KEY` in the repo-root `.env` or the environment.
4. Run commands from the **Inkstone repo root**.

Smoke-test:

```bash
echo "print('ok')" | colab exec
colab stop
```

## One-shot flow

```bash
# 1) CPU session (no --gpu)
./scripts/colab_run.sh new

# 2) Install Inkstone on the VM
./scripts/colab_run.sh bootstrap              # git clone origin/main on the VM
# or, if your local commits are not pushed yet:
./scripts/colab_run.sh bootstrap --from-local

# 3) Start the job in the background (safe to close the laptop after this)
./scripts/colab_run.sh run path/to/novel.txt --project my-novel-01 --format webtoon

# 4) Later — from any machine with Colab CLI auth
./scripts/colab_run.sh status
./scripts/colab_run.sh logs

If `status` times out, the Colab kernel may be stuck after a long `exec`:

```bash
./scripts/colab_run.sh restart
./scripts/colab_run.sh status
```

# 5) Pull artifacts home
./scripts/colab_run.sh download --project my-novel-01

# 6) Tear down the VM when finished
./scripts/colab_run.sh stop
```

`run` uploads the novel, then starts:

```text
nohup python examples/generate_comic.py … --project <id> --out comic_out/<id>
```

on the VM. The local `colab exec` only waits until **start** succeeds (not until
the comic finishes). That is what lets you close the laptop.

## Resume after disconnect / Colab recycle

Use the **same** `--project` id (same `comic_out/<id>/state.json`):

```bash
./scripts/colab_run.sh new
./scripts/colab_run.sh bootstrap --from-local   # or bootstrap + re-upload state
./scripts/colab_run.sh run novel.txt --project my-novel-01 --format webtoon
```

If the previous VM still exists and still has `comic_out/<id>`, just `run` again
with the same project — `run_until_complete` / `state.json` resume skips finished
panels.

To continue a project whose artifacts only exist locally, upload them first:

```bash
tar -czf /tmp/my-novel-01.tgz -C comic_out my-novel-01
colab upload -s inkstone /tmp/my-novel-01.tgz /content/my-novel-01.tgz
# then extract under /content/inkstone/comic_out/ on the VM (see script or colab exec)
```

## Why not `web/server.py` on Colab?

| Approach | Close laptop? | Notes |
|----------|:-------------:|-------|
| Local `web/server.py` | No | Bound to this machine |
| Colab + Web UI | Awkward | Need a tunnel + browser; CLI disconnect still hurts |
| Colab + `generate_comic.py` (this guide) | Yes* | Background `nohup` + `--project` resume |

\*Subject to Colab session lifetime (see below).

## Limitations (read before overnight runs)

- **Free Colab** can reclaim or idle-timeout VMs. Treat Colab as “better than a laptop,” not a dedicated 24h server.
- **Agnes rate limits** still apply; Colab does not make image/chat calls faster.
- `colab exec` has a **short default timeout (30s)**. Never run the full comic pipeline in the foreground via `colab exec`; always background it (the script does this).
- Do **not** commit `.env` or upload secrets to a public notebook URL. The script writes the key to `/content/AGNES_API_KEY` on the VM only.

For true always-on overnight jobs, a cheap VPS running the same
`examples/generate_comic.py --project …` command is more reliable.

## Environment knobs

| Variable | Default | Meaning |
|----------|---------|---------|
| `INKSTONE_COLAB_SESSION` | `inkstone` | Colab session name (`colab -s`) |
| `INKSTONE_COLAB_REMOTE_ROOT` | `/content/inkstone` | Checkout path on the VM |
| `INKSTONE_COLAB_REPO` | `https://github.com/phaethix/inkstone.git` | Clone URL for `bootstrap` |
| `INKSTONE_COLAB_BRANCH` | `main` | Clone branch |

## Manual Colab CLI equivalents

If you prefer raw commands (same semantics as the script):

```bash
colab new -s inkstone
colab upload -s inkstone .env /content/AGNES_API_KEY   # or a key-only file
# clone or upload a source tarball into /content/inkstone
colab install -s inkstone -r /content/inkstone/requirements.txt
# better: pip install -e ".[dev]" inside /content/inkstone via colab exec

colab upload -s inkstone novel.txt /content/inkstone/novel.txt
echo 'import subprocess; subprocess.check_call("cd /content/inkstone && export AGNES_API_KEY=$(cat /content/AGNES_API_KEY) && nohup python examples/generate_comic.py novel.txt --project my-novel-01 --format webtoon --out comic_out/my-novel-01 > /content/inkstone_run.log 2>&1 &", shell=True)' \
  | colab exec -s inkstone --timeout 60

colab download -s inkstone /content/my-novel-01.tgz ./comic_out/my-novel-01.tgz
colab stop -s inkstone
```

## Related

- Local one-click: [`scripts/start.sh`](../scripts/start.sh) / `examples/generate_comic.py`
- Unattended supervisor: `core/pipelines/run_until_complete.py`
- Colab CLI announcement: <https://developers.googleblog.com/introducing-the-google-colab-cli/>
