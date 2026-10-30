# Inkstone codebase review

**Repository:** phaethix/inkstone  
**Reviewed at:** `main` @ `04aa7dd` (2026-10-30)  
**Scope:** architecture, security, errors and logging, API validation, tests, performance, maintainability, CI/CD. Web UI (`web/`), core pipeline, providers and API keys, state/resume, harness experiments, scripts, and GitHub Actions.  
**Method:** static review of the tree that is actually in the repo. No application code was changed. Line numbers refer to that revision and will drift.

No **Critical** issue (remote code execution, committed secret, or default bind on a public interface) turned up. Two **High** issues are local-server trust-boundary bugs: the UI can be driven and read by a page that is not the UI, and image downloads will fetch whatever URL a provider returns.

---

## Architecture summary

Inkstone is a local-first novel-to-comic generator. A `.txt` source is segmented, planned with a chat model (`agnes-3.0-flash` by default), painted with an image model (`agnes-image-2.5-flash`), lettered with real fonts, and exported as PDF, CBZ, or a webtoon PNG. There is no GPU stack and no new web framework: the UI is one HTML file plus `http.server`.

```
CLI (core/cli.py)  /  Web UI (web/server.py + web/index.html)
        │
        ▼
run_until_complete          supervisor: 429/503/timeout retry until a 24h pause
        │
        ▼
creative_comic              segment → extract → bible → page plan → paint → letter → export
        │                   checkpoint: comic_out/<id>/state.json + cache.json
        ▼
screenwriter + harness      forced tool calls; editable prompt tree under core/harness/data/
        │
        ▼
ChatProvider / ImageProvider     Agnes default; PROVIDER=openai_compat optional
        │
        ▼
rate limiter + retry + error JSONL
```

Supporting pieces that are easy to miss on a first pass:

| Area | Where it lives | Role |
|---|---|---|
| Contracts | `core/schemas.py` (~2003 lines) | Pydantic models doubled as LLM tool schemas |
| Visual bible / identity | `core/comic/visual_bible.py` (~2076), `core/comic/identity.py`, `core/comic/ledger.py` | Canon, alias review, continuity ledger |
| Content-addressed runs | `core/comic/cas.py`, `core/pipelines/stage_*.py`, `core/comic/env_snapshot.py` | Stage keys, manifests, `h_env` digest |
| Resume / supervisor | `core/pipelines/resume.py`, `reconcile.py`, `run_until_complete.py` | Skip completed work; pause on deadline, budget, gate, ledger |
| Experiments | `core/experiment.py`, `core/harness/` | Planning-only A/B; compose never overwrites the builtin tree |
| Config | `core/config.py` | Every env name is an `ENV_*` constant; `ALL_ENV_VARS` is derived from those names |
| Entry points | `inkstone = core.cli:main` in `pyproject.toml`; `web/server.py`; `scripts/start.sh` | |
| Tests | `tests/` — 127 modules, 1192 `test_*` functions | Offline; `conftest.py` stubs sleep, rate limit, and error-log writes |
| CI | `.github/workflows/ci.yml`, `pages.yml` | ruff + pytest on 3.10–3.12; static Pages deploy of `web/index.html` |

Default render mode is finished-page (one image per page, lettering overlaid). `INKSTONE_RENDER_MODE=panel_compose` keeps the older per-panel path. L3 face paste is off unless `INKSTONE_L3=1`.

The orchestrator is the structural risk. `core/pipelines/creative_comic.py` is 2997 lines, and `_creative_comic` starts at line 1091 and runs to the end of the file. Schemas, the visual bible, the CLI, and the SPA are each over 1600 lines. The provider, retry, lock, and checkpoint layers underneath that are in much better shape.

---

## What looks strong

