# Contributing to Inkstone

Thank you for your interest in contributing! This document explains how to set
up the project, the conventions we follow, and how to get a pull request merged.

## Code of Conduct

By participating, you agree to abide by our
[Code of Conduct](CODE_OF_CONDUCT.md).

## Getting started

```bash
# 1. Fork and clone
git clone https://github.com/<your-username>/inkstone.git
cd inkstone

# 2. Create & activate the conda environment (Python 3.10+)
conda create -n inkstone python=3.10 -y
conda activate inkstone
pip install -e ".[dev]"        # runtime + dev/test tools (pytest, ruff)

# 3. Configure (ordinary users only need the API key)
cp .env.example .env
#    edit .env and set AGNES_API_KEY=sk-xxx

# 4. Run the test suite (no network required)
pytest
```

## Project layout

| Path | Responsibility |
| --- | --- |
| `core/api/` | Image Provider abstraction, Agnes wrapper, rate limiter, error collector |
| `utils/` | Cross-cutting helpers (e.g. image download) |
| `tests/` | Unit tests (network-free where possible) |
| `docs/ROADMAP.md` | Current shipped / prototype / planned work and milestone history |
| `docs/ONBOARDING.md` | Developer map of the shipped codebase (layers, tour, hotspots) |
| `docs/guides/colab-cli.md` | Colab remote runner operations |
| `docs/superpowers/` | Historical completed plans / specs |

> **Note on design documents:** the roadmap, onboarding guide, Colab guide, and
> `docs/superpowers/` are versioned. Other local drafts under `docs/`
> (architecture, product brief, archive) stay untracked until deliberately
> published. Put temporary discussion and unapproved research in `.issue/`, not
> in the versioned documentation set.

## Development workflow

1. **Branch from `main`** using a descriptive name:
   `feat/<short-description>`, `fix/<short-description>`,
   `docs/<short-description>`, `chore/<short-description>`.
2. **Make your change**, keeping commits focused and atomic.
3. **Verify locally** before opening a PR:
   ```bash
   ruff check .
   ruff format --check .
   pytest
   ```
   (Install the pre-commit hooks with `pre-commit install` to run these
   automatically on every commit.)
4. **Open a pull request** against `main` using the provided template.

## Commit messages

We follow [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>[optional scope]: <description>

[optional body]

[optional footer(s)]
```

Types: `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`,
`build`, `ci`, `chore`. Use `!` after the type for breaking changes, or add a
`BREAKING CHANGE:` footer.

Examples:

```
feat(comic): add grid layout engine for comic pages
fix(api): retry on 503 instead of failing immediately
docs: clarify Free Access tier limits in README
```

The maintainer may squash a PR and write the final message; contributors do not
need to be perfect, but readable history is appreciated.

## Coding standards

- **Language:** all code, comments, docs, and commit messages are in English.
- **Style:** formatted with `ruff format`; linted with `ruff check`.
- **Typing:** prefer type hints on public functions.
- **Async:** Agnes calls are async (`asyncio.to_thread` for blocking I/O).
- **No silent failures:** API errors are collected, never swallowed.

## Reporting bugs & requesting features

Please use the issue templates. For security issues, follow
[SECURITY.md](SECURITY.md) — do **not** open a public issue.
