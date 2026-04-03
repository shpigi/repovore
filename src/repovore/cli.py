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
    config: Path | None = typer.Option(None, "--config", "-c", help="Path to config file"),
    stage: str | None = typer.Option(None, "--stage", "-s", help="Run only this stage"),
    from_stage: str | None = typer.Option(
        None, "--from-stage", "-f", help="Run from this stage onwards"
    ),
    force: bool = typer.Option(False, "--force", help="Re-process already completed items"),
) -> None:
    """Fetch, enrich, and score GitHub repositories."""
    urls = _read_urls(input_file, url)
    if not urls:
        typer.echo("No URLs provided. Use --input FILE or --url URL.", err=True)
        raise typer.Exit(code=1)

    try:
        cfg = load_config(config)
    except Exception as exc:
        typer.echo(f"Error loading config: {exc}", err=True)
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