- **One config registry.** `core/config.py` funnels env reads through named constants, and `tests/test_env_snapshot.py` requires `H_ENV_REGISTERED | H_ENV_EXCLUDED` to partition `ALL_ENV_VARS`. Credentials are excluded from cache keys (`core/comic/env_snapshot.py` lines 145–149) so rotating a key does not invalidate a book and a digest cannot be reversed into a key.
- **Provider boundary.** Chat and image share `retryable_post` (`core/api/retry.py`). Sessions set `trust_env = False` so a stale shell `HTTPS_PROXY` cannot black-hole every call (lines 42–62). Downloads were brought onto the same session (`utils/image.py` lines 32–39; `tests/test_image_download_proxy.py`).
- **Rate limiting matches the published free-tier table** and buckets by resolution tier, not by exact `WxH` (`core/api/rate_limiter.py` lines 57–91). The sleep happens after the lock is released (lines 106–120), so waiters do not serialize on the bucket lock.
- **Resume is designed, not bolted on.** Atomic checkpoint writes (`core/comic/atomic.py`, `ProjectState.save` at `core/schemas.py` 1733–1763), schema version plus unknown-field round-trip (lines 1659–1664, 1755–1758), project file lock (`creative_comic.py` 764–772), and separate `timing.json` so the web layer does not race the pipeline on `state.json` (`web/server.py` 428–433).
- **Path checks on reference images.** Portrait and page refs are resolved and rejected unless they stay inside the project directory (`core/pipelines/reconcile.py` 29–39; used at `creative_comic.py` 1790, 1824–1826, 2265–2267, 2743). Symlinks that point outside fail the check.
- **Web input hardening that already exists.** Project ids are `^[a-zA-Z0-9_-]{1,64}$` (`web/server.py` 93, 266–270). Experiment ids are a single path segment plus a resolved-parent check (lines 750–763). POST bodies are capped (`ui_max_body_bytes`, default 8 MiB). Naive cross-site POSTs are rejected (`tests/test_web_server.py` 428–447). Bind address defaults to `127.0.0.1`, and a non-loopback bind prints a warning (`web/server.py` 1515–1524).
- **Error redaction exists.** `core/api/error_collector.py` 30–51 masks `Authorization: Bearer`, `api_key=`, and `sk-…` before the JSONL write.
- **Content-policy handling is deliberately strict.** HTTP 400 alone does not skip a page (`core/screenwriter.py` 463–477; `tests/test_safety.py`). That avoids caching a parameter bug as a permanent content rejection.
- **Harness tripwire.** `core/harness/tripwire.py` refuses to put quality-metric phrases into the system prompt or tool schema.
- **CI is reproducible.** `uv sync --extra dev --frozen`, ruff lint and format, pytest on 3.10/3.11/3.12, plus a non-editable install smoke (`.github/workflows/ci.yml`). `.env`, `.agnes_config/`, `logs/`, and `comic_out/` are gitignored.
- **Test volume is real.** 1192 tests, including web path traversal (`tests/test_web_server.py` 844–848), state integrity, resume, ledger, and provider retry. The suite is built to run with no network (`conftest.py`).

---

## 1. Architecture and project structure

### A1 — Orchestration is one function

- **Severity:** Medium
- **Where:** `core/pipelines/creative_comic.py` lines 1091–2997 (`_creative_comic`); file is 2997 lines. Neighbors of the same shape: `core/comic/visual_bible.py` (2076), `core/schemas.py` (2003), `core/cli.py` (1689), `web/index.html` (2153), `web/server.py` (1535).
- **Why:** The pipeline’s control flow (segment, extract, portraits, bible, beats, page plan, render, letter, export, both render modes) is one coroutine closing over a large set of locals. A resume bug or a new stage has to be edited inside that span. The stage registry and CAS modules already describe stages as data; the runner still mostly inlines them.
- **Fix:** Split `_creative_comic` along the stage boundaries that `core/pipelines/stage_registry.py` already names (`extract`, `bible`, `portrait`, `page_plan`, `render`, `letter`, `export`). Keep `creative_comic()` as the public façade that builds `ComicRunOptions` and holds the project lock. Move lettering’s private helpers out of the orchestrator’s import list (see A2). Do this only when a behavior change needs it; a drive-by split will fight the stage-source hashes.

### A2 — Stage hashes import private lettering helpers

- **Severity:** Low
- **Where:** `core/pipelines/creative_comic.py` lines 102–115 and 486–503 (`_LETTER_SOURCES`).
- **Why:** The lettering stage hash intentionally includes `_avoid_overlap`, `_clamp_box`, and the other `_`-prefixed functions from `core/comic/page_lettering.py`, so an edit invalidates lettered pages. The cost is that the pipeline module is coupled to another module’s private surface. Renaming a helper changes resume behavior.
- **Fix:** Have `page_lettering.py` export one public tuple, e.g. `LETTERING_STAGE_FUNCTIONS`, and hash that. The pipeline should not name private functions.

### A3 — Two render paths remain live

- **Severity:** Low
- **Where:** `creative_comic.py` module docstring lines 3–10; `core/config.py` `render_mode()` lines 264–268; legacy branch continues through the panel loop near line 2730.
- **Why:** Finished-page is the default and the README’s contract. `panel_compose` is still a full second painter inside the same function. Every resume, fingerprint, and skip rule has to be right twice. That is an honest compatibility choice, and it is also why A1 is expensive.
- **Fix:** Keep the flag, but put the legacy painter in `core/pipelines/panel_compose.py` with its own tests already living in `tests/test_creative_comic.py` and `tests/test_finished_page_pipeline.py`. Delete it only when a release note says the fallback is gone.

---

## 2. Security and secret management

### S1 — Local UI is unauthenticated, and the CSRF check trusts the Host header

