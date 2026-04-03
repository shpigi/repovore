"""Card serialization and output."""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

from rich.columns import Columns
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from repovore.db import Database
from repovore.models import RepoCard
from repovore.utils import get_logger

logger = get_logger(__name__)


def write_card(card: RepoCard, cards_dir: Path, db: Database | None = None) -> Path:
    """Write a single card as JSON. Returns the output path.

    If *db* is provided, also upserts the card into the SQLite index.
    """
    cards_dir.mkdir(parents=True, exist_ok=True)
    slug = f"{card.owner}__{card.name}"
    path = cards_dir / f"{slug}.json"
    path.write_text(card.model_dump_json(indent=2), encoding="utf-8")
    if db is not None:
        db.upsert_card_index(card.model_dump())
    return path


def write_cards_jsonl(cards: list[RepoCard], output_path: Path) -> None:
    """Write all cards as a JSONL file (one JSON object per line)."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as fh:
        for card in cards:
            fh.write(card.model_dump_json() + "\n")
    logger.info("Wrote %d cards to %s", len(cards), output_path)


def load_card(path: Path) -> RepoCard:
    """Load a card from a JSON file."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    return RepoCard.model_validate(raw)


def reindex_all_cards(cards_dir: Path, db: Database) -> int:
    """Scan existing card JSON files and populate the SQLite cards index.

    Returns the number of cards indexed.
    """
    count = 0
    if not cards_dir.is_dir():
        return count
    for path in sorted(cards_dir.glob("*.json")):
        try:
            card = load_card(path)
            db.upsert_card_index(card.model_dump())
            count += 1
        except Exception:
            logger.warning("Failed to index card %s", path, exc_info=True)
    logger.info("Indexed %d cards from %s", count, cards_dir)
    return count


# ── Helpers ──────────────────────────────────────────────────────────────────

def _health_color(score: float) -> str:
    if score >= 75:
        return "bright_green"
    if score >= 50:
        return "yellow"
    return "red"


def _maintenance_color(level: str) -> str:
    return {"active": "bright_green", "maintained": "green",
            "minimal": "yellow", "abandoned": "red"}.get(level, "white")


def _check(val: bool) -> Text:
    return Text("✔", style="green") if val else Text("✘", style="red dim")


def _opt(val: int | float | None, fmt: str = "") -> str:
    return f"[dim](unknown)[/dim]" if val is None else f"{val:{fmt}}"


