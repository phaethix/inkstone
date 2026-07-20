<p align="center">
  <img src="assets/logo.svg" width="220" alt="Inkstone" />
</p>

<h1 align="center">Inkstone</h1>

<p align="center">
  <b>Local-first, open-source <i>novel → series comic</i> generator.</b><br>
  <sub>Built on the free Agnes multimodal API — no GPU, no paid key, grind your novel into comics.</sub>
</p>

<p align="center">
  <a href="https://github.com/phaethix/inkstone/releases"><img src="https://img.shields.io/badge/release-v0.1.0-blue" alt="Release" /></a>
  <a href="./LICENSE"><img src="https://img.shields.io/badge/license-MIT-green" alt="License" /></a>
  <a href="https://github.com/phaethix/inkstone/actions/workflows/ci.yml"><img src="https://github.com/phaethix/inkstone/actions/workflows/ci.yml/badge.svg?branch=main" alt="CI" /></a>
  <img src="https://img.shields.io/badge/python-3.10%2B-blue" alt="Python 3.10+" />
  <img src="https://img.shields.io/badge/PRs-welcome-brightgreen" alt="PRs welcome" />
</p>

> *"Grind your novel into comics."*

**Inkstone** is a local-first, open-source **novel → series comic** generator. It reads a local `txt` novel and, through the free **Agnes** multimodal API, produces comic pages with cross-panel character consistency — exported as **PDF / PNG**. No GPU, no paid plan: just one free API key. Image generation sits behind a pluggable `ImageProvider`, so you can switch to any OpenAI-compatible endpoint with a single line.

## Table of Contents

- [Features](#features)
- [Why Inkstone](#why-inkstone)
- [Quick Start](#quick-start)
- [Configuration](#configuration)
- [How it works](#how-it-works)
- [Resources](#resources)
- [Contributing](#contributing)

---

## Features

- **Free & local-first** — one `AGNES_API_KEY`, no GPU, no paid plan.
- **Agnes-native multimodal** — `agnes-2.0-flash` for scripting, `agnes-image-2.1-flash` for t2i and i2i consistency.
- **Pluggable `ImageProvider`** — Agnes by default; one line switches to any OpenAI-compatible endpoint.
- **Character consistency engine** — L1 prompt description + L2 reference img2img + L3 PIL/OpenCV overlay, the best feasible under no-GPU.
- **Reliability layer** — token-bucket rate limiting, exponential-backoff retries, and error collection against 429/503.
- **Resumable long-form runs** — chapter-split generation with a persisted `state.json` checkpoint.

## Why Inkstone

Every novel-to-comic tool we surveyed in 2026 runs on a **paid** model — Gemini, OpenAI, Doubao, Wenxin, or Claude. Inkstone is built differently: it is **Agnes-native and zero-cost**, the one combination no other open-source generator occupies.

| | Agnes-native | Zero-cost (no GPU / no paid key) |
|---|:---:|:---:|
| Other open-source comic generators | — | — (all paid APIs) |
| **Inkstone** | ✓ | ✓ |

Inkstone is an **independent implementation, not a fork** — inspired by [`lcy362/agnes-video-generator`](https://github.com/lcy362/agnes-video-generator) (MIT) but carrying none of its source tree. Attribution is recorded in [NOTICE](NOTICE).

> **An honest trade-off.** Free, cloud-only Agnes with no GPU caps how far character consistency can reach. The strongest approaches (IP-Adapter / InsightFace) need a local GPU running SDXL/Flux — incompatible with Inkstone's zero-cost premise. So Inkstone trades *perfect* consistency for *zero-cost + no-GPU + out-of-the-box*, using L1+L2+L3 as the best feasible strategy. Stated plainly, not hidden.

## Quick Start

> **Prerequisites:** Python 3.10+, [conda](https://docs.conda.io/projects/conda/en/latest/user-guide/install/index.html), and a free [Agnes](https://agnes-ai.com) API key (Free Access tier).

```bash
# 1. Create & activate the conda environment
conda create -n inkstone python=3.12 -y
conda activate inkstone
# Verify the env is active — `which python` must point inside the env
# (e.g. .../envs/inkstone/bin/python). If it still shows /opt/homebrew/... or
# /usr/bin, run `conda init zsh` and open a new terminal, then reactivate.

# 2. Install Inkstone (runtime + dev/test tooling)
python -m pip install -e ".[dev]"

# 3. Provide your API key
cp .env.example .env
#    then open .env and set AGNES_API_KEY=sk-xxx
```

Generate your first panel — the runnable example lives at `examples/first_panel.py`:

```console
$ python examples/first_panel.py   # run from the repo root
saved -> panel.png
```

Verify the install — the test suite runs fully offline:

```console
$ python -m pytest
15 passed   # validates the ImageProvider abstraction, factory contract, and retry/backoff reliability
```

> **Status:** M1 (the `ImageProvider` abstraction foundation) has shipped. The comic-specific pipeline — `creative_comic`, layout/export, long-novel resumption — is planned for M2/M3. See [Roadmap](docs/ROADMAP.md).

## Configuration

Inkstone is configured through environment variables (copy `.env.example` → `.env`):

| Variable | Required | Default | Description |
|----------|:---:|---|---|
| `AGNES_API_KEY` | ✅ | — | Free Access tier key; the only thing ordinary users need. |
| `AGNES_RATE_LIMIT` | | `20` | Requests/min ceiling (× 0.8 safety factor applied). |
| `AGNES_IMAGE_I2I_MODEL` | | `agnes-image-2.1-flash` | Model used for consistency img2img. |
| `PROVIDER` | | `agnes` | Set to `openai_compat` to route to any OpenAI-compatible image endpoint. |
| `OPENAI_COMPAT_*` | | — | Base URL / key / models, used only when `PROVIDER=openai_compat`. |

## How it works

A `txt` novel is split into segments → characters & scenes are extracted with `agnes-2.0-flash` → storyboard prompts are generated → the `ImageProvider` (Agnes by default) paints each panel → panels are laid out and exported to PDF/PNG. The core challenge — **cross-panel character consistency without a GPU** — is handled by a layered strategy (prompt hard-description + reference img2img + PIL/OpenCV overlay), backed by a reliability layer (rate limiting, retries, and `state.json` resumption). Full design, consistency strategy, and risk analysis live in the [whitepaper](docs/whitepaper.md).

## Resources

- **Design & risk analysis** — [docs/whitepaper.md](docs/whitepaper.md)
- **Milestone plan** — [docs/ROADMAP.md](docs/ROADMAP.md)
- **Contributing guide** — [CONTRIBUTING.md](CONTRIBUTING.md)
- **Issues & feedback** — [GitHub Issues](https://github.com/phaethix/inkstone/issues)

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). All code, docs, and commit messages are in English; commits follow [Conventional Commits](https://www.conventionalcommits.org/).

---

<p align="center">
  MIT License — <a href="./LICENSE">LICENSE</a>
</p>