- **Severity:** High
- **Where:** `web/server.py` `do_GET` file route lines 1123–1136; origin check lines 1277–1294; bind warning lines 1512–1526. Novel text is written to `comic_out/<id>/source.txt` at `core/pipelines/creative_comic.py` 1254–1258. Review prompts land under `review/` (`core/comic/review_packet.py` lines 8–16). `GET /api/job/<id>` returns the log, panel URLs, and error string (server lines 1170–1208).
- **Why:** The server is meant to be loopback-only, and the default host is `127.0.0.1`. There is still no shared secret. Any local process, and any browser page that can get the browser to send `Origin: http://<Host>` for a host that resolves to loopback, can:
  - `POST /api/generate` and spend the Agnes (or OpenAI-compat) quota for up to `INKSTONE_RUN_DEADLINE_HOURS` (default 24).
  - `GET /files/<project>/source.txt`, `state.json`, `cache.json`, and `review/prompts/*.json`.
  - `POST` review, ledger confirm, regen, experiment pick, and sequence advance.

  The origin check compares `Origin` to `http(s)://` plus the request’s own `Host` header. A DNS-rebinding page whose name resolves to `127.0.0.1` presents a matching Origin and Host, and `Sec-Fetch-Site` is `same-origin` after the page loads. `tests/test_web_server.py` 428–455 covers a foreign Origin and a missing Origin; it does not cover “Origin and Host are the same attacker-controlled name”. Missing `Origin` is allowed on purpose so curl works, which also means any non-browser client on the machine can call the API.

  Binding to `0.0.0.0` is warned about (lines 1520–1524) and is worse: the same routes are then reachable without rebinding.
- **Fix:**
  1. On startup, mint a secret (file mode `0600` under `data_dir()`, or print a one-time token). Require it on every `/api/*` and `/files/*` request as a header, or set it as a cookie from `/` with `SameSite=Strict`.
  2. Stop reflecting `Host`. Allow only `127.0.0.1`, `localhost`, and `[::1]` plus the configured port, unless the operator sets an explicit public origin.
  3. Allowlist `/files/` to generated artifacts (`.png`, `.pdf`, `.cbz`, `reader.html`). Do not serve `source.txt`, `state.json`, `cache.json`, `review/`, or `consistency.json` on that route. Give the UI a small JSON status route for the fields it already receives on `/api/job`.
  4. Add `Content-Security-Policy: default-src 'self'` and `X-Content-Type-Options: nosniff` on responses. `reader.html` is script-bearing (`core/comic/reader.py` 64–106); a CSP that allows that inline script should be explicit.

### S2 — Image fetches follow provider-supplied URLs, including redirects

- **Severity:** High
- **Where:** `utils/image.py` `download_image` lines 28–57; called from `ImageOutput.save` in `core/api/image_provider.py` lines 40–44. The URL comes from the provider JSON (`core/api/agnes_image.py` 190–209). `OPENAI_COMPAT_BASE_URL` is an arbitrary host (`.env.example` lines 29–33, `core/config.py` 297–298).
- **Why:** After a successful generation the process `GET`s `data[0].url` with the machine’s network position. `requests` follows redirects by default. There is no scheme or host allowlist. A compat gateway (the documented hedge against Agnes) that returns `http://169.254.169.254/...` or `http://127.0.0.1:<internal>` causes this process to fetch it and write the body under the project, where S1 will serve it. The download correctly omits the API key (comment at `image_provider.py` lines 8–9). That does not stop the pivot: the key was already sent to the attacker-controlled gateway on the POST; the GET is extra access to the user’s network. A compromised or malicious image CDN in front of a trusted provider is the same shape. Size is capped at 50 MiB (line 22), which limits disk fill and does not limit the target.
- **Fix:** Before `session.get`, require `https`, parse the host, and resolve it. Refuse loopback, link-local, private, and multicast addresses (check every redirect hop the same way; set `allow_redirects=False` and re-validate). Prefer an allowlist: the Agnes image host plus an explicit `INKSTONE_IMAGE_DOWNLOAD_HOSTS` when using compat. Keep the size cap. Add a test that a `127.0.0.1` URL and a redirect to `169.254.169.254` raise.

### S3 — Prompts and signed image URLs are written to disk and to the job API

- **Severity:** Medium
- **Where:** `core/api/error_collector.py` lines 96–112 (`prompt` up to 5000 chars, `response_body` up to 5000, redaction regex lines 31–35). `utils/image.py` line 31 logs the full download URL at INFO. The web job handler is attached to the root logger (`web/server.py` 180–188, 465–474) and `GET /api/job` returns the last 200 lines (lines 1187).
- **Why:** Redaction catches bearer tokens and `sk-` keys. It does not catch presigned query strings (`X-Amz-Signature`, `sig`, `token`) that image CDNs put on the URL. Those lines are persisted in `logs/*.jsonl` (gitignored, but mode follows the umask; see S4) and, while a job runs, copied into the UI log. The prompt field is the novel excerpt or the image prompt, which is the user’s manuscript.
- **Fix:** Redact URL query strings in `_redact_secrets` and in the download log (log host and path, drop the query). Stop attaching the job handler to the root logger; attach it to `inkstone` / `core` loggers only. Truncate prompts in the JSONL further, or hash them, when the log is not an explicit debug session.

### S4 — Checkpoints and the manuscript are typically world-readable

- **Severity:** Medium
- **Where:** `core/comic/atomic.py` lines 22–26 and 50–59 (`mode = 0o666 & ~umask`, usually `0644`). `ProjectState.save` uses that helper (`core/schemas.py` 1622–1626, 1733). `source.txt` is a normal `write_text` (`creative_comic.py` 1255–1258). Error JSONL uses `open(..., "a")` (`error_collector.py` 111).
- **Why:** On a multi-user machine, mode `0644` lets other local accounts read `state.json`, `cache.json`, `source.txt`, and error logs. The atomic writer even preserves an existing mode, so a later lock-down sticks, but the first save follows the umask. API keys are not stored in those files (they stay in the environment). The manuscript and the prompts are.
- **Fix:** Pass an explicit `0o600` for `source.txt`, `state.json`, `cache.json`, `review/`, and `logs/*.jsonl`. Keep the “preserve existing mode” behavior only when the existing mode is already owner-only. The comment at the top of `atomic.py` explains why `mkstemp`’s `0600` was relaxed (other local workers). Make that opt-in via umask or a group, not the default.

