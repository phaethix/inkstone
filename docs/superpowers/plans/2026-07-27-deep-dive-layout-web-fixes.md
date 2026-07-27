# Deep-dive review fixes (no release tag)

**Date:** 2026-07-27  
**Source:** `.issue/2026-07-27-21_09-deep-dive-review.md`  
**Out of scope:** git tags / GitHub releases (per maintainer)

## Scope

1. **Page-mode aspect squash** — `_place_panel` uses `ImageOps.contain` + centered paste (letterbox), not stretch-to-cell.
2. **README provider claim** — state that `openai_compat` follows Agnes i2i `extra_body.image` protocol; vanilla OpenAI/Gemini need a subclass.
3. **Web POST hardening** — reject cross-site POSTs (`Origin` / `Sec-Fetch-Site`); cap JSON body size.
4. **`_load_dotenv`** — load remaining `.env` keys even when `AGNES_API_KEY` is already set (still do not override existing env).
5. **D1 plan CLI** — prefix output with `[experimental]`; keep English note that generate ignores density.

## Tests first

- Layout: square panel into tall 3-up cell must leave letterbox (bg pixels), not stretch.
- Web: foreign Origin → 403; oversized body → 413; same-origin / no Origin (curl) still OK for loopback tooling.
- dotenv: with `AGNES_API_KEY` preset, other keys from `.env` still load.
- CLI plan: stdout contains `[experimental]`.
