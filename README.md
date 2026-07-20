# Inkstone

> Local-first, open-source **novel → series comic** generator built on the free Agnes multimodal API.
> Reads a local `txt` → extracts characters / scenes / panels → Agnes generates images → comic layout → exports **PDF / PNG**.
>
> *"Grind your novel into comics."*

Inkstone wraps image generation behind a pluggable `ImageProvider` abstraction: **Agnes is the zero-config default** (Free Access tier, no GPU / paid key required), with one-line switching to any OpenAI-compatible image endpoint (Gemini / Qwen / self-hosted SD, ...) to hedge single-provider risk.

## Features

- **Truly zero-threshold:** ordinary users only set one `AGNES_API_KEY` to run (Free Access tier).
- **Agnes-native multimodal:** screenwriting `agnes-2.0-flash`, text-to-image `agnes-image-2.1-flash`, image-to-image / consistency `agnes-image-2.0-flash`.
- **Character consistency (core hard problem):** prompt hard-description (L1) + reference-image img2img (L2) + PIL/OpenCV feature-overlay fallback (L3, M2).
- **Reliability layer:** token-bucket rate limiting + exponential-backoff retries + error collection, against upstream 503 / 429.
- **Pluggable image Provider:** `ImageProvider` abstraction, Agnes by default, swappable to any OpenAI-compatible image API.

## Quick start (M1)

```bash
# 1) Prepare the environment
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2) Configure (ordinary users only edit the first line)
cp .env.example .env
#    edit .env and set AGNES_API_KEY=sk-xxx

# 3) Run the unit tests (no network required)
pytest
#   -> 6 passed (validates the ImageProvider abstraction and factory contract)

# 4) Call it in code (example)
from core.api import get_image_provider
provider = get_image_provider()          # reads AGNES_API_KEY, defaults to agnes
out = await provider.generate_single_image("a bespectacled cat, ink-wash style")
out.save("panel.png")                    # saved on generation (24h URL expiry avoided)
```

> M1 has shipped the "image Provider abstraction" foundation. The comic-specific
> pipeline (`creative_comic`, layout/export, long-novel resumption) is planned for
> M2/M3 — see ROADMAP.

## Current progress

- [x] **M1 foundation:** `ImageProvider` abstraction (`core/api/image_provider.py`), `AgnesImageAPI` default + `OpenAICompatProvider` fallback, `get_image_provider()` factory; token-bucket rate limiter + error collector; 6/6 unit tests passing.
- [ ] **M2:** comic-specific pipeline `creative_comic` + consistency engine (L1/L2/L3) + layout + PDF/PNG export + content-safe screenwriter constraints.
- [ ] **M3:** long-novel resumption by chapter + cross-chapter character consistency hardening.
- [ ] **M4:** open-source release.

## Design philosophy & attribution

Inkstone is an **independent implementation, not a fork**. Its reliability-layer
design and the choice of Agnes as the free multimodal backend were inspired by
[`lcy362/agnes-video-generator`](https://github.com/lcy362/agnes-video-generator)
(MIT), but **it does not incorporate the upstream source tree**.

- Released under the **MIT** license (see [LICENSE](LICENSE)).
- Upstream inspiration and attribution are recorded in [NOTICE](NOTICE).
- Public milestones: [docs/ROADMAP.md](docs/ROADMAP.md).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). All code, docs, and commit messages are in
English; commits follow [Conventional Commits](https://www.conventionalcommits.org/).