### S5 — `scripts/start.sh` executes `.env` as shell

- **Severity:** Medium
- **Where:** `scripts/start.sh` lines 23–28 (`set -a; source .env`). The web loader is a literal parser (`web/server.py` 161–177) and does not expand commands.
- **Why:** `source` runs command substitutions and backticks in the file. A `.env` copied from a gist, a chat, or a compromised editor will execute at launch. The Python path was written to avoid that class of surprise and the shell path does not use it.
- **Fix:** Load `.env` with the same line parser as `_load_dotenv` (or `uv run --env-file .env`) and export only `KEY=value` pairs. Do not `source` the file.

### S6 — `.env.example` documents a config file the code never reads

- **Severity:** Medium
- **Where:** `.env.example` lines 6–9 tell the user to write the key into `.agnes_config/config.json`. A repo search shows that path only in this example and in `.gitignore` line 222. `get_image_provider` / `get_chat_provider` read `AGNES_API_KEY` from the environment and then raise `RuntimeError("... no AGNES_API_KEY found (env or config)")` (`core/api/image_provider.py` 242–245, `core/api/chat_provider.py` 259). Nothing opens `config.json`.
- **Why:** A user who follows the comment and does not export the variable gets a runtime error that the key is missing, after putting a live secret in a side file. The error text says “env or config”, which matches the example and does not match the loader. The directory is gitignored, so the secret is unlikely to be committed. The instruction is still wrong.
- **Fix:** Delete the `.agnes_config` sentence. Say that the key must be in the environment or in repo-root `.env`, and point at the loader that actually runs (`scripts/start.sh` and `web/server.py` `_load_dotenv`).

### S7 — Skipped-panel keys are interpolated into HTML

- **Severity:** Low
- **Where:** `web/index.html` line 1965: `row.innerHTML = "<span>panel " + key + "</span>"`. The rest of the experiment UI uses `esc` (line 1061), which escapes `& < > " '`.
- **Why:** `job.skipped` is pipeline-generated today, so this is latent. If a key ever contains `<img onerror=...>` the local UI will execute it. Same-origin as S1, so a poisoned `state.json` becomes script.
- **Fix:** Use `textContent` the way `renderReview` already does (lines 1821–1823), or pass the key through `esc`.

### S8 — Access logs are discarded

- **Severity:** Low
- **Where:** `web/server.py` `log_message` lines 1105–1106 (`pass`).
- **Why:** After S1, there is no record of who hit `/api/generate` or `/files/`. `server_version = "InkstoneUI/0.1"` also advertises the product on every response.
- **Fix:** Log method, path, and status at INFO without bodies or headers. Set `server_version` to a generic token.

---

## 3. Error handling and logging

### E1 — The supervisor treats any `RuntimeError` containing `"timeout"` as retryable for up to 24 hours

- **Severity:** Medium
- **Where:** `core/pipelines/run_until_complete.py` `is_transient_error` lines 62–77 (`_TRANSIENT_MARKERS` includes `"timeout"` and `"timed out"`, lines 42–43). The loop is lines 210–252. Default deadline is 24h (`core/config.py` `run_deadline_hours`, line 243).
- **Why:** Classification is a substring search on `str(exc)`, and only for `RuntimeError`. A permanent bug whose message happens to include “timeout” (a bad request echoed by the provider, a local “lock timeout”, a test double) is retried with backoff until the wall clock. Each attempt can issue more billable calls if the failure happened before the checkpoint advanced. The inverse is also true: a transient error that is not a `RuntimeError`, `Timeout`, `ConnectionError`, or `HTTPError` is fatal. `BudgetPaused`, `GatePaused`, and `ContinuityReviewPaused` are excluded correctly (lines 187–199).
- **Fix:** Retry on exception type (`requests.Timeout`, `requests.ConnectionError`, HTTP status in `RETRYABLE_STATUS`) and on an explicit marker the retry layer sets. Do not scan arbitrary message text for `"timeout"`. Cap supervisor attempts as well as wall time.

### E2 — Job records are mutated off the lock that readers hold

- **Severity:** Medium
- **Where:** Writes in `_run_job` at `web/server.py` 502–518 (`_fill_job_from_project`, then `job["status"] = "done"`) sit outside `JOBS_LOCK`. Readers copy fields under the lock (lines 1182–1207). Progress updates do take the lock (lines 476–483). The root logger level is process-global (lines 191–220).
- **Why:** CPython will not tear a single pointer assign, but status, panels, pdf, and error are separate keys. A poll can observe `status=done` with the previous panel list, or a new panel list with `status=running`. Two overlapping jobs both set the root logger to INFO and restore it by depth count; that part is careful. A library logger at INFO still lands in every job buffer because the handler is on the root (see S3).
- **Fix:** Build the finished snapshot on the side and assign it in one critical section. Attach log handlers to named loggers. Keep the depth counter.

