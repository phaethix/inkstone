# Design: Lettering MVP + Showcase scaffold + Streaming PDF

**Date:** 2026-07-27  
**Approved scope:** Option **C** — infrastructure only for showcase (source + scripts; no generated panels/PDF in-repo).

## Lettering

- Extend `Panel` / `GeneratedPanel` / `PanelImage` with optional `caption` and `sfx` (keep `dialogue`).
- Layout:
  - `caption` → top rectangular narration bar
  - `dialogue` → existing rounded bubble (bottom)
  - `sfx` → bold outlined text, upper-right of cell (page) / below panel (webtoon)
- Storyboard system/user reminders ask the model to split these fields; legacy states with only `dialogue` unchanged.
- Out of scope: face-aware placement, bubble tails, speaker attribution UI, R2L panel grid.

## Showcase (C)

- `examples/showcase/journey-west-ch1/source.txt` — short public-domain 《西游记》 excerpt
- `examples/showcase/journey-west-ch1/README.md` — how to `inkstone plan`, generate, Colab; honest note that artifacts are not committed
- `scripts/run_showcase.sh` — thin wrapper calling plan + printing next generate/colab steps
- No panels/PDF/coverage report committed.

## Streaming PDF

- Prefer `img2pdf` when importable (embeds files without decoding all RGB).
- Else PIL path: convert in batches of 8 to temp PDFs, concatenate with `pypdf` when importable; if neither helper exists and page count > 8, still export but log a loud warning and use batched peak (batch size 8) via temp files + a minimal concat that shells to `pdfunite`/`gs` if present; final fallback single-batch only when `len(pages) <= 8`.
- Soft deps: try `img2pdf` / `pypdf` / CLI merge tools — no new hard dependency required for small comics; document `pip install img2pdf pypdf` for long books.
