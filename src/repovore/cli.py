"""Typer CLI for Repovore — GitHub repository quality assessment pipeline."""

from __future__ import annotations

from pathlib import Path

import typer

from repovore.config import load_config
from repovore.db import Database

app = typer.Typer(name="repovore", help="GitHub repository quality assessment pipeline")


def _read_urls(input_file: Path | None, url_args: list[str] | None) -> list[str]:
    """Collect URLs from file and/or --url arguments."""
    urls: list[str] = []
    if input_file is not None:
        text = input_file.read_text(encoding="utf-8")
        for line in text.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                urls.append(line)
    if url_args:
        urls.extend(url_args)
    return urls


@app.command()
def process(
    input_file: Path | None = typer.Option(
        None, "--input", "-i", help="File with one GitHub URL per line"
    ),
    url: list[str] | None = typer.Option(None, "--url", "-u", help="Single GitHub URL"),
    all_repos: bool = typer.Option(
        False, "--all", "-a", help="Re-process all repos already in the database"
    ),
    config: Path | None = typer.Option(None, "--config", "-c", help="Path to config file"),
    stage: str | None = typer.Option(None, "--stage", "-s", help="Run only this stage"),
    from_stage: str | None = typer.Option(
        None, "--from-stage", "-f", help="Run from this stage onwards"
    ),
    force: bool = typer.Option(False, "--force", help="Re-process already completed items"),
) -> None:
    """Fetch, enrich, and score GitHub repositories."""
    try:
        cfg = load_config(config)
    except Exception as exc:
        typer.echo(f"Error loading config: {exc}", err=True)
        raise typer.Exit(code=1)

    urls = _read_urls(input_file, url)

    if all_repos:
        db_path = Path(cfg.output.data_dir) / "repovore.db"
        if not db_path.exists():
            typer.echo("No database found. Run 'repovore process' with URLs first.", err=True)
            raise typer.Exit(code=1)
        db = Database(db_path)
        db_urls = [f"https://github.com/{r['project_path']}" for r in db.get_repos()]
        if not db_urls:
            typer.echo("No repos in database.", err=True)
            raise typer.Exit(code=1)
        # Merge: DB repos + any explicitly provided URLs (deduplicated)
        seen = set(urls)
        for u in db_urls:
            if u not in seen:
                seen.add(u)
                urls.append(u)
        typer.echo(f"Processing {len(urls)} repos from database")

    if not urls:
        typer.echo("No URLs provided. Use --input FILE, --url URL, or --all.", err=True)
        raise typer.Exit(code=1)

    from repovore.pipeline import Pipeline  # noqa: PLC0415

    try:
        pipeline = Pipeline(cfg)
        stages = [stage] if stage else None
        pipeline.run(urls=urls, stages=stages, from_stage=from_stage, force=force)
    except Exception as exc:
        typer.echo(f"Pipeline error: {exc}", err=True)
        raise typer.Exit(code=1)


@app.command()
def status(
    config: Path | None = typer.Option(None, "--config", "-c", help="Path to config file"),
) -> None:
    """Show pipeline processing status."""
    try:
        cfg = load_config(config)
    except Exception as exc:
        typer.echo(f"Error loading config: {exc}", err=True)
        raise typer.Exit(code=1)

    db_path = Path(cfg.output.data_dir) / "repovore.db"
    if not db_path.exists():
        typer.echo("No database found. Run 'repovore process' first.")
        return

    db = Database(db_path)
    repos = db.get_repos()
    stats = db.get_run_stats()

    typer.echo(f"Total repos: {len(repos)}")
    typer.echo("")

    if not stats:
        typer.echo("No processing state recorded yet.")
        return

    all_statuses: list[str] = []
    for stage_stats in stats.values():
        for s in stage_stats:
            if s not in all_statuses:
                all_statuses.append(s)
    all_statuses.sort()

    col_width = 12
    stage_col_width = 16
    header = f"{'Stage':<{stage_col_width}}" + "".join(
        f"{s:<{col_width}}" for s in all_statuses
    )
    typer.echo(header)
    typer.echo("-" * len(header))

    for stage_name in sorted(stats):
        row = f"{stage_name:<{stage_col_width}}"
        for s in all_statuses:
            count = stats[stage_name].get(s, 0)
            row += f"{count:<{col_width}}"
        typer.echo(row)