### E3 — Policy detection and failure reporting disagree

- **Severity:** Medium
- **Where:** `is_content_policy_rejection` in `core/screenwriter.py` 463–477 (only `content_policy`, `content policy`, `policy_violation`). Call sites in `creative_comic.py` swallow `Exception` and then branch on that predicate (for example 1595–1598, 2365–2369, 2597). The UI stores `job["error"] = str(exc)` (`web/server.py` 511–514).
- **Why:** The strict predicate is a good fix for “every HTTP 400 becomes a permanent skip” (`tests/test_safety.py`). A provider body that says `content filter` or `safety` (the words the log lines themselves use, e.g. `creative_comic.py` 1499) is not recognized, so the chunk aborts the run instead of being skipped. Separately, `str(exc)` on `requests.HTTPError` often includes the response body, and that string is returned to the browser with no redaction step. Redaction runs only inside the error collector.
- **Fix:** Centralize a small allowlist of policy codes and phrases, including the ones Agnes actually returns, and unit-test the live samples you have in `logs/`. Run `_redact_secrets` on any exception string before it enters `job["error"]` or a `print`.

### E4 — Several “best effort” handlers swallow everything

- **Severity:** Low
- **Where:** Examples: `web/server.py` 349–351 and 392–402 (checkpoint load), 452 (timing persist); `run_until_complete.py` 109–112; `error_collector.py` 121–123.
- **Why:** Most of these are annotated and fall back on purpose (a corrupt `cache.json` must not block progress). The collector’s bare `Exception` is right: logging must not break generation. The risk is the ones that `return None` or `return 0.0` with only a warning, which looks like “no checkpoint” or “elapsed time is zero” and can cause a redraw or a reset clock.
- **Fix:** Distinguish `ValidationError` / `JSONDecodeError` / `OSError` from unexpected exceptions. Log the unexpected ones at ERROR with the path. Keep the fallback.

---

## 4. API design and data validation

### V1 — HTTP bodies are untyped dicts, and bad enums are rewritten

- **Severity:** Medium
- **Where:** `web/server.py` `_post_generate` lines 1307–1329 (`format` not in `page|webtoon` becomes `webtoon`), `_post_regen` 1494–1496, experiment compose 1350–1364 (components passed through if they are a list). The pipeline’s real contracts are Pydantic models in `core/schemas.py`.
- **Why:** A client that sends `"format": "pdf"` or `"cbz"` gets a webtoon and a 200. The CLI accepts `cbz` (`README` and `creative_comic` docstring line 1100). The UI silently picks a different product. `style_guide` is an unbounded string inside the 8 MiB body and is concatenated into prompts. Experiment `components` are not checked against the harness id grammar before `compose_harness`.
- **Fix:** Add a small Pydantic model per POST (`GenerateRequest`, `RegenRequest`, `PickRequest`) and return 400 on extra or invalid fields. Accept `cbz` or reject it explicitly. Cap `style_guide` (a few thousand characters). Validate `components` against `stage.<name>` ids the harness already knows.

### V2 — `ast.literal_eval` runs on model output with no size bound

- **Severity:** Medium
- **Where:** `core/schemas.py` `coerce_jsonish` lines 41–84, especially 72–76.
- **Why:** Tool arguments that are not JSON fall through to `json_repair` and `ast.literal_eval`. `literal_eval` does not run arbitrary code. It does parse attacker-controlled literals. A hostile or prompt-injected model reply can be a multi-megabyte nested list and stall the process. `MemoryError` is caught; CPU and recursion are not. This is on the hot path for every chat stage.
- **Fix:** Cap the string (for example 1 MiB, and a maximum nesting depth) before repair or `literal_eval`. Prefer `json.loads` plus one `json_repair` pass. Drop `literal_eval` unless a recorded payload still needs Python literals, and test that payload.

### V3 — Query strings are part of the route key

- **Severity:** Low
- **Where:** `web/server.py` `do_GET` uses `self.path` raw (lines 1108–1161). `BaseHTTPRequestHandler.path` includes `?query`.
- **Why:** `/api/health?ready=1` 404s. `/api/job/<id>?x=1` looks up the wrong id. Not a traversal bug (ids are checked, and `%2e%2e` is covered by tests), but it makes health checks and proxies brittle.
- **Fix:** Split on `?` once in `do_GET` / `do_POST` and route the path only.

---

## 5. Test coverage and test quality

### T1 — Coverage is broad and unmeasured

- **Severity:** Medium
- **Where:** `pyproject.toml` pytest `addopts = "-q"` (lines 52–55). CI runs `uv run pytest` only (`.github/workflows/ci.yml` lines 34–35). No `pytest-cov`, diff-cover, or coverage artifact. 127 test modules, 1192 test functions.
- **Why:** The suite is a strength: providers, schemas, resume, ledger, web path checks, and harness specs are tested without network. There is no number for what `_creative_comic`’s 1900-line body does not hit. Regressions in an untested branch of the orchestrator will not fail CI. `test_creative_comic.py` has 13 tests; many other modules drive the pipeline indirectly, which is good and still invisible.
- **Fix:** Add `pytest-cov` in the dev extra and upload `coverage.xml` from CI. Start with a floor on `core/api`, `web/server.py`, and `core/pipelines/run_until_complete.py`, not a vanity gate on the whole repo. Fail the job when those packages drop, and ratchet the orchestrator after A1 splits it.

