# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- M2 comic-specific pipeline: `creative_comic` orchestration (segment → extract → character
  consistency L1/L2/L3 → storyboard → generate → layout → export) with resumable `state.json`
  and content-safety graceful skip on upstream rejection.
- Chat provider abstraction (`ChatProvider` / `AgnesChatAPI`) with forced function calling.
- `core/comic`: `consistency` (L1/L2/L3), `segmentation`, `layout` (page + webtoon output),
  `export` (PDF via `manga2pdf` + pure-PIL webtoon PNG).
- `core/screenwriter` for structured extraction/storyboard planning with content-safety hygiene.
- End-to-end example (`examples/generate_comic.py` + `examples/scene1.txt`).
- M3 long-form + consistency hardening:
  - Cross-chapter character reuse: `CharacterAsset` persisted in `state.json`; `merge_characters`
    dedups by exact name so a portrait is never regenerated for a known character.
  - Alias detection: `detect_character_aliases` flags near-duplicate names (e.g. `方鸿渐` vs `鸿渐`)
    into `state.needs_review` for human review — no auto-merge, so a variant name is never silently
    forked into a second character that would fracture consistency.
  - Billing-free resume: per-chunk `extract`/`storyboard` cached in `ProjectState.chunk_cache`, so a
    resume reuses them and never re-pays the (billable) chat API; `panels_done` dedup avoids duplicate
    panel generation. `InsightFace` embedding / L4 iterative refinement remain deferred (GPU-only).

## [0.1.0] - 2026-07-20

### Added
- `ImageProvider` abstraction layer: `AgnesImageAPI` (default) and
  `OpenAICompatProvider` (fallback), with a `get_image_provider()` factory.
- Token-bucket `RateLimiter` and an error collector that persists API failures
  to `logs/`.
- Project scaffolding: README, CONTRIBUTING, CODE_OF_CONDUCT, SECURITY,
  SUPPORT, CHANGELOG, issue/PR templates, CODEOWNERS, Dependabot, and a CI
  workflow (ruff lint + format check + pytest on Python 3.10–3.12).
- Upstream attribution and non-fork statement in `NOTICE`.