@app.command()
def show(
    repo_ref: str = typer.Argument(help="Repo (e.g. 'owner/repo') or URL"),
    config: Path | None = typer.Option(None, "--config", "-c", help="Path to config file"),
) -> None:
    """Pretty-print a repository card."""
    from repovore.github.parser import parse_github_url  # noqa: PLC0415
    from repovore.output import load_card, pretty_print_card  # noqa: PLC0415

    try:
        cfg = load_config(config)
    except Exception as exc:
        typer.echo(f"Error loading config: {exc}", err=True)
        raise typer.Exit(code=1)

    if repo_ref.startswith("http"):
        try:
            parsed = parse_github_url(repo_ref)
            repo_ref = parsed.full_name
        except ValueError as exc:
            typer.echo(f"Invalid URL: {exc}", err=True)
            raise typer.Exit(code=1)

    slug = repo_ref.replace("/", "__")
    cards_dir = Path(cfg.output.data_dir) / "cards"
    card_path = cards_dir / f"{slug}.json"

    if not card_path.exists():
        import glob  # noqa: PLC0415

        pattern = str(cards_dir / f"*{slug}*.json")
        matches = glob.glob(pattern)
        if matches:
            card_path = Path(matches[0])

    if not card_path.exists():
        typer.echo(
            f"No card found for {repo_ref!r}. Run 'repovore process' first.",
            err=True,
        )
        raise typer.Exit(code=1)

    card = load_card(card_path)
    typer.echo(pretty_print_card(card))


@app.command("show-all")
def show_all(
    config: Path | None = typer.Option(None, "--config", "-c", help="Path to config file"),
) -> None:
    """Pretty-print all repository cards."""
    from repovore.output import load_card, pretty_print_card  # noqa: PLC0415

    try:
        cfg = load_config(config)
    except Exception as exc:
        typer.echo(f"Error loading config: {exc}", err=True)
        raise typer.Exit(code=1)

    cards_dir = Path(cfg.output.data_dir) / "cards"
    if not cards_dir.exists():
        typer.echo("No cards found. Run 'repovore process' first.", err=True)
        raise typer.Exit(code=1)

    card_files = sorted(cards_dir.glob("*.json"))
    if not card_files:
        typer.echo("No cards found. Run 'repovore process' first.", err=True)
        raise typer.Exit(code=1)

    for path in card_files:
        card = load_card(path)
        typer.echo(pretty_print_card(card))


@app.command()
def trending(
    limit: int = typer.Option(30, "--limit", "-n", help="Max repos to fetch"),
    days: int = typer.Option(7, "--days", "-d", help="Look-back window in days"),
    language: str | None = typer.Option(None, "--language", "-l", help="Filter by language"),
    topic: str | None = typer.Option(None, "--topic", "-t", help="Filter by topic or keywords"),
    min_stars: int = typer.Option(10, "--min-stars", help="Minimum star count"),
    config: Path | None = typer.Option(None, "--config", "-c", help="Path to config file"),
    force: bool = typer.Option(False, "--force", help="Re-process already completed items"),
) -> None:
    """Fetch trending repos via GitHub Search API and process them."""
    from repovore.trending import fetch_trending  # noqa: PLC0415

    try:
        cfg = load_config(config)
    except Exception as exc:
        typer.echo(f"Error loading config: {exc}", err=True)
        raise typer.Exit(code=1)

    filters = ", ".join(
        f for f in [
            f"{days}d",
            f"language={language}" if language else "",
            f"topic={topic}" if topic else "",
            f"stars>={min_stars}",
        ] if f
    )
    typer.echo(f"Searching GitHub trending repos ({filters})...")
    try:
        urls = fetch_trending(
            days=days,
            language=language,
            topic=topic,
            min_stars=min_stars,
            limit=limit,
            token_env_var=cfg.github.token_env_var,
        )
    except Exception as exc:
        typer.echo(f"Failed to fetch trending: {exc}", err=True)
        raise typer.Exit(code=1)

    if not urls:
        typer.echo("No repos found matching criteria.", err=True)
        raise typer.Exit(code=1)

    typer.echo(f"Found {len(urls)} repos:")
    for u in urls:
        typer.echo(f"  {u}")
    typer.echo("")

    from repovore.pipeline import Pipeline  # noqa: PLC0415

    try:
        pipeline = Pipeline(cfg)
        pipeline.run(urls=urls, force=force)
    except Exception as exc:
        typer.echo(f"Pipeline error: {exc}", err=True)
        raise typer.Exit(code=1)

    # Record that trending was updated
    db_path = Path(cfg.output.data_dir) / "repovore.db"
    db = Database(db_path)
    from datetime import UTC, datetime  # noqa: PLC0415

    db.set_metadata("trending_updated_at", datetime.now(UTC).isoformat())
    typer.echo("Trending data updated successfully.")