### T2 — Pre-commit ruff does not match CI, and pytest is not a hook

- **Severity:** Medium
- **Where:** `.pre-commit-config.yaml` pins `ruff-pre-commit` at `v0.6.9` (lines 1–7). `pyproject.toml` dev dependency is `ruff>=0.17.2` (line 28). CI uses the locked project ruff (`.github/workflows/ci.yml` 28–32). `CONTRIBUTING.md` lines 53–60 says installing hooks runs `ruff check`, `ruff format --check`, and `pytest`. The hook file does not run pytest.
- **Why:** A commit can be formatted by ruff 0.6.9 and rejected by 0.17 on CI, or the reverse. Contributors who trust the contributing guide will think pytest ran locally when only whitespace hooks ran.
- **Fix:** Pin the hook to the same ruff version as `uv.lock`. Either add a pytest hook or change CONTRIBUTING to say hooks are ruff-only and pytest is CI / `uv run pytest`.

### T3 — The SPA is not exercised

- **Severity:** Low
- **Where:** `web/index.html` (2153 lines, inline script from line 940). `tests/test_web_server.py` has 54 tests and they drive the Python handler.
- **Why:** XSS (S7), demo-vs-live mode, and the A/B tab are untested in a browser. Server tests will stay green if `esc` is bypassed.
- **Fix:** One Playwright (or the existing `.playwright-cli` ignore suggests it was used) smoke: load `/`, confirm demo mode without a key, and with the server assert that a skipped key containing `<` is text. Not a substitute for T1.

### T4 — Test quality habits that are worth keeping

- **Severity:** Low (observation, not a defect)
- **Where:** `conftest.py` lines 12–30; `tests/test_safety.py`; `tests/test_web_server.py` origin tests; `tests/test_image_download_proxy.py`.
- **Why:** Sleep and rate limits are stubbed so the suite stays fast. Safety tests assert that a bare HTTP 400 is not a policy skip. Download tests use a real local socket instead of mocking `requests.get` incorrectly. New tests should follow that, and should not add a marker substring to `is_transient_error` without a negative case (E1).

---

## 6. Performance and scalability risks

### P1 — Each generate starts a daemon thread, with no cap

- **Severity:** Medium
- **Where:** `web/server.py` `_start_job` lines 560–564. Each thread calls `asyncio.run(run_until_complete(...))` (lines 489–501). Image concurrency defaults to 3 (`core/config.py` 305). The supervisor will keep going for 24h (E1).
- **Why:** `POST /api/generate` does not check how many jobs are already running. Combined with S1, a browser loop can open many books, each holding threads, sockets, and up to 3 in-flight image calls, all sharing one rate limiter. The limiter protects Agnes RPM. It does not protect this machine’s memory or the user’s daily quota. Daemon threads also die on interpreter shutdown mid-flight; atomic saves make that survivable, but an in-progress image is simply lost.
- **Fix:** Refuse new work with 429 when a running-job count exceeds a small limit (1 is enough for the interactive UI; make it `INKSTONE_MAX_JOBS`). Use non-daemon threads and stop them via the existing `cancel_event` on server shutdown.

### P2 — Webtoon memory guard can be turned off, and file responses are fully buffered

- **Severity:** Medium
- **Where:** `core/config.py` `webtoon_max_pixels` lines 191–200 (`0` is a valid return). `core/comic/layout.py` `_compose_webtoon` lines 196–204 (“0 disables the guard”). Default cap is 200_000_000 pixels (`config.py` 182), about 600 MB of RGB before PNG compression. `Handler._send_file` reads the whole file (`web/server.py` 1096–1103).
- **Why:** A long novel in webtoon format builds one canvas. The guard is the right idea. `INKSTONE_WEBTOON_MAX_PIXELS=0` removes it, and the error text tells users to do that. Serving the resulting PNG or PDF copies it into a `bytes` object per request, on a threaded server, so two readers double the cost.
- **Fix:** Treat `0` as “use the default”, or require a separate `INKSTONE_WEBTOON_UNLIMITED=1` so the footgun is not a numeric zero. Stream `/files/` with `self.wfile` and a bounded buffer, or `shutil.copyfileobj`.

### P3 — Rate-limiter RPM is frozen at first use

- **Severity:** Low
- **Where:** `core/api/rate_limiter.py` `get_rate_limiter` lines 137–147. `reset_rate_limiter` exists for tests (lines 150–153).
- **Why:** The first call builds a `RateLimiter` with the RPM at that moment and stores it forever. Tests that forget the reset, or a process that changes `AGNES_RATE_LIMIT` after a call, keep the old ceiling. The 0.8 safety factor and tier split are otherwise sound, and the lock is not held during sleep.
- **Fix:** Key the cache by `(bucket, tier, rpm)` or rebuild when `select_rpm` changes.

