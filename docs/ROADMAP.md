# Inkstone Roadmap

> This file is the **single progress-control document** for humans and AI.
> It records what is released, what only exists locally, and what is approved next.
> Update it whenever implementation status changes.
> Inkstone is an independent, from-scratch implementation (not a fork), so we do not carry over the
> upstream `server.py` / video / audio modules; the comic-specific layer is written to this project's standard.

## How to read and update this roadmap

Status definitions:

- **Released**: committed to `main` and CI is green.
- **Prototype on `main`**: committed code that is experimental or estimate-only; not a product capability or quality gate.
- **Local prototype**: present only in a working tree; not a product capability.
- **Planned**: approved direction, not yet implemented.

Developer onboarding for the shipped tree is [`docs/ONBOARDING.md`](ONBOARDING.md).
The **target** architecture lives under `docs/architecture/` in the author's
working tree only: that directory is gitignored, so a fresh clone does not have
it and those paths are not links you can follow from here. The governing file is
`2026-09-28-content-addressed-pipeline-design.md`; its Chinese translation
(`…-design.zh-CN.md`) **lags as of 2026-10-19**, lacking the dated revisions from
2026-10-20 onward, so the English file governs.
Its three original blockers (L2 continuity cascade, dynamic page DAG, missing
tombstones) and its majors were verified against the shipped tree and have been
folded into the design (§2, §4, §6, §12); they are now design requirements rather
than open findings. The separate review document was never committed to this repo,
so it is not referenced here. Treat this roadmap, the onboarding guide, and the released code as
the source of truth for **current status**; the architecture document is the source
of truth for **where the design is heading**. Where they disagree, status wins until
the migration lands. Additional long-form drafts may still live under local
`docs/architecture/`.

When completing a change:

1. update the relevant item below;
2. do not mark an item Released until it is committed and CI passes;
3. explicitly record unfinished local experiments as Local prototype;
4. if the developer workflow or system boundary changes, update
   [`docs/ONBOARDING.md`](ONBOARDING.md) (and this roadmap) in the same change.

## Current implementation status

