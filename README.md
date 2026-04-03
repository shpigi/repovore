# Repovore

GitHub repository quality assessment pipeline. Fetches metadata for GitHub repositories and produces structured "cards" with health and maintenance signals.

## Setup

```bash
# Install uv if needed: https://docs.astral.sh/uv/
uv sync
```

Set your GitHub token:

```bash
export GITHUB_TOKEN="your-personal-access-token"
```

## Usage

```bash
# Process a single repo
repovore process --url https://github.com/owner/repo

# Process multiple repos from a file (one URL per line)
repovore process -i repos.txt

# View pipeline status
repovore status

# Pretty-print a card
repovore show owner/repo
```

## Card Fields

Each card includes:

- **Core**: name, owner, description, stars, forks, languages, license, topics
- **Activity**: last commit, commit frequency, issues, pull requests, releases
- **Quality signals**: README, LICENSE, CI, CONTRIBUTING presence; bus factor
- **Scores**: maintenance level (active/maintained/minimal/abandoned), health score (0-100)

## Configuration

Edit `config/default.yml` or set `REPOVORE_CONFIG` to a custom YAML path. Scoring weights are configurable.