@app.command()
def backfill(
    config: Path | None = typer.Option(None, "--config", "-c", help="Path to config file"),
) -> None:
    """Find repos with incomplete/failed stages and re-run only what's needed."""
    from repovore.pipeline import STAGES, Pipeline  # noqa: PLC0415

    try:
        cfg = load_config(config)
    except Exception as exc:
        typer.echo(f"Error loading config: {exc}", err=True)
        raise typer.Exit(code=1)

    db_path = Path(cfg.output.data_dir) / "repovore.db"
    if not db_path.exists():
        typer.echo("No database found. Run 'repovore process' first.", err=True)
        raise typer.Exit(code=1)

    db = Database(db_path)
    incomplete = db.get_incomplete_repos(STAGES)

    if not incomplete:
        typer.echo("All repos are fully processed — nothing to backfill.")
        return

    typer.echo(f"Found {len(incomplete)} repo(s) needing backfill:")
    for repo in incomplete:
        statuses = ", ".join(
            f"{s}={repo['stage_statuses'][s]}" for s in STAGES
        )
        typer.echo(f"  {repo['project_path']:50s} {statuses}")
    typer.echo("")

    # Determine which stages need running (union of all missing stages, in order)
    stages_needed: list[str] = []
    for stage in STAGES:
        if any(stage in r["missing_stages"] for r in incomplete):
            stages_needed.append(stage)

    # Build URLs only for incomplete repos
    urls = [f"https://github.com/{r['project_path']}" for r in incomplete]

    typer.echo(f"Running stages {stages_needed} for {len(urls)} repo(s)...")
    pipeline = Pipeline(cfg)
    pipeline.run(urls=urls, stages=stages_needed)
    typer.echo("Backfill complete.")


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host", "-h", help="Bind host"),
    port: int = typer.Option(8000, "--port", "-p", help="Bind port"),
    config: Path | None = typer.Option(None, "--config", "-c", help="Path to config file"),
) -> None:
    """Start the Repovore web UI."""
    try:
        import uvicorn  # noqa: PLC0415
    except ImportError:
        typer.echo(
            "Web dependencies not installed. Run: uv pip install -e '.[web]'",
            err=True,
        )
        raise typer.Exit(code=1)

    from repovore.web.app import create_app  # noqa: PLC0415

    try:
        cfg = load_config(config)
    except Exception as exc:
        typer.echo(f"Error loading config: {exc}", err=True)
        raise typer.Exit(code=1)

    web_app = create_app(cfg)
    typer.echo(f"Starting Repovore web UI at http://{host}:{port}")
    uvicorn.run(web_app, host=host, port=port)


@app.command()
def reindex(
    config: Path | None = typer.Option(None, "--config", "-c", help="Path to config file"),
) -> None:
    """Rebuild the cards search index from existing JSON card files."""
    from repovore.output import reindex_all_cards  # noqa: PLC0415

    try:
        cfg = load_config(config)
    except Exception as exc:
        typer.echo(f"Error loading config: {exc}", err=True)
        raise typer.Exit(code=1)

    data_dir = Path(cfg.output.data_dir)
    db = Database(data_dir / "repovore.db")
    cards_dir = data_dir / "cards"

    count = reindex_all_cards(cards_dir, db)
    typer.echo(f"Indexed {count} cards into the database.")


if __name__ == "__main__":
    app()
