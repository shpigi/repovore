"""Parse GitHub URLs into (instance_url, owner/repo) tuples."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True)
class ParsedRepo:
    """Parsed GitHub repository reference."""

    instance_url: str
    owner: str
    repo: str

    @property
    def full_name(self) -> str:
        """Owner/repo format used by the GitHub API."""
        return f"{self.owner}/{self.repo}"

    @property
    def slug(self) -> str:
        """Filesystem-safe slug: 'owner/repo' -> 'owner__repo'."""
        return f"{self.owner}__{self.repo}"


def parse_github_url(url: str) -> ParsedRepo:
    """Parse a GitHub URL into owner and repo.

    Handles:
        https://github.com/owner/repo
        https://github.com/owner/repo.git
        https://github.com/owner/repo/tree/main
        https://github.com/owner/repo/issues/42
        https://github.com/owner/repo/pull/10

    Raises:
        ValueError: If the URL cannot be parsed as a GitHub repo URL.
    """
    url = url.strip()
    parsed = urlparse(url)

    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"Invalid URL: {url!r}")

    instance_url = f"{parsed.scheme}://{parsed.netloc}"

    # Strip leading/trailing slashes
    path = parsed.path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]

    segments = [s for s in path.split("/") if s]

    if len(segments) < 2:
        raise ValueError(f"URL must contain owner/repo: {url!r}")

    owner = segments[0]
    repo = segments[1]

    return ParsedRepo(instance_url=instance_url, owner=owner, repo=repo)
