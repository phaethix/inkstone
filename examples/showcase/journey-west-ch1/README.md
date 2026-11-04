# 《西游记》第一回节选（公版）Showcase

**Scope:** source text only. Generated panels / PDF / coverage reports are
**not** committed — run them locally, then keep artifacts under `comic_out/`
(gitignored). All commands use the unified `uv run inkstone` entry.

## Source

`source.txt` is a short public-domain excerpt from *Journey to the West*
(西游记), chapter 1 opening. Suitable for a smoke-scale generate, not a
full-book run.

## Offline plan (no API key)

From the repo root:

```bash
uv run inkstone plan --book examples/showcase/journey-west-ch1/source.txt --density B --format page
```

When `comic_out/journey-west-ch1/state.json` is present, add
`--out comic_out/journey-west-ch1` so the estimate uses those stored counts.
Without that file it prints the experience-rate estimate.

`plan` is marked `[experimental]`. Its panel rate is an experience value until
you pass `--out` pointing at a project that already has plans. `generate
--density` uses the same tier and replans when the tier changes. This excerpt
is one source unit. A local generate under `comic_out/journey-west-ch1` passes
`inkstone structure`: every check is pass and 8 pages are stored. Lettering is
kept only when it is a contiguous span of `source.txt`. The PDF and page images
stay in `comic_out/` and are not committed.

## Generate (needs AGNES_API_KEY)

```bash
uv run inkstone generate \
  --book examples/showcase/journey-west-ch1/source.txt \
  --project journey-west-ch1 \
  --format page \
  --out comic_out/journey-west-ch1
```

`--format cbz` zips the stored pages and writes a page count. After pages
exist, `uv run inkstone structure --out comic_out/journey-west-ch1` and
`uv run inkstone read --out comic_out/journey-west-ch1` read that project and
do not call a model. The reader shows the page index. A local project at that
path passes every structural check and has 8 stored pages. The PDF is not
committed.

## Honesty note

Lettering (caption / dialogue / sfx) is drawn by layout when the storyboard
model fills those fields. Consistency remains L1+L2 on the free tier — expect
drift across panels. Treat this showcase as a reproducible recipe, not a
polished published comic until you review the PDF yourself.
