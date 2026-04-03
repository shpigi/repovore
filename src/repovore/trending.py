"""Fetch trending repositories from OSS Insight and GitHub Search API."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta

import requests

logger = logging.getLogger(__name__)

# OSS Insight period names mapped from approximate day counts
_OSSINSIGHT_PERIODS = {
    1: "past_24_hours",
    7: "past_week",
    30: "past_month",
}


def _ossinsight_period(days: int) -> str:
    """Pick the closest OSS Insight period for a given day count."""
    if days <= 1:
        return "past_24_hours"
    if days <= 14:
        return "past_week"
    return "past_month"


def _fetch_ossinsight(
    *,
    days: int = 7,
    language: str | None = None,
    limit: int = 100,
) -> list[str]:
    """Fetch trending repos from OSS Insight (engagement-velocity ranked).

    Returns up to 100 repo URLs sorted by OSS Insight's total_score.
    """
    period = _ossinsight_period(days)
    params: dict[str, str] = {"period": period}
    if language:
        params["language"] = language

    resp = requests.get(
        "https://api.ossinsight.io/v1/trends/repos/",
        params=params,
        headers={"Accept": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    rows = resp.json()["data"]["rows"]

    urls: list[str] = []
    for row in rows[:limit]:
        name = row.get("repo_name", "")
        if name:
            urls.append(f"https://github.com/{name}")
    return urls


def _search_github(
    query: str,
    *,
    limit: int,
    token: str,
) -> list[dict[str, object]]:
    """Run a GitHub search/repositories query and return items."""
    headers: dict[str, str] = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    per_page = min(limit, 100)
    items: list[dict[str, object]] = []
    page = 1

    while len(items) < limit:
        resp = requests.get(
            "https://api.github.com/search/repositories",
            params={
                "q": query,
                "sort": "stars",
                "order": "desc",
                "per_page": str(per_page),
                "page": str(page),
            },
            headers=headers,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        batch = data.get("items", [])
        if not batch:
            break
        items.extend(batch)
        if len(batch) < per_page or page * per_page >= 1000:
            break
        page += 1

    return items[:limit]


def fetch_trending(
    *,
    days: int = 7,
    language: str | None = None,
    topic: str | None = None,
    min_stars: int = 10,
    limit: int = 30,
    token_env_var: str = "GITHUB_TOKEN",
) -> list[str]:
    """Fetch trending repos, ranked by actual engagement velocity.

    **Primary source**: OSS Insight — scores repos by recent stars, forks,
    PRs, and pushes.  Returns up to 100 repos with no auth required.

    **Fallback / supplement**: GitHub Search API — used when a topic filter
    is specified (OSS Insight doesn't support topics), or when OSS Insight
    doesn't return enough results.

    Args:
        days: Look-back window in days.
        language: Filter to a specific language (e.g. "Python").
        topic: Filter to a specific topic or free-text keywords
               (forces GitHub Search API).
        min_stars: Minimum star count (GitHub Search fallback only).
        limit: Maximum number of repos to return.
        token_env_var: Environment variable holding a GitHub token.

    Returns:
        List of GitHub URLs like ``https://github.com/owner/repo``.
    """
    seen: set[str] = set()
    urls: list[str] = []

    # ── OSS Insight (primary, unless topic filter forces GitHub Search) ──
    if not topic:
        try:
            oss_urls = _fetch_ossinsight(days=days, language=language, limit=limit)
            for u in oss_urls:
                name = u.removeprefix("https://github.com/")
                if name not in seen:
                    seen.add(name)
                    urls.append(u)
            logger.info("OSS Insight returned %d repos", len(oss_urls))
        except Exception:
            logger.warning("OSS Insight unavailable, falling back to GitHub Search")

    # ── GitHub Search API (topic queries, or to fill remaining slots) ──
    remaining = limit - len(urls)
    if remaining > 0:
        token = os.environ.get(token_env_var, "")
        since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

        lang_filter = f" language:{language}" if language else ""
        topic_filter = f" topic:{topic}" if topic else ""
        filters = f"{lang_filter}{topic_filter}"

        q = f"created:>{since} stars:>={min_stars}{filters}"
        items = _search_github(q, limit=remaining, token=token)
        for item in items:
            name = str(item["full_name"])
            if name not in seen:
                seen.add(name)
                urls.append(str(item["html_url"]))
            if len(urls) >= limit:
                break

    logger.info("Trending search returned %d repos total", len(urls))
    return urls[:limit]
