# 《西游记》第一回节选（公版）Showcase

**Scope:** source text + scripts only. Generated panels / PDF / coverage
reports are **not** committed — run them locally or on Colab, then keep
artifacts under `comic_out/` (gitignored).

## Source

`source.txt` is a short public-domain excerpt from *Journey to the West*
(西游记), chapter 1 opening. Suitable for a smoke-scale generate, not a
full-book run.

## Offline plan (no API key)

From the repo root (with the inkstone env active):

```bash
./scripts/run_showcase.sh
# or:
inkstone plan --book examples/showcase/journey-west-ch1/source.txt --density B --format page
```

`plan` is marked `[experimental]` and does **not** constrain `generate`.

## Generate (needs AGNES_API_KEY)

```bash
python examples/generate_comic.py \
  examples/showcase/journey-west-ch1/source.txt \
  --project journey-west-ch1 \
  --format page \
  --out comic_out/journey-west-ch1
```

Or via Colab CLI:

```bash
./scripts/colab_run.sh run examples/showcase/journey-west-ch1/source.txt \
  --project journey-west-ch1 --format page
./scripts/colab_run.sh download --project journey-west-ch1
```

## Honesty note

Lettering (caption / dialogue / sfx) is drawn by layout when the storyboard
model fills those fields. Consistency remains L1+L2 on the free tier — expect
drift across panels. Treat this showcase as a reproducible recipe, not a
polished published comic until you review the PDF yourself.
