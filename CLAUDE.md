# Repovore — Developer Guide

## What is this?

GitHub repository quality assessment pipeline. Takes GitHub repo URLs, fetches metadata via API, computes quality/health signals, and produces structured "cards" for each repo.

## Quick reference

```bash
uv sync                                           # install deps
uv run pytest                                     # run tests
uv run ruff check src/ tests/                     # lint
uv run mypy src/                                  # type check
uv run repovore process --url URL                 # process a single repo
uv run repovore process -i urls.txt               # process from file
uv run repovore status                            # show pipeline state
uv run repovore show owner/repo                   # pretty-print a card
```

## Architecture

Three-stage pipeline: **fetch** → **enrich** → **score**

- `fetch`: Core repo metadata from GitHub API
- `enrich`: Languages, contributors, commits, PRs, releases, root contents
- `score`: Compute maintenance_level + health_score, produce RepoCard

State tracked in SQLite (`data/repovore.db`). Each stage is independently resumable.

## Key files

- `src/repovore/models.py` — RepoCard Pydantic schema (central data contract)
- `src/repovore/github/client.py` — Async GitHub API client with rate limiting
- `src/repovore/scoring.py` — Pure scoring functions
- `src/repovore/pipeline.py` — Stage orchestrator
- `src/repovore/cli.py` — Typer CLI entry point

## Conventions

- Python 3.11+, type hints everywhere
- Pydantic v2 for models and config
- `ruff` for linting (line-length 100), `mypy --strict` for types
- Async via `asyncio.to_thread()` wrapping synchronous `PyGithub`
- Token from env var (name configured in YAML, never the secret itself)
- Config: `config/default.yml`, override via `REPOVORE_CONFIG` env var
