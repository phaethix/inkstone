# Inkstone Roadmap

> This file is the **execution-layer** milestone plan, paired with the (internal) architecture notes.
> Inkstone is an independent, from-scratch implementation (not a fork), so we do not carry over the
> upstream `server.py` / video / audio modules; the comic-specific layer is written to this project's standard.

## M1 — Image Provider abstraction foundation ✅ done

Goal: unify image generation behind a single, swappable interface — ordinary users run Agnes with
zero config; advanced users can switch to any OpenAI-compatible endpoint.

- [x] `core/api/image_provider.py`
  - `ImageOutput` (result with `url` / `b64` forms; `.save()` persists and **downloads without an auth token**)
  - `ImageProvider(ABC)`: abstract `async generate_single_image(...)` contract
  - `OpenAICompatProvider(ImageProvider)`: any OpenAI-compatible image endpoint (Gemini, ...), hedges single-provider risk
  - `get_image_provider(...)`: factory reading `PROVIDER` / `AGNES_API_KEY` / `OPENAI_COMPAT_*`, default agnes
- [x] `core/api/agnes_image.py`: `AgnesImageAPI(ImageProvider)` with exponential-backoff retries + error collection
- [x] `core/api/rate_limiter.py`: thread-safe token bucket (default 20/min × 0.8 safety factor)
- [x] `core/api/error_collector.py`: API failures persisted to `logs/`, self-failures never break the main flow
- [x] `utils/image.py`: `download_image` (bare request, 50MB cap)
- [x] `tests/test_providers.py`: 6 cases — interface conformance + factory missing-key error, **all passing**

Verified: `pytest` → 6 passed.

## M2 — Comic-specific pipeline (next)

Goal: assemble text-to-image / image-to-image into a real comic production chain.

**New:**
- `core/comic/consistency.py` — `ConsistencyEngine`
  - L1: prompt hard-describes character features
  - L2: reference-image img2img (character portrait as `reference_image_paths`)
  - L3: PIL/OpenCV face / feature overlay fallback (M1 spike showed L2 alone is insufficient)
- `core/comic/layout.py` — `LayoutEngine`: N panels laid out on a grid (2×2, ...) into a comic page
- `core/comic/export.py` — `ExportEngine`: PDF (`manga2pdf`) + vertical-strip PNG
- `core/pipelines/creative_comic.py` — orchestration: character portraits → panels → layout → export
- `core/screenwriter.py` — screenwriter (reusing the Agnes chat approach): adds content-safety constraints
  (no explicit / smoking / etc. words — the spike proved this necessary)

**Reused (not rewritten):** `get_image_provider`, `AgnesImageAPI`, `RateLimiter`, `error_collector`, `utils.image.download_image`.

**New deps (enable in requirements when needed):** fastapi / uvicorn / pydantic / manga2pdf.

## M3 — Long-form + consistency hardening

- Long text split by chapter / segment → generate per segment → cross-chapter character assets reused
  (`CharacterAsset` persisted + optional face embedding).
- Default consistency strategy **L1 + L2 + L3**; introduce `InsightFace` embedding for precise "which face belongs to which
  character" matching (avoids L3 mis-overlay).
- Resumption: checkpoint written to `state.json`; `done_panels` dedup key avoids duplicate generation / billing.

## M4 — Open-source release

- README / screenshots / sample-novel demo; GitHub Release; one-click launch script.
- Optional lightweight Web UI (single-file Tailwind SPA, zero build).