| Area | Status | Notes |
|---|---|---|
| Core TXT → comic pipeline | Released | Segmentation, extraction, portraits, storyboard, panels, layout, PDF / Webtoon export, `state.json` resume. **Default render mode is `finished_page`** (one image per comic page); `panel_compose` is the explicit legacy fallback (`INKSTONE_RENDER_MODE=panel_compose`). **Finished-page text:** deferred lettering — blank art + font overlay ([spec](superpowers/specs/2026-07-31-deferred-lettering-design.md)). |
| Providers and reliability | Released | Agnes + OpenAI-compatible routing, rate limit, retry and JSONL error collection. Default Agnes host is `https://apihub.agnes-ai.com/v1` ([Agnes 3.0 Flash](https://agnes-ai.com/en/docs/agnes-30-flash)); `AGNES_BASE_URL` overrides it. |
| Cross-chapter identity | Released | L1/L2 consistency, alias review, stale-only redraw; L3 is experimental and off by default |
| Web UI and unattended supervisor | Released | Local browser UI, cancel, retry, review, deadline pause / resume; job/project JSON exposes `render_mode`, `pages_done`, `skipped_pages` |
| Colab operations | Released | Background jobs, download progress, alias adopt after 404/401 |
| Page-PDF recovery and source-language dialogue prompt | Released | Existing panels can be re-exported to PDF; new runs request dialogue in source language |
| Density estimate (D1) | Prototype on `main` | Tiers set the planning budget. `plan` is still an uncalibrated page-count estimate |
| Old PageScript / coverage (D2) | Prototype on `main` — do not treat as gate | Opt-in via `INKSTONE_PAGE_SCRIPT=1`; coverage never vacuous-passes skips |
| Chapter-complete adaptation | Prototype on `main` | Source units, adaptation plans, beat ids, lettering, and `inkstone structure` are in tree. A chapter counts only when every check passes and a page is stored. The local Journey excerpt passes every check and has 8 stored pages |
| CBZ, chapter reader, identity CLI | Prototype on `main` | `--format cbz`, `inkstone read`, the web Read pages link, and `identity --view` are in tree. They are not a validated-chapter gate |

### Prototype guardrail (D1/D2 on `main`)

Do **not** present the current D1/D2 code as a completed quality feature:

- density tiers now set the planning budget and the structure fingerprint; the panel budget `3/8/14` is still an experience value. The page count is derived through `PANELS_PER_PAGE = 4`, the engine's `_paginate(per_page=4)` packing limit. That is an upper bound, not the planner's rate: planning splits on narrative beats, so measured pages come out well above `panels / 4` (the Journey excerpt: 14 panels, 8 pages, 1.75 per page). Pages are therefore measured whenever a project has them, and derived only when it does not;
- PageScript is created after storyboard, so it cannot restore omitted narrative beats;
- coverage currently checks non-empty fields and substring matches, not reader-visible information;
- policy-rejected pages must not be excluded from a final completeness denominator.

Keep these prototypes only as migration material until they conform to the target architecture.

## Next approved work

### P0 — Make the current state honest and deterministic

- **Local (2026-10-20, `main`):** Four more specs on the runner (`letter`,
  `layout`, `export` PDF and CBZ). Each keeps its own key derivation; only the
  replay check and the manifest write moved.
- **Local (2026-10-20, `main`):** Per-stage runner. `core/pipelines/stage_runner.py`
  implements the four properties §9 states, with the action key passed in rather
  than derived, the manifest stage resolved from `stage_registry` (so `webtoon`
  records under `export`), an empty key refused, and a non-ok manifest treated as
  settled. `layout` and `webtoon` now execute through it; the remaining ten stages
  still run inline. `tests/test_stage_runner.py` holds each property with an
  injected counterexample.
- **Local (2026-10-20, `main`):** Architecture evolution. Added the declarative
  stage catalog (`inkstone stages`, startup validation) and a manifest schema
  migrator (`inkstone migrate`, dry-run by default, refusing key/filename
  mismatches). `verify`/`migrate` also reject manifest stages outside the
  catalog, and `migrate --json` supports automation. These are the seams future
  per-stage runners and manifest upgrader work can use without another
  cross-cutting edit.
- **Local (2026-10-20, `main`):** Content-addressed design close-out.
  beats/page_plan/storyboard/page_script are manifest-backed, so a lost cache
  restores them instead of re-planning; finished-page rendering now prefers the
  ledger's authoritative reference path over a stale cached portrait. The design
  document was corrected to use runtime pauses (not `stopped`/`awaiting_human`
  manifests), deletion-based release, scoped `h_env`, schema-3 chat keys, and
  the six-field `cache.json`.
- **Local (2026-10-20, `main`):** Web continuity polish. The review panel lists
  pending pages as selectable chips, supports Confirm selected / Confirm all,
  and `POST /ledger/confirm` accepts a `pages` subset. A/B and sequence errors
  use a toast instead of `alert`. Candidate cards also show a page-level diff,
  and sequences show a round timeline. A final visual pass adds focus rings,
  mobile tab navigation, panel lightbox/keyboard support, carousel arrow keys,
  and reduced-motion handling. A persisted light/dark theme toggle and inline
  ◆ A/B diff markers complete the visual pass. Regression:
  `tests/test_web_server.py`.
- **Local (2026-10-20, `main`):** Final review batch. `h_env` is split by stage
  scope; budget specs must fund every charged stage; partial continuity
  confirmation is available through `identity --confirm --page`; failed alias
  merges roll back state and ledger; `source_units`/`adaptation_plans` moved to
  `cache.json`; and the chat key argument surface is locked by a contract test.
  D-07 was verified: CLI gate and GC already search the shared and project gate
  locations, so no code change was needed.
- **Local (2026-10-20, `main`):** D-13. The finished-page resume branch now
  short-circuits on the same evidence as the panel branch and no longer requires
  `visual_bible`. A best-effort bible failure cannot force a completed chunk
  through a no-op page traversal. Regression:
  `tests/test_finished_page_pipeline.py`.
- **Local (2026-10-20, `main`):** D-12 + D-14. A new day advances the budget's
  stored `day`/`paused_key` even without carry-over (spent counts still reset
  only with carry-over), so a stale boundary cannot suppress the new day's stop
  record. `GateSession._hold` records a changed resume boundary once per
  session while still emitting one stop for multiple beyond-sample pages in a
  single run. Regression: `tests/test_budget.py`, `tests/test_gate.py`.
- **Local (2026-10-20, `main`):** D-20 + D-21. `clear_tombstones` now removes
  the key from `pages_done` too, so a released page is actually retried.
  `page_state_key` enforces the four-digit range, `ensure_character_l1` is
  idempotent for evidence/warning suffixes, merge chains resolve to the final
  canonical, the dead panel loop is gone, and the Web review merge maps a
  disappeared kept name to 400. Regression: `tests/test_identity.py`,
  `tests/test_phase0b_page_identity.py`, `tests/test_tombstones.py`,
  `tests/test_web_server.py`.
- **Local (2026-10-20, `main`):** D-08 + D-09. Legacy page-key migration is now
  read-time behavior: `ProjectState.load()` migrates before returning, so the
  supervisor, Web fallback, GC, prune, and CLI readers cannot miss it. Key
  collisions keep the canonical positional record and log the discarded one;
  duplicate plan `page_id`s warn and keep the first position; the page lists are
  deduplicated. Regression: `tests/test_phase0b_legacy_migration.py`.
- **Local (2026-10-20, `main`):** D-19 + D-22. `INKSTONE_PDF_BATCH` now lives in
  `core.config` (`ENV_PDF_BATCH` / `pdf_batch_size()`), is registered in
  `ALL_ENV_VARS`, and is excluded from `h_env` as a memory-only knob. Scheduler
  docs now state the real split: the plan pass is graph-driven, while render and
  export are graph-validated and sequentially dispatched. This closes the
  review's remaining documentation/config items.
- **Local (2026-10-20, `main`):** D-17 + D-23. The resolved font digest is now
  cached by path + mtime + size, removing the render path's repeated
  multi-megabyte font reads. Atomic writes share `core/comic/atomic.py`: new
  files get the process umask, existing modes are preserved, the parent
  directory is fsynced, and the temp fd cannot leak. Regression:
  `tests/test_fonts.py`, `tests/test_atomic.py`.
- **Local (2026-10-20, `main`):** `INKSTONE_HARNESS=0` one-release fallback.
  It forces the builtin tree, ignores the harness dir, forces `general` genre,
  and disables craft lessons; the review manifest records
  `harness_fallback: true`. This closes the prompt-harness roadmap. Regression:
  `tests/test_finished_page_pipeline.py`, `tests/test_config.py`.
- **Local (2026-10-20, `main`):** Sequence Web view. The A/B tab lists
  sequences above experiments; opening one shows the current round's anonymous
  plan cards, records a pick, and advances to the next round from the same
  page. APIs: `GET /api/sequences`, `GET /api/sequence/<id>`,
  `POST /api/sequence/<id>/pick|advance`. Regression:
  `tests/test_web_server.py`. Remaining: `INKSTONE_HARNESS=0` fallback.
- **Local (2026-10-20, `main`):** Sequential recursion.
  `experiment sequence plan|advance|status` carries a composed checkpoint
  through N rounds: each round probes the current checkpoint plus the same
  candidate directories, `advance` composes the human pick into
  `rounds/<n>/checkpoint`, and the next round starts from it. Each round is a
  normal experiment packet. Regression: `tests/test_experiment.py`. Next:
  sequence view in the Web A/B tab / `INKSTONE_HARNESS=0` fallback.
- **Local (2026-10-20, `main`):** Project-level harness override.
  `generate --harness-dir` / `INKSTONE_HARNESS_DIR` runs all six chat stages with
  a validated tree; the digest lands in the review manifest and the request
  digest in each chat key. The image renderer/export keys are unchanged;
  `inkstone prompt --harness-dir` covers every stage. Regression:
  `tests/test_finished_page_pipeline.py`, `tests/test_cli_prompt.py`. Next:
  sequential recursion.
- **Local (2026-10-20, `main`):** Web compose button. The A/B card composes the
  picked tree into `<experiment>/adopted/` via
  `POST /api/experiment/<id>/compose`, with an optional `stage.page_plan` merge
  from the other candidate. The browser cannot choose the output path; the
  server fixes it under the experiment. Regression:
  `tests/test_web_server.py`.
- **Local (2026-10-20, `main`):** Candidate-level A/B + compose. The default
  experiment now runs extract/beats/page_plan under each candidate;
  `--shared-evidence` keeps the cheaper page_plan-only probe.
  `inkstone experiment compose` adopts the picked tree and can merge named
  components from another candidate; the result is validated in staging before
  it is moved into place, and `compose.json` / `ADOPTION.md` record the choice.
  Regression: `tests/test_experiment.py`. Next: sequential recursion /
  project-level harness override; the Web card still only selects, not composes.
- **Local (2026-10-20, `main`):** Web A/B card. The local UI has an A/B tab that
  lists `INKSTONE_EXPERIMENTS_DIR`, shows anonymised plans and structure reports,
  and records a pick + required `why` through the same `selection.json` writer
  as the CLI. APIs: `GET /api/experiments`, `GET /api/experiment/<id>`,
  `POST /api/experiment/<id>/pick`. Regression: `tests/test_web_server.py`.
  Next: auto compose / candidate-level extract+beats.
- **Local (2026-10-20, `main`):** Planning-only blind A/B + external harness
  loading. `Harness` can load any tree with the builtin layout;
  `inkstone harness validate|list --dir` and `inkstone prompt --harness-dir`
  check one. `inkstone experiment plan` shares extract/beats, probes
  `page_plan` under each candidate with zero image calls, anonymises A/B/C, and
  writes `experiment.json` + per-candidate evidence; `inkstone experiment pick`
  records the human choice and `why` in `selection.json`. The MVP does not
  auto-compose and does not probe extract/beats per candidate. Regression:
  `tests/test_experiment.py`. Next: web A/B card and compose.
- **Local (2026-10-20, `main`):** Lesson Library MVP. Human-approved lessons
  live under `<INKSTONE_DATA_DIR>/library/<genre>/`; `inkstone lesson
  add|list|retire` writes/retires them, and page planning retrieves up to three
  by genre + cue overlap and injects `{{craft_notes}}`. Writes require evidence
  (run + page/decision), `--no-entry` is valid, and `INKSTONE_LESSONS=0` disables
  injection. The review packet now records `plans/` and `pages.json`. Regression:
  `tests/test_lessons.py`, `tests/test_review_packet.py`. Next Phase 2 step is
  external harness loading + blind A/B. Scoping doc:
  the prompt-harness design (`docs/architecture/2026-10-19-prompt-harness-design.md`, local-only).
- **Local (2026-10-20, `main`):** Shared-state hardening for Phase 2. A reusable
  data-directory lock serializes `budget.json` / `gate.json` read-modify-write,
  and open sessions refresh the shared spent/sample state before writing; a
  corrupt shared JSON is quarantined and rebuilt instead of failing every
  project; `GateSession.load` finds both the shared and project-local gate; and
  cache-hit beats/page-plan stamps persist immediately. Regression:
  `tests/test_shared_state.py`. This is the prerequisite for running multiple
  candidate harnesses without corrupting experiment accounting.
- **Local (2026-10-20, `main`):** Prompt-harness phase 0 + first phase-1 slice.
  Chat action keys carry a request digest (schema 3) over the system prompt,
  stage instructions, tool schema, model, and declared inputs; accumulated
  context (character table, bible prose, recent layout strings) stays pinned out
  so one edit cannot cascade through the book. The invariant system prompt and
  the first genre packs moved to `core/harness/data/` (`general`, `wuxia`).
  `inkstone prompt` prints an effective request without a provider call;
  `inkstone harness validate|list` checks the tree; a local `review/` packet
  records prompts, source, structure report, and decisions. All six chat stage
  user-message templates now live under `core/harness/data/stages/` and are
  rendered through `render_stage()`; the builders only compute slot values.
  Render/letter/export keys remain schema 2. Remaining phase-1 work is the
  static render-lock lines, then the planned A/B / lesson-library loop. Scoping doc:
  the prompt-harness design (`docs/architecture/2026-10-19-prompt-harness-design.md`, local-only).
  P0 ledger fixes D-01–D-04 landed with `tests/test_p0_state_integrity.py`.

- [x] Deferred lettering for finished pages: model paints empty chrome; Inkstone overlays caption/dialogue/sfx with CJK-capable fonts. Unlettered art under `pages/blank/` enables resume re-lettering without re-calling the image API. Spec: [`docs/superpowers/specs/2026-07-31-deferred-lettering-design.md`](superpowers/specs/2026-07-31-deferred-lettering-design.md) (local on `feat/deferred-lettering` until merged).
- [x] Make density a real contract: persist it in `ProjectState`, include it in
  the structure fingerprint (`render_fingerprint` covers style/model/L3), pass a
  budget to planning, and invalidate affected caches. Tier B at 8 panels per
  chunk keeps the fingerprint a project already stored, and the panels a planner
  returns are kept. A or C replans, drops the derived caches, and keeps only
  that many panels.
  - **Local (v0.1.2):** structure/render fingerprint split landed — style, model,
    and L3 changes soft-invalidate panels/portraits only; `chunk_cache` is reused.
    Legacy states still compare the combined hash until migrated.
  - **Local (v0.1.3):** defect-review patch — sparse chunk panel ordering, preserve
    content-policy `skipped` across soft-invalidate, Latin word-wrap, unique
    `unnamed_*` identities, fill empty appearance on merge (see
    `docs/superpowers/plans/2026-07-27-v0.1.3-defect-review-fixes.md`).
  - **Local (post-0.1.3):** page layout letterbox (no aspect squash), README
    provider honesty, web Origin/body caps, dotenv no longer skips on preset
    `AGNES_API_KEY`, `inkstone plan` marked `[experimental]` (see
    `docs/superpowers/plans/2026-07-27-deep-dive-layout-web-fixes.md`).
- [x] Reconcile README, configuration defaults, CLI help and historical docs so
  they do not contradict released behavior. The CLI describes the shipped
  commands. `identity` is the ledger. `plan` no longer prints a required-info
  count that was only the page count.
- [x] Isolate, rename or remove the old PageScript / coverage prototype so it
  cannot be mistaken for a release-quality gate. `coverage` prints a prototype
  banner before any ratio. It stays off unless `INKSTONE_PAGE_SCRIPT=1`.
- [ ] Calibrate estimates with a public sample. The rate question is settled in
  policy, not in numbers: pages are measured when a project has them, derived
  only when it does not, and a derived page count says so in the output. The
  constants stay 3/8/14. A local generate of the Journey to the West excerpt
  planned 14 panels and 8 pages for its one chunk (1.75 panels per page against
  the planner's own 4-per-page packing limit), while the tier rate for that text
  is 8 panels and 2 pages. So `panels / PANELS_PER_PAGE` is an upper bound on
  density, never the planner's rate, and feeding measured panels in without
  measured pages now derives pages from those panels instead of leaving the
  stale tier value. `inkstone plan --out` reads panel and page counts a project
  has already planned; without `--out` the output stays the experience rate and
  says so. A project that stopped between chunks keeps the pages it measured.
  Those pages are projected through the measured rate, not spread evenly: a page
  plan also reports the panels its pages carried, so a pending chunk with a
  storyboard is projected from its own panel count. The engine packs at most
  `PANELS_PER_PAGE` panels per page, so no projection is allowed to exceed that,
  and the output says when the limit raised the number. The two lists are
  aligned by chunk, so a gap in `page_cache` (a chunk whose page plan a content
  filter rejected) cannot slide a later chunk's pages onto an earlier chunk's
  panels, and a page count of zero means "not planned yet" rather than "no
  pages". A chunk with no page plan and no storyboard takes the measured average,
  which is the only thing left to spread.
  What remains un-calibrated is the budget itself: tier B prompts for
  "about 8 panels" and only A and C clip the plan, so a B planner can and did
  produce 14. The overrun is no longer silent — both the storyboard and the page
  plan log the panels kept against the budget when nothing is clipped.
  - Only tier A clips. `clips_plan` in `core/density.py` is the single answer to
    "does this tier refuse an overrun"; the nine pipeline call sites, the two
    prompt-renderer sites, and the estimator's floor warning all read it, so the
    code that enforces and the sentence that describes it cannot disagree. A
    promises 主线概览, so thinning to its budget is the feature it is named for.
    B promises 章级完整 and C promises 近原著, so dropping panels there breaks the
    promise the tier name makes.
  - C used to clip as well, capped at 14 while B was uncapped. A planner that
    wrote more than 14 panels then made the densest tier the thinnest one: C
    came out shorter than B, inverting the A<B<C order the brief promises. With
    only A clipping, B and C both keep what the planner wrote, so they can meet
    at the top but can never cross, and the order holds for any planner output.
    Verified by mutation: restoring `tier != "B"` inverts the order at 20, 24,
    and 40 panels. Pinned by `test_only_tier_a_clips_the_plan` and
    `test_tier_c_never_comes_out_thinner_than_tier_b`.
  - Clipping no longer keeps only the opening. `_clip_page_plan` and
    `plan_storyboard` used to take the first N panels, so tier A kept the setup
    and dropped the turn and the close — the reader got the beginning of a story
    with no ending. Both now spread the kept panels across the chapter and always
    keep the first and the last; the count still equals the budget. Only A takes
    that cut, which is what makes the order above hold. Pinned by
    `test_a_clipped_plan_keeps_the_chapter_ending` and
    `test_a_clipped_storyboard_keeps_the_chapter_ending`.
  - What is still open is whether B and C are *distinct in practice*. Nothing
    caps either, so if the planner writes the same 14 panels for both, the two
    tiers produce the same book and the only difference is the sentence they were
    asked with. Calibrating that needs a real C sample; the tier numbers stay as
    they are until one exists. Note what this policy does not do: it guarantees
    C is never *shorter* than B, not that C is ever *longer*.
- [x] Phase 3 keys. Extract, beats, page plan, and finished-page render use
  action keys. A matching key does not call the provider again. A page plan key
  carries the history window and the summarizer source hashes, not the recent
  layout strings. A portrait key is that character's own prompt. The earlier
  portrait file stays out, so a matching key does not paint it again. A storyboard
  panel key is that panel's own plan. The previous panel stays out. A bible key
  is the chunk plus the summary-function hashes, not the bible prose, so a
  recorded merge does not reconcile every chunk again. A storyboard key is the
  chunk and the names in that chunk. A page script key is that chunk's text,
  storyboard, and extracted elements. A beats key includes that chunk's extract
  key, so a changed extract replans the beats. A project that already stored a
  beats key replans those beats and its page plan once. A page plan key includes
  that beats key, so a project that already stored a page plan key replans the
  pages once. A stale page still repaints. The alias-merge ceiling stays 15 image
  calls and 0 chat calls at 300 pages, and the same 15 and 0 at 600 pages.
- [x] Phase 3 fingerprint tokens. The render fingerprint no longer carries the
  manual identity, stage-lock, layout, voice-timeline, beats, or lettering
  tokens. The next resume of an existing project repaints once, because the
  stored fingerprint no longer matches. Lettering keeps its own action key.
  The structure fingerprint is the normalized source, plus density when the
  tier is not B at 8 panels. The next resume of a project stored under the old
  pipeline version rebuilds its extract caches once and keeps the human alias
  decisions. `_PIPELINE_STATE_VERSION` remains only in the legacy combined
  fingerprint.
- [x] Phase 3 scheduler. A run plans every chunk before it draws a page or a
  panel. The page count comes from that plan pass. An ephemeral two-phase work
  graph drives the planning pass; render and export use the same graph as a
  validation layer (assertions, the export gate, and the final outstanding
  check) while keeping their existing deterministic dispatch order. Render
  nodes are expanded from the plans (one chained node per finished page;
  positional panel nodes with continuity, or one bounded-concurrency batch per
  chunk without it); export finalization is gated on every render node
  completing.

- **Local (2026-10-21, `main`):** One packing limit instead of two copies.
  `DEFAULT_PANELS_PER_PAGE` in `core/config.py` now answers both questions the
  number is asked: `LayoutEngine` paginates by it and `core.density` divides by
  it to quote pages, cost, and duration before any billable call. The engine
  previously hard-coded `per_page=4` in two places (`_compose_pages` and the
  `_paginate` default), so changing the constant would have left every quoted
  page, cost, and duration figure quietly wrong with no failing test.
  `PANELS_PER_PAGE` in `core.density` is now an alias for the shared value, so
  existing importers keep working. `tests/test_page_capacity_contract.py` ties
  the quoted page count to the pages the engine can actually produce, and a
  mutation check confirms the test fails when the two copies disagree. The
  layout stage hash changes once, which re-composes local page images on the
  next run; that stage calls no provider.
- **Local (2026-10-21, `main`):** An omitted tier now means "keep the stored
  one". `generate --density` defaulted to `"B"` in both parsers, so a resumed
  tier C project that did not repeat the flag was read as a deliberate switch
  to B: the structure fingerprint stopped matching, the state was rebuilt, and
  every paid page was redrawn (measured: one extra chat and one extra image
  call on a one-chapter project, and the pages already on disk). The flag now
  defaults to `None` and `creative_comic` inherits `state.density` when nothing
  is passed; a fresh project still starts at B, and an explicit flag still
  replans. `inkstone prompt` had the matching gap: it promised "the exact
  request one stage would send" while hard-coding tier B, printing about 8
  panels and "a target, not a cap" even for a tier C project. It now reads the
  stored tier and applies that tier's clipping semantics. Both paths are pinned
  by mutation-checked tests in `tests/test_density_contract.py` and
  `tests/test_cli_prompt.py`.

### P0 — Content-addressed pipeline migration (0a-0h)

Per the the content-addressed pipeline design (`docs/architecture/2026-09-28-content-addressed-pipeline-design.md`, local-only) §9.

- **Local (2026-10-20, `main`):** Phase 0c declared read edges plus the
  `migrate --check` forecast. `core/pipelines/stage_registry.py` now names the
  producer of each stage's hard inputs; the pipeline validates that table before
  the first billable call and rejects an input with no producer, a producer that
  is not a stage, and an edge pointing at a later phase. `read_edges()` and
  `upstream_of()` expose the graph the §9 per-stage runner contract needs.
  `inkstone migrate --check` compares each manifest's recorded `stage_src`
  against the hash current code computes, reporting billable chat calls,
  local-only redos, and reusable pages separately; it re-derives no keys.
  Contract: the content-addressed design (`docs/architecture/2026-09-28-content-addressed-pipeline-design.md`, local-only) §9.

- **Local (2026-10-19, `main`):** Continuity-review escalation (the 5b
  follow-up 0d deferred). `LEDGER_ESCALATION_THRESHOLD = 30` (same order as the
  sample gate, held by a test) holds the render phase at the plan→render
  boundary — one deduplicated `stop` record per unchanged boundary, no render
  call issued — until a human confirms; pending sets at or below the threshold
  stay quiet and only appear in the ledger and the panel. Confirmation lands
  through the ledger API, `POST /api/project/<id>/ledger/confirm`, and
  `inkstone identity --confirm`; the web UI renders the aggregated "Continuity
  review" panel. The ledger and the threshold enter no key or fingerprint, and
  `consistency.json` keeps its schema. Scoping doc:
  [escalation design](superpowers/specs/2026-10-19-ledger-escalation-design.md).

- **Local (2026-10-19, `main`):** Two-part `state.json`. The heavy format
  caches (`chunk_cache`, `page_cache`, `beat_cache`, `visual_bible`) moved to
  the sibling `cache.json`; the render loop's per-page and per-panel saves
  write the checkpoint only, and progress readers read it without the cache
  file. Scoping doc:
  [state-split design](superpowers/specs/2026-10-19-state-split-design.md).
  Remaining migration work: manifest-backing the chat-stage cache bodies and
  per-stage runners.

- **Local (2026-10-19, `main`):** Migration close-out. The legacy combined
  fingerprint and `_PIPELINE_STATE_VERSION` are deleted; a `state.json`
  without split fingerprints now takes the announced one-time recompute and
  keeps its recorded human decisions. The one-time invalidation announcement
  and the idempotency-probe verdict (§13 item 27: two identical requests
  returned different bytes, so §8's duplicate-charge window stays open) are
  recorded in [CHANGELOG.md](../CHANGELOG.md). The plan caches moved to the
  sibling `cache.json` in the state split above, not left in `state.json`;
  per-stage runners were later work on this date, and their contract is stated in
  the the content-addressed design (`docs/architecture/2026-09-28-content-addressed-pipeline-design.md`, local-only) §9
  (superseded 2026-10-20: the runner landed for `layout` and `webtoon`).

- **Local (2026-10-19, `main`):** Two-phase ephemeral scheduler foundation.
  `core/pipelines/stage_scheduler.py` validates the in-memory plan/render/export
  graph and selects ready nodes in deterministic order. `creative_comic` drives
  the plan pass through that graph; finished-page rendering, both panel-compose
  modes, and export finalization are validated against it but keep their
  deterministic sequential dispatch. Plan nodes remain composite (one per
  chunk); plan-cache relocation, per-stage runners, legacy-fingerprint removal,
  and action-key changes remain incomplete migration work.
- **Local (2026-10-18, `main`):** Finished-page
  artifact projection now restores page and blank bytes from verified
  `render.page` / `letter` manifests; PDF, CBZ, Webtoon, and layout export
  artifacts restore from verified manifests. Plan caches still live in
  `state.json`, and the legacy combined fingerprint still exists.

- [x] **0b** Positional page identity: a page's identity is `c{ci:04d}-p{idx:04d}`,
  converging on the panel-key convention. Model-generated `page_id` no longer
  participates in identity; a deterministic, zero-quota rewrite on load migrates
  legacy `c{ci:04d}:{page_id}` keys so a resumed project is not repainted.
- [x] **0d** Consistency ledger (`consistency.json`, §12): an explicit,
  authoritative record of which pages each character appears on. It is
  CAS-independent and zero-quota — page sets are derived from `page_cache`, and
  the ledger doubles as the decided L2 reference source for Phase 1 (resolved
  item 17). Landed: schema + atomic IO, derivation, corruption recovery,
  reference versions/pending review, alias-merge queries, pipeline maintenance,
  a `ledger_pending` web snapshot field, and `inkstone identity --view`. The
ledger enters no `action_key` or fingerprint (guarded by a test).
- [x] **0g** Minimal reclaimer: `inkstone prune --older-than <N>d [--apply]` deletes
  generated image assets whose reference count is zero **and** whose age exceeds
  the threshold, dry-run by default (§7, resolved items 3 and 22). CAS-independent
  and zero-quota: today artifacts are written at deterministic, overwritten paths,
  so the root set is `state.json` + `consistency.json`; `collect_live_refs` is the
  seam Phase 4's `gc` extends. It ships before Phase 3, the entry point that writes
  image bytes.
- [x] **5a** Quota budget. `inkstone generate --budget N` caps billable calls for
  that run. Each stage has its own reservation (a render overrun cannot spend the
  extract reservation) and one call is withheld as a recovery reserve when the
  budget is at least 2. Exhaustion pauses at the item boundary, writes one
  `runs.jsonl` stop, and a resume at the same boundary does not append another or
  issue the call. `--no-carry-over` is the default: a later calendar day does not
  re-arm the allowance; `--carry-over` does. The budget enters no fingerprint.
  Shared accounts live in `quota.jsonl` and `budget.json` under the data directory
  (`INKSTONE_DATA_DIR`, default `~/.inkstone`). Omitting `--budget` keeps today's
  uncapped run, so existing projects are not paused by surprise.
- [x] **5b** Sample gate. A render batch larger than 30 pages, spanning two chunks
  when the book has two, pauses for a human decision before the remainder is
  drawn. `inkstone gate --decision accept|redraw|accept-and-flag` records the
  per-page card; accept and accept-and-flag release the rest once every sample
  page is decided that way, and the acceptance rate then moves the next sample
  (`round(n * 1.618)`, lock, or halve). `--yes` is the audited escape hatch.
  A resume at the same boundary does not append another stop or issue the
  call. The gate enters no fingerprint. Shared state is `gate.json` under the
  data directory when the CLI or web pass it, otherwise the project directory.
  The deferred escalation half of §4's continuity flag landed 2026-10-19:
  one character's pending set above `LEDGER_ESCALATION_THRESHOLD` (30) holds
  the render boundary until confirmed; see the local note above.
- [x] **0h growth** Doubling the alias-merge sample from 300 pages to 600, with the
  alias still on the same 15 pages, leaves the re-run at 15 stale keys, 15 image
  calls, and 0 chat calls. The current architecture already does not grow with book
  length. Extract and finished-page render use action keys, and this re-run stays
  at 15 image calls and 0 chat calls. A run plans every chunk before it draws.
  Phase 2's object store is the part that has started.
- [x] **1 (contracts)** Stage input declarations. `page_plan` may read earlier
  layouts only through a window of 8; the declared input is that window plus the
  summarizer source hashes, not the intent strings and not `page_cache`. Chat
  stages do not take `{page_size, panel_continuity, l3_enabled}`, and those knobs
  do not change the structure fingerprint. The L2 continuity image stays a soft
  reference of `render.page`. This does not add CAS or action keys.
- [x] **1 (page bible)** A finished-page prompt carries bible entries only for the
  characters on that page. An off-page canon cannot appear in the prompt, so a
  later key for the page cannot depend on the rest of the book. Style, era, and
  color stay global.
- [x] **0a (resolved size)** A finished page records the size the provider
  accepted. When the portrait-shaped request is rejected, `resolved_size` is the
  square fallback, not the configured string. Old `state.json` files load with
  the field empty.
- [x] **0a (letter size)** A letter key uses that accepted size. A square
  fallback re-letters the stored blank and does not call the image provider.
  The same size copies the stored page. An empty `resolved_size` keeps the key
  that was stored for the configured size.
- [x] **0a (bound size)** A PDF, a strip, and a collage record the pixel size of
  the images they bind. Changing the configured page size does not rebuild them
  when those images stay. Mixed pages record each size in bind order.
- [x] **2 (manifest time)** A new letter, export, or layout manifest records when
  it was written. Rewriting that manifest in place keeps the original time.
- [x] **call audit** An image or chat call appends an intent before the provider
  is touched, and a failure appends the exception class. The prompt and the error
  text stay out of `runs.jsonl`. A resume that does not call does not append
  another line. A chat call outside a run writes nothing.
- [x] **1 (render mode)** Switching a finished-page project to `panel_compose`
  plans the missing storyboard. A finished-page completion is not treated as a
  storyboard hit.
- [x] **1 (remaining stages)** `portrait`, `page_script`, `render.panel`,
  `letter`, and `export` declare their inputs. None of them reads an accumulated
  window. The previous panel and the canonical portrait stay soft references.
  Lettering does not take the font path as an input.
- [x] **1 (human state)** Replacing the projection because the source changed
  keeps `needs_review`. Page tombstones belong to the discarded projection and
  are dropped with it.
- [x] **1 (dismissed alias)** Dismissing an alias records the pair. A later
  extract, including one after the source changes, does not put that pair back
  on `needs_review`. Old `state.json` files load with the record empty.
- [x] **1 (merged alias)** Merging an alias records the pair. A later extract
  that sees both names folds them again before portraits and planning, and does
  not reopen the review queue. The cached extract text stays unchanged.
- [x] **1 (alias pair)** A dismiss or a merge matches the two names in either
  order. A later extract that introduces the canonical name first does not
  reopen the queue, and a merge still folds toward the recorded canonical.
- [x] **1 (bible summary)** `bible` reads one current summary, not earlier
  chunks. Its declared input is that summary plus the summarizer source hashes.
  The summary body stays out of the declaration.
- [x] **2 (store)** A project can hold `cas/` objects and `index/` manifests.
  An object is written once under its content hash. Rewriting a manifest keeps
  the key and records `supersedes`. `inkstone verify --out ...` checks that
  every output exists and matches its hash, and that a non-ok outcome has a
  reason. It does not reconcile `state.json`.
- [x] **2 (letter)** Finished-page lettering writes a manifest under the project.
  The same blank, plan, and environment copies the stored bytes instead of
  drawing again. A different blank letters again. Render keys stay off the
  generate path.
- [x] **2 (export)** Binding the page images into `comic.pdf` writes a manifest.
  The same page bytes and the same layout copy the stored PDF. A changed page
  binds again.
- [x] **2 (verify in CI)** A CI job builds a temporary project from a few KB of
  synthetic bytes, runs `inkstone verify`, and fails when one object is
  deleted. No example book is committed.
- [x] **2 (tombstone)** A rejected letter or export manifest is a hit. The next
  run does not letter or bind that key again. `inkstone rebuild --stage
  letter|export --key ...` drops the manifest, and the following run does the
  work. An ok manifest stays.
- [x] **2 (webtoon)** Stacking pages or panels into `webtoon.png` writes a
  manifest. The same images and the same drawn text copy the stored strip. A
  changed panel or a changed line stacks again.
- [x] **2 (layout)** Collaging panels into page sheets writes one manifest for
  every sheet. The same panels and the same drawn text copy those sheets. A
  changed line collages again. A rejected collage is not bound into a PDF until
  `rebuild --stage layout` releases it.
- [x] **2 (font)** A changed font re-letters a finished page from its stored
  blank. The image provider is not called, and the blank bytes stay as they
  were. The same font copies the stored page.
- [x] **2 (stage source)** Letter, layout, webtoon, and export keys hash the
  functions that draw or bind their bytes. A page-grid change is not part of
  lettering. The pipeline function stays out of those hashes.
- [x] **2 (shorter collage)** A collage that writes fewer sheets deletes the
  extra `page_NN.png` files. A finished-page file in the same directory stays.
  The PDF binder therefore sees only the sheets this collage produced.
- [x] **2 (source closure)** A direct callee of a local stage is in that stage's
  hash. Wrapping CJK and Latin on different lines is part of the collage and
  the strip. The font resolver, the PDF batch size, and the webtoon pixel cap
  stay out.
- [x] **4 (gc)** `inkstone gc` lists `cas/` objects nothing names, once they are
  older than the threshold. A manifest input or output, a rejected manifest's
  previous bytes, a ledger content hash, and a hash named in `gate.json` stay.
  `--apply` is what deletes. An unreadable manifest refuses the plan.
- [x] **4 (pending pages)** A sample page that still awaits a decision keeps the
  `cas/` object whose bytes are that page or its blank. An accepted page, a
  released sample, and a `--yes` sample do not. An unreadable `state.json`
  refuses the plan.
- [x] **4 (ledger bytes)** Recording a portrait copies its bytes into `cas/` and
  stores that content hash on the ledger. `gc` leaves the object in place, so
  the authoritative reference still resolves after the age threshold.
- [x] **4 (older portraits)** A ledger whose reference has a path and no content
  hash copies that file into `cas/` on the next run. The version and the pending
  pages stay as they were. A missing file, or a path outside the project, stays
  unset.
- [x] **1 (layout contract)** `layout` declares the panels, the drawn text, and
  the page geometry. `export` declares the bound images and the binding. Neither
  reads an accumulated window or a render-only knob.

### P1 — Chapter-complete adaptation MVP

- [x] Introduce normalized, globally addressable `SourceUnit` records. A unit is
  `u{index:04d}` plus a character span of the normalized source. The spans
  concatenate back to that source. They are stored on the project and do not
  enter a fingerprint. A heading that uses 回 is still one unit.
- [x] Generate per-chapter `AdaptationPlan` and `NarrativeBeat` records with
  required / optional status and causal dependencies. A plan is stored per
  source unit. A key beat the extractor marked must-draw is required; the rest
  are optional. Each beat after the first depends on the previous beat in that
  unit. A chunk that sits in two units is left unassigned. The copy does not
  call a model and does not enter a fingerprint.
- [x] Generate beat-constrained storyboard panels with explicit `beat_ids`.
  A panel keeps a beat id only when that beat was already extracted. Each
  required beat is written onto a panel. Optional beats stay unnamed unless a
  panel already named them. A chunk with no beats keeps empty `beat_ids`, and
  the storyboard call does not gain another model call.
- [x] Add a reader-visible lettering layer: separate caption, dialogue and SFX.
  - **Local:** MVP lettering fields + layout drawers shipped (caption bar /
    dialogue bubble / sfx outline). A detected face rectangle moves the box
    off that face. No detected face leaves the box where it was.
- [x] Add structural coverage gates for source traceability, beat coverage,
  visible text, causal order and blocked content. `inkstone structure` reads
  the stored project. A check with nothing to read is absent, not a pass.
  Skipped chunks and tombstones fail the blocked check. The report does not
  call a model and is not a readability or quality gate.
- [x] Validate one public-domain chapter end-to-end before attempting a whole book.
  A chapter counts only when every structural check passes and a page is stored.
  The local Journey to the West excerpt passes every check and has 8 stored
  pages. Lettering that is not a contiguous span of the source is dropped. A
  trailing ellipsis stays only when the text before it is in the source. The
  stored blank is lettered again and the image provider is not called. The
  drawings stay in `comic_out/` and are not committed.

### P2 — Delivery experience and proof

- [ ] Improve PDF typography, including CJK caption support. CBZ export is done:
  `--format cbz` zips the page images in filename order and writes
  `ComicInfo.xml` with that page count, and a second bind copies the stored
  archive. What remains is the typography below.
  - **Local:** Pillow writes pages at 150 dpi. img2pdf
    uses that same dpi in its page layout, so a PNG without a dpi tag is not
    left at the converter default of 96. A CJK
    caption that wraps past its bar is drawn smaller so the glyphs stay inside
    the bar. Dialogue and sfx that do not fit their box do the same and stop at
    the box edge. The caption bar, the dialogue outline, and the sfx stroke
    all stop on the last pixel inside that edge. A box narrower than one glyph
    does not paint past that edge. A closing mark stays with the previous glyph,
    and an opening mark stays with the next, when that pair fits the line. A
    later closing mark does not pull that glyph away and leave the opening mark
    alone. An ellipsis stays with the previous glyph when that group fits the
    line. A halfwidth comma hangs on the previous CJK glyph and leaves a Latin
    word intact. A halfwidth opening mark stays with the next glyph when that
    pair fits the line. A dash, a tilde, and a middle dot hang on the previous
    glyph when that group fits the line. On a page grid, sfx starts below the
    caption bar. A percent sign and a degree mark hang on the previous digit
    when that pair fits the line. A fullwidth percent hangs on the previous
    digit the same way. A letter unit such as cm hangs on the previous digit
    when that group fits the line. A decimal keeps its letter unit with the
    last digit the same way. A permille and a prime hang on the previous digit
    when that pair fits the line. A spaced letter unit keeps that space and
    hangs on the previous digit when that group fits the line. A fahrenheit
    sign, an ohm sign, and a micro sign hang on the previous digit the same
    way. A single-character unit such as ㎡ or ㎏ hangs on the previous digit
    when that pair fits the line. A currency mark hangs on the previous digit
    when that pair fits the line. A space before that mark stays when the
    spaced group fits the line. 块, 角, and 分 hang on the previous digit the
    same way. A thousands group stays together, and its comma hangs on the
    previous digit when that group fits the line. A decimal after that group
    hangs on the last digit the same way. A letter unit after that decimal
    stays with the fraction when the pair fits the line. A classifier such as
    个, 只, 名, 位, or 岁 hangs on the previous digit the same way. A clock keeps
    its minutes with the colon when that group fits the line. Seconds stay in
    their own pair, and that colon hangs on the previous pair when it fits. Letters
    beside a clock stay outside those pairs. A number range keeps both sides
    intact, and the dash hangs on the first number when that group fits. A date
    keeps its year, month, and day intact, and each separator hangs on the
    previous field when that group fits. A spaced letter after a clock hangs on
    the whole minute pair when that group fits. A numeric expression keeps both
    operands intact, and its operator hangs on the first operand when that group
    fits. A letter unit after that expression stays with the right operand. A
    symbol unit such as ㎡, ℃, or ㎝ stays with that operand the same way. A
    decimal in either operand stays with its digits. A spaced letter unit stays
    with the whole operand before it when that group fits. A Chinese date
    keeps its year, month, and day intact, and 年, 月, and 日 hang on that
    field when the group fits. A Chinese time keeps its hour, minute, and
    second intact, and 时, 分, and 秒 hang on that field when the group fits.
    点 hangs on the hour the same way, and 号 hangs on the day. A sum or a
    difference keeps both operands intact, and its plus or minus hangs on the
    first operand when that group fits. A hyphen range stays a range. A
    duration keeps 小时, 分钟, and 秒钟 intact, and with the number when that
    group fits. 公斤, 公里, 厘米, 毫米, 千米, and 千克 stay intact the same
    way. 毫升 and 公升 stay intact the same way. 公分 stays intact the
    same way. A caption that mixes CJK and Latin keeps each Latin word intact.
    The PDF is still that raster.
- [ ] Publish a public-domain *Journey to the West* chapter showcase with source,
  plan, coverage report and PDF.
  - **Local (scaffold):** `examples/showcase/journey-west-ch1/` +
    `scripts/run_showcase.sh` (source + plan recipe; generated artifacts not
    committed — option C). When `comic_out/journey-west-ch1/state.json` is
    present, the script plans from those stored counts and prints the
    structural check.
- [x] Add chapter navigation / browser reader and an identity-ledger CLI or view.
  - **Local:** `inkstone read --out` lists stored pages by the chunk id in the
    filename and writes `reader.html`. The page shows its index and the stored
    page count. Arrow keys flip a page. A chunk button
    jumps to that chunk's first page. `inkstone identity --view` already prints
    the ledger. A finished web job links to the same file when pages are stored;
    opening that link writes it. The reader does not call a model.
- [x] Rewrite README and contributor experience around the validated long-form workflow.
  - **Local:** The README names PDF, CBZ, and PNG, and the shipped commands. It
    says a chapter is validated only when every structural check passes and a
    page is stored, and it points at the Journey excerpt. A local run of that
    excerpt passes the five checks and has 8 pages. The page images and PDF stay
    in `comic_out/`.

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

## M2 — Comic-specific pipeline ✅ done

Goal: assemble text-to-image / image-to-image into a real comic production chain.

**New:**
- [x] `core/api/chat_provider.py` — `ChatProvider` + `AgnesChatAPI` + `get_chat_provider()` (forced function calling)
- [x] `core/schemas.py` — `CharacterAsset` / `StoryElements` / `Storyboard` / `ProjectState` (double as function-tool schemas)
- [x] `core/comic/consistency.py` — `ConsistencyEngine`
  - L1: prompt hard-describes character features
  - L2: reference-image img2img (character portrait as `reference_image_paths`)
  - L3: PIL/OpenCV face / feature overlay fallback (cv2 lazy; quality guards skip on failure)
- [x] `core/comic/segmentation.py` — `segment_text` (chapter/token split + overlap) + `merge_characters` (exact-name dedup)
- [x] `core/comic/layout.py` — `LayoutEngine`: N panels on a grid (page) or vertical strip (webtoon) + dialogue bubbles
- [x] `core/comic/export.py` — `ExportEngine`: PDF (`manga2pdf`) + vertical-strip PNG (pure PIL)
- [x] `core/pipelines/creative_comic.py` — orchestration: portraits → panels → layout → export, with `state.json` resumption (regenerates missing panels) and content-safety graceful skip
- [x] `core/screenwriter.py` — screenwriter: forced extract/storyboard + content-safety hygiene (`sanitize_text`, `is_content_policy_rejection`)
- [x] `examples/generate_comic.py` + `examples/scene1.txt` — end-to-end runnable demo (needs `AGNES_API_KEY`)

**Reused (not rewritten):** `get_image_provider`, `AgnesImageAPI`, `RateLimiter`, `error_collector`, `utils.image.download_image`.

**New deps (in requirements):** `pydantic` (schemas), `manga2pdf` (PDF export, optional CLI).

Verified: `pytest` → 64 passed (1 cv2-dependent test skipped), CI green on Python 3.10–3.12.

## M3 — Long-form + consistency hardening ✅ done

- [x] Long text split by chapter / segment → generate per segment → cross-chapter character assets reused
  (`CharacterAsset` persisted in `state.json`; `merge_characters` dedups by exact name so the same portrait is never regenerated).
- [x] Default consistency strategy **L1 + L2** wired and exercised end-to-end (prompt hardening / multi-image reference).
  - **L3 (cv2 Haar face overlay) is opt-in and OFF by default** since it deforms stylized faces when pose/lighting differ; enable with `INKSTONE_L3=1`. Verified by regenerating `comic_out` end-to-end with L3 off.
- [x] Cross-chapter alias detection: `detect_character_aliases` flags near-duplicate names (e.g. `方鸿渐` vs `鸿渐`) into
  `state.needs_review` for human decision — **no auto-merge**, so a variant name is never silently forked into a second character.
- [x] Resumption: checkpoint in `state.json`; per-chunk `extract`/`storyboard` cached in `chunk_cache` so a resume reuses them
  and never re-pays the (billable) chat API; `panels_done` dedup key avoids duplicate panel generation / billing.
- [ ] **Deferred (GPU / optional):** `InsightFace` embedding for precise "which face belongs to which character" matching
  (avoids L3 mis-overlay) and **L4** multi-round iterative refinement. The whitepaper scopes these as local-GPU-only and out
  of the default zero-cost path; revisit only if a GPU branch is added.

Historical verification at M3 completion: `pytest` → 67 passed (1 cv2-dependent test skipped), CI green on Python 3.10–3.12. Current verification commands and status are defined above.

## M4 — Open-source release ✅ done

Goal: make Inkstone genuinely runnable and reviewable by an outside contributor.

- [x] `comic_out/` added to `.gitignore` (generated artifacts no longer committable by accident).
- [x] Initial design, review and hosting notes created; historical copies may
  live under local `docs/archive/` until deliberately republished.
- [x] One-click launch script (`scripts/start.sh` / `scripts/start.ps1`) — sets up env, installs deps, runs the demo.
- [x] Sample-novel demo — runnable `txt` inputs ship in `examples/` (`scene1.txt` + `sample_novel.txt`).
- [x] README gallery — committed 3 sample panels + a downscaled webtoon under `assets/samples/` and referenced them.
- [x] Optional lightweight Web UI: single-file Tailwind SPA (zero build) + zero-dependency stdlib `http.server` backend that runs the pipeline and streams panels.
- [x] Same SPA deployed to GitHub Pages in demo mode (auto-detects backend via `/api/health`) so the public site shows the identical UI and a real generated sample (the "effect").