### P4 — Sequence advance blocks an HTTP worker

- **Severity:** Low
- **Where:** `web/server.py` `_post_sequence_advance` lines 1403–1409 (`asyncio.run` inside `do_POST`).
- **Why:** `ThreadingHTTPServer` handles this on a worker thread, so other requests still run. The advance is a multi-call chat probe. The UI has no timeout of its own. A second click starts a second advance if the client retries.
- **Fix:** Run advance on the same job machinery as generate (P1), return a job id, and poll.

---

## 7. Maintainability and code style consistency

### M1 — User-facing CLI text is Chinese; the project standard is English

- **Severity:** Medium
- **Where:** `core/cli.py` module docstring lines 1–14 (Chinese) and user-facing prints such as lines 547–561, 660–668, 717–726, 774. `CONTRIBUTING.md` line 92: “all code, comments, docs, and commit messages are in English.” `docs/ONBOARDING.md` line 7 repeats that. Help text for the parser is English (`cli.py` 34–36). `AGENTS.md` is a single Chinese sentence about ruff.
- **Why:** Operators see a mixed language CLI. New contributors cannot tell which language a new message should use. The rule and the largest user surface contradict each other.
- **Fix:** Pick one. If English is the contract, translate the prints and the module docstring, and keep a `--lang zh` only if you still need it. If Chinese is the product language, change CONTRIBUTING and ONBOARDING so they stop claiming otherwise. Do not translate inside an unrelated PR.

### M2 — Ruff covers style, not architecture

- **Severity:** Low
- **Where:** `pyproject.toml` lines 42–47: lint select is `E, F, I, UP, B`. Line length 100. No `mypy` or `pyright` config. No `ANN` / complexity rules.
- **Why:** Format and import order are consistent, which is why the tree feels even. The 3000-line function (A1) and the Chinese strings (M1) are invisible to ruff. Public functions are mostly annotated; the HTTP layer is not, because it is dict-shaped (V1).
- **Fix:** After A1, consider `mypy` on `core/api` and `core/schemas.py` only. Do not turn on a repo-wide type gate in one step.

### M3 — `AGENTS.md` is a single instruction, easy to miss next to CONTRIBUTING

- **Severity:** Low
- **Where:** `AGENTS.md` (one line: run `uvx ruff format .` after writing code). CI already runs `ruff format --check`.
- **Why:** Agents and humans can format with a different ruff than CI if they follow `uvx` (latest) instead of `uv run ruff` (locked). That is the same skew as T2.
- **Fix:** Point `AGENTS.md` at `uv run ruff format .` and `uv run ruff check .`.

---

## 8. CI/CD, deployment, and environment configuration

### C1 — Web UI settings are read before `.env` is loaded

- **Severity:** Medium
- **Where:** `web/server.py` lines 83–91 assign `EXPERIMENTS_DIR`, `HOST`, `PORT`, and `MAX_JSON_BODY_BYTES` at import. `_load_dotenv()` runs later in `main()` at line 1516. Provider keys are read later still, inside the job, so `AGNES_API_KEY` in `.env` works. `job_ttl_seconds()` is called per request and also works.
- **Why:** `INKSTONE_UI_HOST`, `INKSTONE_UI_PORT`, `INKSTONE_UI_MAX_BODY_BYTES`, and `INKSTONE_EXPERIMENTS_DIR` in `.env` are ignored. The process keeps `127.0.0.1:8000`, an 8 MiB cap, and `comic_out/experiments` under the cwd at import. The comment above line 85 says these exist so the env registry can see them. The registry is correct; the snapshot is too early. A user who sets a port only in `.env` will think the server failed to start when something else is already on 8000, or will look for experiments in the wrong directory.
- **Fix:** Call `_load_dotenv()` at the top of `main()` before reading those four values, and pass them into the handler (or set them on the server object) instead of caching them at import. Add a test that a temp `.env` with `INKSTONE_UI_PORT` changes the bound port. Keep import-time defaults for unit tests that import the module without `main()`.

### C2 — Dependabot is configured for pip while CI installs from `uv.lock`

- **Severity:** Medium
- **Where:** `.github/dependabot.yml` lines 3–7 (`package-ecosystem: "pip"`). CI installs with `uv sync --frozen` (`.github/workflows/ci.yml` lines 26 and 50). The lockfile is `uv.lock` and is not gitignored (`.gitignore` lines 96–101 comment that it should be committed).
- **Why:** A pip-ecosystem bump can edit `pyproject.toml` and leave `uv.lock` stale. The frozen sync then fails, or worse, a hand-fixed PR drifts from the lock. GitHub Actions updates are configured and are fine.
- **Fix:** Switch the Python ecosystem to Dependabot’s `uv` ecosystem (or enable it beside pip and disable pip). Confirm the first PR updates `uv.lock` and passes `uv sync --frozen`.

### C3 — Pages workflow is a generation behind CI, and actions are not pinned

