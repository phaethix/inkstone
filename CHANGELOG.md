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

### Changed
- Phase 3 extract, beats, page plan, bible, portrait, storyboard panel, and
  finished-page render use action keys. A matching key does not call the provider
  again. A page plan key omits the recent layout strings. A bible key omits the
  bible prose, so a recorded merge does not reconcile every chunk. A stale page
  still repaints. The alias-merge ceiling stays 15 image calls and 0 chat calls at
  300 pages, and the same 15 and 0 at 600 pages. The two-phase scheduler and the
  manual fingerprint tokens remain.
- Measured the cost of one typical alias merge at zero quota (§9 phase 0h, resolved item 26):
  on the fixed 300-page sample (20 chunks x 15 pages, alias on 5 pages of chunks 4/9/14) the
  re-run spends **15 image calls** (the 15 stale pages) and **0 chat calls**. The merge
  is recorded the way a human merge is, so the folded alias is not painted again and does
  not reconcile the bible on the re-run. That replaces the design's "15 to 190" / "15 to 20"
  estimates in the §9 threshold table. The same merge on 600 pages, alias still on those 15,
  spends the same 15 image calls and 0 chat calls, so the re-run does not grow with book length.
- **L3 face overlay is now OFF by default** (`INKSTONE_L3=1` to enable). The cv2/OpenCV
  face-swap pastes a close-up portrait face onto generated panels and deforms stylized faces
  whenever pose/angle/lighting differ (which is most of the time in comic art). Consistency now
  relies on the robust L1 (prompt hard-description) + L2 (reference img2img) path; L3 stays as an
opt-in experiment. `apply_l3` guards were also retuned to use absolute face-pixel size as the
  "big enough" gate instead of a fragile full-frame ratio.
- **Phase 0b: page identity is now positional** (`c{ci:04d}-p{idx:04d}`, matching panel keys).
  The model-generated `page_id` is retained for prompts and auditing but no longer participates in
  identity, so a replan that renames pages cannot orphan recorded pages. Existing
  `state.json` checkpoints with legacy `c{ci:04d}:{page_id}` keys are rewritten deterministically
  on load (zero quota, idempotent), so a resumed project is not repainted.

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