def pretty_print_card(card: RepoCard) -> str:
    """Render a card with Rich and return the ANSI string."""
    buf = StringIO()
    c = Console(file=buf, highlight=False, force_terminal=True)

    # ── Header ───────────────────────────────────────────────────────────────
    title = Text()
    title.append(f"{card.owner}/", style="dim white")
    title.append(card.name, style="bold white")
    if card.archived:
        title.append("  [ARCHIVED]", style="bold red")

    header = Text()
    header.append_text(title)
    header.append("\n")
    star_style = "bold yellow" if card.stars >= 10_000 else "yellow"
    fork_style = "bold cyan" if card.forks >= 1_000 else "cyan"
    header.append(f"⭐ {card.stars:,}  ", style=star_style)
    header.append(f"🍴 {card.forks:,}  ", style=fork_style)
    if card.license:
        header.append(f"⚖  {card.license}  ", style="dim")
    display_tags = card.tags if card.tags else card.topics
    if display_tags:
        for t in display_tags[:10]:
            header.append(f"#{t} ", style="dim cyan")
    if card.description:
        header.append(f"\n{card.description}", style="italic dim white")
    if card.repo_age_days is not None or card.stars_per_year is not None:
        header.append("\n")
        if card.repo_age_days is not None:
            header.append(f"{card.repo_age_days / 365:.1f}y old", style="dim")
        if card.stars_per_year is not None:
            spy = card.stars_per_year
            vel_style = "bold yellow" if spy >= 10_000 else "yellow" if spy >= 1_000 else "dim"
            header.append("  ·  " if card.repo_age_days is not None else "", style="dim")
            header.append(f"⭐ {spy:,.0f}/yr", style=vel_style)
    header.append(f"\n{card.url}", style="dim underline")

    c.print(Panel(header, style="bold blue", padding=(0, 2)))

    # ── Score banner ─────────────────────────────────────────────────────────
    score_color = _health_color(card.health_score)
    maint_color = _maintenance_color(card.maintenance_level)

    banner = Table.grid(padding=(0, 6))
    banner.add_column(justify="center")
    banner.add_column(justify="center")
    banner.add_column(justify="center")

    score_t = Text()
    score_t.append(f"{card.health_score:.0f}", style=f"bold {score_color}")
    score_t.append(" / 100", style="dim")

    gh_t = Text()
    if card.community_health_pct is not None:
        gh_t.append(f"{card.community_health_pct}%", style="bold cyan")
    else:
        gh_t.append("—", style="dim")

    maint_t = Text(card.maintenance_level.upper(), style=f"bold {maint_color}")

    banner.add_row(
        Text("HEALTH SCORE", style="dim"), Text("GH COMMUNITY", style="dim"), Text("MAINTENANCE", style="dim"),
    )
    banner.add_row(score_t, gh_t, maint_t)
    c.print(Panel(banner, style=score_color, padding=(0, 2)))

    # ── Activity panel ───────────────────────────────────────────────────────
    def date_str(dt: object) -> str:
        return "[dim](unknown)[/dim]" if dt is None else str(dt)[:10]

    def last_commit_text() -> Text:
        dt = card.last_commit_date
        if dt is None:
            return Text("(unknown)", style="dim")
        from datetime import UTC, datetime  # noqa: PLC0415
        days = (datetime.now(UTC) - dt).days
        color = "bright_green" if days <= 14 else "green" if days <= 60 else "yellow" if days <= 180 else "red"
        return Text(str(dt)[:10], style=color)

    act = Table.grid(padding=(0, 2))
    act.add_column(style="dim", min_width=22)
    act.add_column()

    add_s = f"[green]+{card.code_churn_additions_52w:,}[/green]" if card.code_churn_additions_52w else "[dim]?[/dim]"
    del_s = f"[red]-{card.code_churn_deletions_52w:,}[/red]" if card.code_churn_deletions_52w else "[dim]?[/dim]"

    def commits_text() -> Text:
        v = card.commit_frequency_90d
        if v is None:
            return Text("(unknown)", style="dim")
        color = "bright_green" if v >= 30 else "green" if v >= 10 else "yellow" if v >= 3 else "red"
        return Text(str(v), style=color)

    def days_since_release_text() -> Text:
        v = card.days_since_last_release
        if v is None:
            return Text("(unknown)", style="dim")
        color = "bright_green" if v <= 30 else "green" if v <= 90 else "yellow" if v <= 365 else "red"
        return Text(str(v), style=color)

    def owner_commits_text() -> Text:
        if card.owner_commit_pct is None:
            return Text("(unknown)", style="dim")
        pct = card.owner_commit_pct
        color = "bright_green" if pct >= 50 else "green" if pct >= 20 else "yellow" if pct >= 5 else "red"
        return Text(f"{pct:.0f}%", style=color)

    act.add_row("Last commit", last_commit_text())
    act.add_row("Commits (90d)", commits_text())
    def release_tag_text() -> Text | str:
        tag = card.latest_release_tag
        if not tag:
            return date_str(card.last_release_date)
        t = Text()
        try:
            major = int(tag.lstrip("vV").split(".")[0])
            style = "dim yellow" if major == 0 else "green"
        except (ValueError, IndexError):
            style = "white"
        t.append(tag, style=style)
        return t

    act.add_row("Last release", release_tag_text())
    act.add_row("Days since release", days_since_release_text())
    act.add_row("Owner commits (52w)", owner_commits_text())
    act.add_row("Churn (52w)", f"{add_s}  {del_s}")
    if card.top_contributors:
        contrib_text = Text()
        for i, contrib in enumerate(card.top_contributors):
            if i:
                contrib_text.append("  ")
            contrib_text.append(f"@{contrib['login']}", style="bold")
            pct = float(contrib["pct"])
            pct_color = "red" if pct >= 70 else "yellow" if pct >= 40 else "dim"
            contrib_text.append(f" {pct:.0f}%", style=pct_color)
        act.add_row("Top contributors", contrib_text)

    # ── Quality panel ────────────────────────────────────────────────────────
    def ci_text() -> Text:
        t = _check(card.has_ci)
        if card.has_ci and card.ci_success_rate is not None:
            t.append(f" {card.ci_success_rate:.0f}% passing", style=_health_color(card.ci_success_rate))
        return t

    def dep_text() -> Text:
        if card.dependabot_open_total is None:
            return Text("?", style="dim")
        if card.dependabot_open_total == 0:
            return Text("0 ✔", style="green")
        t = Text(str(card.dependabot_open_total), style="red bold")
        if card.dependabot_open_critical:
            t.append(f" ({card.dependabot_open_critical} crit)", style="red")
        return t

    def bus_text() -> Text:
        if card.bus_factor_top3_pct is None:
            return Text("?", style="dim")
        pct = card.bus_factor_top3_pct
        color = "green" if pct <= 50 else "yellow" if pct <= 80 else "red"
        return Text(f"{pct:.0f}%", style=color)

    qual = Table.grid(padding=(0, 2))
    qual.add_column(style="dim", min_width=18)
    qual.add_column(min_width=6)
    qual.add_column(style="dim", min_width=18)
    qual.add_column(min_width=6)

    qual.add_row("README",         _check(card.has_readme),         "LICENSE",     _check(card.has_license))
    qual.add_row("CI",             ci_text(),                        "Contributing", _check(card.has_contributing))
    qual.add_row("Code of Conduct", _check(card.has_code_of_conduct), "Funding",    _check(card.has_funding))
    qual.add_row("Bus factor",     bus_text(),                       "Dependabot",  dep_text())

    c.print(Columns([
        Panel(act,  title="[bold]Activity[/bold]",       style="blue",    padding=(0, 2)),
        Panel(qual, title="[bold]Quality Signals[/bold]", style="magenta", padding=(0, 2)),
    ]))

    # ── Issues & PRs ─────────────────────────────────────────────────────────
    prs = Table.grid(padding=(0, 4))
    prs.add_column(style="dim")
    prs.add_column()
    prs.add_column(style="dim")
    prs.add_column()

    def open_ratio_style(open_: int, closed: int) -> str:
        if open_ == 0:
            return "bright_green"
        if closed == 0:
            return "red"
        ratio = open_ / (open_ + closed)
        return "green" if ratio <= 0.1 else "yellow" if ratio <= 0.3 else "red"

    prs.add_row("Issues open",  Text(str(card.open_issues), style=open_ratio_style(card.open_issues, card.closed_issues)),
                "Issues closed", Text(str(card.closed_issues), style="green"))
    prs.add_row("PRs open",     Text(str(card.open_prs), style=open_ratio_style(card.open_prs, card.closed_prs)),
                "PRs closed",    Text(str(card.closed_prs), style="green"))

    c.print(Panel(prs, title="[bold]Issues & PRs[/bold]", style="cyan", padding=(0, 2)))

    # ── Owner ─────────────────────────────────────────────────────────────────
    own = Table.grid(padding=(0, 2))
    own.add_column(style="dim", min_width=22)
    own.add_column()

    def owner_followers_text() -> Text:
        v = card.owner_followers
        if v is None:
            return Text("(unknown)", style="dim")
        style = "bold cyan" if v >= 10_000 else "cyan" if v >= 500 else "dim"
        return Text(f"{v:,}", style=style)

    own.add_row("Type", card.owner_type or "(unknown)")
    own.add_row("Followers", owner_followers_text())
    own.add_row("Public repos", _opt(card.owner_public_repos))

    c.print(Panel(own, title=f"[bold]Owner — {card.owner}[/bold]", style="yellow", padding=(0, 2)))

    # ── Languages ────────────────────────────────────────────────────────────
    if card.languages:
        langs = [(lang, pct) for lang, pct in sorted(card.languages.items(), key=lambda x: -x[1]) if pct >= 0.1]
        lang_table = Table.grid(padding=(0, 1))
        lang_table.add_column(style="dim", min_width=18)
        lang_table.add_column(min_width=32)
        lang_table.add_column(style="cyan", min_width=6, justify="right")

        bar_width = 30
        for lang, pct in langs:
            filled = round(pct / 100 * bar_width)
            bar = Text("█" * filled, style="cyan") + Text("░" * (bar_width - filled), style="dim")
            lang_table.add_row(lang, bar, f"{pct:.1f}%")

        c.print(Panel(lang_table, title="[bold]Languages[/bold]", style="green", padding=(0, 2)))

    # ── LLM Assessment ───────────────────────────────────────────────────────
    if card.summary or card.verdict or card.strengths or card.concerns:
        verdict_color = {
            "adopt": "bright_green", "evaluate": "yellow",
            "hold": "orange1", "avoid": "red",
        }.get(card.verdict or "", "white")

        llm_text = Text()
        if card.verdict:
            llm_text.append(f" {card.verdict.upper()} ", style=f"bold reverse {verdict_color}")
            llm_text.append("  ")
        if card.summary:
            llm_text.append(card.summary, style="italic dim white")

        if card.strengths or card.concerns:
            llm_text.append("\n")
            for s in card.strengths:
                llm_text.append("\n  ✔ ", style="bold green")
                llm_text.append(s, style="green")
            for s in card.concerns:
                llm_text.append("\n  ✘ ", style="bold red")
                llm_text.append(s, style="red")

        c.print(Panel(llm_text, title="[bold]LLM Assessment[/bold]", style=verdict_color, padding=(0, 2)))

    # ── Footer ───────────────────────────────────────────────────────────────
    c.print(f"  [dim]Fetched {card.fetched_at.strftime('%Y-%m-%d %H:%M UTC')} · repovore v{card.card_version}[/dim]\n")

    return buf.getvalue()
