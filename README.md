# Repovore

Discover and assess GitHub repositories. Repovore fetches trending repos or any repo you point it at, analyzes their health and maintenance signals, and serves the results as browsable cards in a web UI.

## Quick start

```bash
# Install
uv sync

# Set your GitHub token
export GITHUB_TOKEN="your-personal-access-token"

# Discover trending repos, process them, and browse the results
uv run repovore trending
uv run repovore serve
```

Open http://127.0.0.1:8000 to browse repo cards.

## What you get

Each repo card includes:

- **Identity** — name, owner, description, stars, forks, languages, license, topics
- **Activity** — last commit, commit frequency, open issues, pull requests, releases
- **Quality signals** — README, LICENSE, CI, CONTRIBUTING presence; bus factor
- **Scores** — maintenance level (active / maintained / minimal / abandoned), health score (0–100)

## CLI reference

### Trending

Fetch trending repos and process them through the pipeline:

```bash
uv run repovore trending                              # top 30 repos from the past week
uv run repovore trending --language Python --days 3    # Python repos, last 3 days
uv run repovore trending --topic "machine learning"    # filter by topic
uv run repovore trending --min-stars 100 --limit 50    # raise the bar, fetch more
```

### Process

Process specific repos by URL:

```bash
uv run repovore process --url https://github.com/owner/repo
uv run repovore process -i repos.txt                   # one URL per line
uv run repovore process --all                           # re-process everything in the DB
uv run repovore process --stage enrich                  # run a single stage
uv run repovore process --from-stage score              # run from a stage onwards
uv run repovore process --force                         # re-process already completed items
```

### View cards

```bash
uv run repovore show owner/repo                        # pretty-print one card
uv run repovore show-all                               # pretty-print all cards
```

### Web UI

```bash
uv run repovore serve                                  # http://127.0.0.1:8000
uv run repovore serve --host 0.0.0.0 --port 3000       # custom bind
```

### Maintenance

```bash
uv run repovore status                                 # pipeline processing status
uv run repovore backfill                               # retry incomplete/failed repos
uv run repovore reindex                                # rebuild search index from card files
```

## Configuration

Edit `config/default.yml` or point `REPOVORE_CONFIG` to a custom YAML file. Scoring weights are configurable.