- **Severity:** Low
- **Where:** `.github/workflows/pages.yml` line 25 uses `actions/checkout@v4`. `ci.yml` line 17 uses `actions/checkout@v7`. Pages also uses `actions/upload-pages-artifact@v3` and `actions/deploy-pages@v4` (lines 32–44). Permissions are tight (`contents: read`, `pages: write`, `id-token: write`, lines 12–15), which is right.
- **Why:** Two checkout majors drift. Tag pins move when the action owner moves the tag. The workflow only copies `web/index.html` and `assets/samples`, so the blast radius is the GitHub Pages site, not the PyPI artifact. Demo mode on Pages does not receive the API key, which is correct.
- **Fix:** Use `actions/checkout@v7` in Pages to match CI. Pin third-party actions by SHA when you next touch the file. Keep the `permissions` block as it is.

### C4 — CI does not typecheck, audit, or run on 3.13+

- **Severity:** Low
- **Where:** `.github/workflows/ci.yml` matrix lines 12–15 (`3.10`, `3.11`, `3.12`). `requires-python = ">=3.10"` (`pyproject.toml` line 10). No `mypy`, `pip-audit`, or `bandit` step. No release workflow; version is `0.1.0` in `pyproject.toml` and the README badge.
- **Why:** In October 2026, 3.13 and 3.14 are the interpreters many users will have. The suite never imports them here. Dependency CVEs wait for the weekly Dependabot PR (C2). That is acceptable for an app with a small, pinned dependency set (`requests`, `tenacity`, `Pillow`, `numpy`, `pydantic`, `json-repair`, `tqdm`, `pypdf`) and a bad fit if C2 is not actually updating the lock.
- **Fix:** Add 3.13 to the matrix (3.14 when plugins allow). Add `uv run pip-audit` or `uv export | pip-audit` once C2 is fixed. Skip a typecheck gate until M2’s narrow config exists.

### C5 — Environment story is otherwise coherent

- **Severity:** Low (observation)
- **Where:** `.env.example`, `core/config.py`, `core/comic/env_snapshot.py`.
- **Why:** Defaults are safe (loopback UI, L3 off, page script off, harness on, 24h deadline, proxy opt-in via `INKSTONE_HTTP_PROXY` only). The `h_env` split between “changes bytes” and “does not” is documented in code and enforced by a test. C1 and S6 are the holes in that story. Colab (`scripts/colab_run.sh`, `docs/guides/colab-cli.md`) tells users not to commit `.env`; the script still has to be reviewed on its own before anyone pastes it into a notebook, because it shells out (`subprocess` with `shell=True` around the remote command, `scripts/colab_run.sh` near lines 367–375).

---

## Prioritized action list

Ordered by severity, then by how much damage or ongoing cost the item creates.

| # | Sev | ID | Action |
|---|---|---|---|
| 1 | High | S1 | Add a local auth token. Stop echoing `Host` into the allowed origin. Stop serving `source.txt`, checkpoints, and `review/` from `/files/`. |
| 2 | High | S2 | Allowlist image-download hosts and block private/link-local addresses on every redirect. |
| 3 | Medium | C1 | Load `.env` before binding the UI and before resolving the experiments directory. |
| 4 | Medium | S5 | Stop `source`-ing `.env` in `scripts/start.sh`. |
| 5 | Medium | S6 | Remove the `.agnes_config/config.json` instruction from `.env.example`. |
| 6 | Medium | E1 | Retry supervisor failures by exception type and status code, not by the substring `"timeout"`. |
| 7 | Medium | S3 | Redact signed URLs; do not put the root logger into the job buffer. |
| 8 | Medium | S4 | Write manuscript, state, and error logs as owner-readable (`0600`). |
| 9 | Medium | P1 | Cap concurrent web jobs and shut them down with the existing cancel event. |
| 10 | Medium | V1 | Validate POST bodies with Pydantic; do not coerce unknown `format` to `webtoon`. |
| 11 | Medium | E3 | Align policy phrases with real provider errors, and redact exception text shown in the UI. |
| 12 | Medium | E2 | Publish job completion under `JOBS_LOCK`. |
| 13 | Medium | P2 | Do not treat webtoon pixel cap `0` as unlimited; stream large files. |
| 14 | Medium | C2 | Point Dependabot at `uv.lock`. |
| 15 | Medium | T1 / T2 | Measure coverage for `core/api` and `web/server.py`; pin pre-commit ruff to the lockfile. |
| 16 | Medium | A1 | Split `_creative_comic` on the existing stage boundaries when the next pipeline change forces a touch. |
| 17 | Medium | M1 | Make CLI language match CONTRIBUTING, in one direction. |
| 18 | Medium | V2 | Cap and then remove `ast.literal_eval` on tool payloads. |
| 19 | Low | S7, S8, V3, A2, P3, P4, M2, M3, C3, C4 | HTML-escape panel keys, restore access logs, strip query strings, export lettering hash helpers, rebuild rate limiters when RPM changes, background the sequence advance, narrow typecheck, align Pages’ checkout major, add Python 3.13 to CI. |

Suggested first PR, still small: S6 (one comment), C1 (dotenv order plus a test), and S5 (shell loader). Second PR: S2 with tests, then S1. Do not start with the `creative_comic.py` split; it will mix behavior changes into a move.
