"""Async GitHub API client with rate limiting and retry."""

from __future__ import annotations

import asyncio
import os
import threading
import time
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from urllib.parse import parse_qs, urlparse

import requests
from github import Auth, Github, GithubException
from github.GithubRetry import GithubRetry

from repovore.config import GitHubConfig
from repovore.utils import get_logger

logger = get_logger(__name__)


class TokenBucketRateLimiter:
    """Simple token-bucket rate limiter for async use."""

    def __init__(self, rate: float) -> None:
        self._rate = rate
        self._max_tokens = rate
        self._tokens = rate
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_refill
            self._tokens = min(self._max_tokens, self._tokens + elapsed * self._rate)
            self._last_refill = now

            if self._tokens < 1:
                wait = (1 - self._tokens) / self._rate
                await asyncio.sleep(wait)
                self._tokens = 0
            else:
                self._tokens -= 1


class GitHubClient:
    """Async wrapper around PyGithub with concurrency control and rate limiting."""

    def __init__(self, config: GitHubConfig) -> None:
        self._config = config
        self._token = os.environ.get(config.token_env_var, "")
        self._local = threading.local()  # per-thread Github instances (PyGithub not thread-safe)
        self._semaphore: asyncio.Semaphore | None = None
        self._rate_limiter: TokenBucketRateLimiter | None = None

    def reset_async_primitives(self) -> None:
        """Recreate event-loop-bound primitives. Call once inside each asyncio.run() scope."""
        self._semaphore = asyncio.Semaphore(self._config.max_concurrent)
        self._rate_limiter = TokenBucketRateLimiter(self._config.requests_per_second)

    @property
    def _gh(self) -> Github:
        """Return the thread-local Github instance, creating it on first access."""
        if not hasattr(self._local, "gh"):
            auth = Auth.Token(self._token) if self._token else None
            # retry=0 disables PyGithub's built-in GithubRetry so our _call controls retries
            self._local.gh = Github(auth=auth, retry=GithubRetry(total=0))
        return self._local.gh  # type: ignore[no-any-return]

    async def _call(self, func: Any, *args: Any, **kwargs: Any) -> Any:
        """Run a synchronous PyGithub call with semaphore + rate limiting + retry."""
        assert self._semaphore is not None and self._rate_limiter is not None, \
            "Call reset_async_primitives() inside the asyncio.run() scope before using the client."
        for attempt in range(self._config.max_retries + 1):
            async with self._semaphore:
                await self._rate_limiter.acquire()
                try:
                    return await asyncio.to_thread(func, *args, **kwargs)
                except GithubException as exc:
                    if exc.status == 403 and attempt < self._config.max_retries:
                        # Rate limited
                        retry_after = float(
                            (exc.headers or {}).get("Retry-After", "")
                            or self._config.base_backoff * 2**attempt
                        )
                        retry_after = min(retry_after, self._config.max_backoff)
                        logger.warning(
                            "Rate limited (403), retrying in %.1fs (attempt %d/%d)",
                            retry_after,
                            attempt + 1,
                            self._config.max_retries + 1,
                        )
                        await asyncio.sleep(retry_after)
                        continue
                    raise
                except Exception:
                    if attempt < self._config.max_retries:
                        delay = min(
                            self._config.base_backoff * 2**attempt,
                            self._config.max_backoff,
                        )
                        logger.warning(
                            "Request failed, retrying in %.1fs (attempt %d/%d)",
                            delay,
                            attempt + 1,
                            self._config.max_retries + 1,
                        )
                        await asyncio.sleep(delay)
                        continue
                    raise
        raise RuntimeError("Exhausted retries")

    async def fetch_repo(self, full_name: str) -> dict[str, Any]:
        """Fetch core repository metadata."""
        def _get() -> dict[str, Any]:
            repo = self._gh.get_repo(full_name)
            return {
                "id": repo.id,
                "name": repo.name,
                "full_name": repo.full_name,
                "description": repo.description,
                "html_url": repo.html_url,
                "stargazers_count": repo.stargazers_count,
                "forks_count": repo.forks_count,
                "open_issues_count": repo.open_issues_count,
                "archived": repo.archived,
                "created_at": repo.created_at.isoformat() if repo.created_at else None,
                "updated_at": repo.updated_at.isoformat() if repo.updated_at else None,
                "pushed_at": repo.pushed_at.isoformat() if repo.pushed_at else None,
                "default_branch": repo.default_branch,
                # repo.topics uses already-fetched data from the get_repo() call;
                # repo.get_topics() would fire a second HTTP request to /topics.
                "topics": repo.topics,
                "license": (
                    {"key": repo.license.key, "name": repo.license.name}
                    if repo.license
                    else None
                ),
                "language": repo.language,
            }

        return cast(dict[str, Any], await self._call(_get))

    async def fetch_languages(self, full_name: str) -> dict[str, float]:
        """Fetch language breakdown as percentages."""
        def _get() -> dict[str, float]:
            token = self._token
            headers: dict[str, str] = {"Accept": "application/vnd.github+json"}
            if token:
                headers["Authorization"] = f"Bearer {token}"
            resp = requests.get(
                f"https://api.github.com/repos/{full_name}/languages",
                headers=headers,
                timeout=30,
            )
            resp.raise_for_status()
            langs: dict[str, int] = resp.json()
            total = sum(langs.values())
            if total == 0:
                return {}
            return {lang: round(bytes_ / total * 100, 1) for lang, bytes_ in langs.items()}

        return cast(dict[str, float], await self._call(_get))

    async def fetch_contributors(self, full_name: str) -> list[dict[str, Any]]:
        """Fetch top 100 contributors by commit count."""
        def _get() -> list[dict[str, Any]]:
            repo = self._gh.get_repo(full_name)
            result = []
            for c in repo.get_contributors():
                result.append({"login": c.login, "commits": c.contributions})
                if len(result) >= 100:
                    break
            return result

        return cast(list[dict[str, Any]], await self._call(_get))

    async def fetch_recent_commits(
        self, full_name: str, since_days: int = 90
    ) -> int:
        """Count commits from the last N days.

        Uses a single REST call with per_page=1 and reads the Link header to
        determine the total page count (= commit count).  This avoids the
        PyGithub totalCount path, which issues a second HTTP request internally.
        Falls back to an explicit loop with a hard cap if the Link header is
        absent (small repos that fit on one page).
        """
        def _get() -> int:
            since = datetime.now(UTC) - timedelta(days=since_days)
            token = self._token
            headers: dict[str, str] = {"Accept": "application/vnd.github+json"}
            if token:
                headers["Authorization"] = f"Bearer {token}"
            resp = requests.get(
                f"https://api.github.com/repos/{full_name}/commits",
                headers=headers,
                params={
                    "since": since.isoformat(),
                    "per_page": "1",
                },
                timeout=30,
            )
            resp.raise_for_status()
            # If the Link header has a "last" rel, parse the page number from it.
            link_header = resp.headers.get("Link", "")
            if 'rel="last"' in link_header:
                for part in link_header.split(","):
                    if 'rel="last"' in part:
                        url_part = part.split(";")[0].strip().strip("<>")
                        qs = parse_qs(urlparse(url_part).query)
                        pages = qs.get("page", ["0"])[0]
                        return int(pages)
            # Fits on one page — count what we got (with per_page=1, 0 or 1).
            # Fetch properly with higher per_page to get the real count.
            resp2 = requests.get(
                f"https://api.github.com/repos/{full_name}/commits",
                headers=headers,
                params={
                    "since": since.isoformat(),
                    "per_page": "100",
                },
                timeout=30,
            )
            resp2.raise_for_status()
            return len(resp2.json())

        return cast(int, await self._call(_get))

    async def fetch_issues_stats(self, full_name: str) -> dict[str, int]:
        """Fetch open/closed issue counts (excluding PRs).

        Each count uses a single REST call (per_page=1 + Link header), so the
        two counts are fetched as two independent _call() invocations to avoid
        holding the semaphore across multiple HTTP requests.
        """
        def _count(state: str) -> int:
            token = self._token
            headers: dict[str, str] = {"Accept": "application/vnd.github+json"}
            if token:
                headers["Authorization"] = f"Bearer {token}"
            resp = requests.get(
                f"https://api.github.com/repos/{full_name}/issues",
                headers=headers,
                params={"state": state, "per_page": "1"},
                timeout=30,
            )
            resp.raise_for_status()
            link_header = resp.headers.get("Link", "")
            if 'rel="last"' in link_header:
                for part in link_header.split(","):
                    if 'rel="last"' in part:
                        url_part = part.split(";")[0].strip().strip("<>")
                        qs = parse_qs(urlparse(url_part).query)
                        pages = qs.get("page", ["0"])[0]
                        return int(pages)
            return len(resp.json())

        open_count, closed_count = await asyncio.gather(
            self._call(_count, "open"),
            self._call(_count, "closed"),
        )
        return {"open": open_count, "closed": closed_count}

    async def fetch_pull_requests(self, full_name: str) -> dict[str, int]:
        """Fetch open/closed PR counts.

        Each count uses a single REST call (per_page=1 + Link header), so the
        two counts are fetched as two independent _call() invocations to avoid
        holding the semaphore across multiple HTTP requests.
        """
        def _count(state: str) -> int:
            token = self._token
            headers: dict[str, str] = {"Accept": "application/vnd.github+json"}
            if token:
                headers["Authorization"] = f"Bearer {token}"
            resp = requests.get(
                f"https://api.github.com/repos/{full_name}/pulls",
                headers=headers,
                params={"state": state, "per_page": "1"},
                timeout=30,
            )
            resp.raise_for_status()
            link_header = resp.headers.get("Link", "")
            if 'rel="last"' in link_header:
                for part in link_header.split(","):
                    if 'rel="last"' in part:
                        url_part = part.split(";")[0].strip().strip("<>")
                        qs = parse_qs(urlparse(url_part).query)
                        pages = qs.get("page", ["0"])[0]
                        return int(pages)
            return len(resp.json())

        open_count, closed_count = await asyncio.gather(
            self._call(_count, "open"),
            self._call(_count, "closed"),
        )
        return {"open": open_count, "closed": closed_count}

    async def fetch_releases(self, full_name: str) -> list[dict[str, Any]]:
        """Fetch recent releases."""
        def _get() -> list[dict[str, Any]]:
            repo = self._gh.get_repo(full_name)
            result: list[dict[str, Any]] = []
            for r in repo.get_releases():
                if len(result) >= 10:
                    break
                result.append({
                    "tag_name": r.tag_name,
                    "name": r.title,
                    "published_at": (
                        r.published_at.isoformat() if r.published_at else None
                    ),
                    "prerelease": r.prerelease,
                })
            return result

        return cast(list[dict[str, Any]], await self._call(_get))

    async def fetch_root_contents(self, full_name: str) -> list[dict[str, str]]:
        """Fetch root directory listing to check file existence."""
        def _get() -> list[dict[str, str]]:
            repo = self._gh.get_repo(full_name)
            try:
                contents = repo.get_contents("")
                if not isinstance(contents, list):
                    contents = [contents]
                return [
                    {"name": c.name, "type": c.type} for c in contents
                ]
            except GithubException:
                return []

        return cast(list[dict[str, str]], await self._call(_get))

    async def fetch_community_profile(self, full_name: str) -> dict[str, Any]:
        """Fetch community profile via REST API (health percentage, files)."""
        def _get() -> dict[str, Any]:
            token = os.environ.get(self._config.token_env_var, "")
            headers: dict[str, str] = {
                "Accept": "application/vnd.github+json",
            }
            if token:
                headers["Authorization"] = f"Bearer {token}"
            resp = requests.get(
                f"https://api.github.com/repos/{full_name}/community/profile",
                headers=headers,
                timeout=30,
            )
            if resp.status_code == 200:
                return resp.json()  # type: ignore[no-any-return]
            return {}

        return cast(dict[str, Any], await self._call(_get))

    async def fetch_dependabot_alerts(self, full_name: str) -> dict[str, Any]:
        """Fetch open Dependabot alert counts by severity."""
        def _get() -> dict[str, Any]:
            token = os.environ.get(self._config.token_env_var, "")
            if not token:
                return {"total": 0, "critical": 0, "high": 0, "medium": 0, "low": 0}
            headers = {
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
            }
            resp = requests.get(
                f"https://api.github.com/repos/{full_name}/dependabot/alerts",
                headers=headers,
                params={"state": "open", "per_page": "100"},
                timeout=30,
            )
            if resp.status_code != 200:
                return {"total": 0, "critical": 0, "high": 0, "medium": 0, "low": 0}
            alerts = resp.json()
            counts: dict[str, int] = {
                "critical": 0, "high": 0, "medium": 0, "low": 0,
            }
            for alert in alerts:
                severity = (
                    alert.get("security_advisory", {})
                    .get("severity", "low")
                    .lower()
                )
                if severity in counts:
                    counts[severity] += 1
            return {"total": len(alerts), **counts}

        return cast(dict[str, Any], await self._call(_get))

    def _stats_get(self, full_name: str, endpoint: str) -> dict[str, Any] | list[Any] | None:
        """GET a GitHub stats endpoint, retrying on 202 (computing) up to 3 times.

        Returns None if GitHub hasn't finished computing the stats within the retry
        budget (3 attempts × 2s sleep = ~6s max). Callers should treat None as
        unknown rather than zero.
        """
        token = self._token
        headers: dict[str, str] = {"Accept": "application/vnd.github+json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        url = f"https://api.github.com/repos/{full_name}/{endpoint}"
        for attempt in range(3):
            resp = requests.get(url, headers=headers, timeout=30)
            if resp.status_code == 200:
                return cast(dict[str, Any] | list[Any], resp.json())
            if resp.status_code == 202:
                if attempt < 2:
                    time.sleep(2)
                continue
            return None
        logger.warning("Stats not ready after 3 attempts for %s/%s — skipping", full_name, endpoint)
        return None

    async def fetch_participation_stats(
        self, full_name: str
    ) -> dict[str, Any]:
        """Fetch owner vs community commit participation (52 weeks)."""
        def _get() -> dict[str, Any]:
            data = self._stats_get(full_name, "stats/participation")
            if not isinstance(data, dict):
                return {}
            owner_weeks: list[int] = data.get("owner", [])
            all_weeks: list[int] = data.get("all", [])
            owner_total = sum(owner_weeks)
            all_total = sum(all_weeks)
            return {
                "owner_commits_52w": owner_total,
                "all_commits_52w": all_total,
                "owner_pct": round(owner_total / all_total * 100, 1) if all_total > 0 else 0.0,
            }

        return cast(dict[str, Any], await self._call(_get))

    async def fetch_code_frequency(self, full_name: str) -> dict[str, int | None]:
        """Fetch aggregate code additions/deletions over the last year."""
        def _get() -> dict[str, int | None]:
            data = self._stats_get(full_name, "stats/code_frequency")
            if not isinstance(data, list):
                return {"additions_52w": None, "deletions_52w": None}
            total_add = sum(int(week[1]) for week in data if len(week) >= 3)
            total_del = sum(abs(int(week[2])) for week in data if len(week) >= 3)
            return {"additions_52w": total_add, "deletions_52w": total_del}

        return cast(dict[str, int | None], await self._call(_get))

    async def fetch_workflow_conclusion(
        self, full_name: str
    ) -> dict[str, Any]:
        """Fetch current pass/fail state per workflow (latest completed run each).

        Uses a single REST call with status=completed&per_page=100 to avoid
        iterating a PyGithub PaginatedList (which fetches one HTTP page per
        iteration batch) while holding the semaphore.
        """
        def _get() -> dict[str, Any]:
            token = self._token
            headers: dict[str, str] = {"Accept": "application/vnd.github+json"}
            if token:
                headers["Authorization"] = f"Bearer {token}"
            try:
                resp = requests.get(
                    f"https://api.github.com/repos/{full_name}/actions/runs",
                    headers=headers,
                    params={"status": "completed", "per_page": "100"},
                    timeout=30,
                )
                if resp.status_code != 200:
                    return {"total_workflows": 0, "success_rate": None}
                runs = resp.json().get("workflow_runs", [])
                # Latest completed run per workflow_id
                seen: dict[int, str] = {}
                for run in runs:
                    wf_id = run.get("workflow_id")
                    if wf_id is not None and wf_id not in seen:
                        seen[wf_id] = run.get("conclusion") or ""
                    if len(seen) >= 50:
                        break
                if not seen:
                    return {"total_workflows": 0, "success_rate": None}
                passing = sum(1 for c in seen.values() if c == "success")
                return {
                    "total_workflows": len(seen),
                    "success_rate": round(passing / len(seen) * 100, 1),
                }
            except Exception:
                return {"total_workflows": 0, "success_rate": None}

        return cast(dict[str, Any], await self._call(_get))

    async def fetch_readme_content(self, full_name: str) -> str | None:
        """Fetch and decode the repo's README (first 3000 chars)."""
        def _get() -> str | None:
            repo = self._gh.get_repo(full_name)
            try:
                readme = repo.get_readme()
                return readme.decoded_content.decode("utf-8", errors="replace")[:20000]
            except GithubException:
                return None

        return cast(str | None, await self._call(_get))

    async def fetch_owner_profile(self, owner: str) -> dict[str, Any]:
        """Fetch owner/org profile stats (followers, repos, account age, bio, total stars)."""
        def _get() -> dict[str, Any]:
            try:
                u = self._gh.get_user(owner)
                return {
                    "followers": u.followers,
                    "public_repos": u.public_repos,
                    "created_at": u.created_at.isoformat() if u.created_at else None,
                    "bio": u.bio,
                    "type": u.type,
                }
            except GithubException:
                return {}

        return cast(dict[str, Any], await self._call(_get))
