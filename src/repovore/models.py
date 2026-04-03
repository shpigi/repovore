"""Pydantic v2 models for repository quality cards."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class RepoCard(BaseModel):
    """Structured quality assessment card for a GitHub repository."""

    # Core
    name: str
    owner: str
    description: str | None = None
    url: str
    stars: int = 0
    forks: int = 0
    languages: dict[str, float] = Field(default_factory=dict)
    license: str | None = None
    topics: list[str] = Field(default_factory=list)

    # Activity
    last_commit_date: datetime | None = None
    commit_frequency_90d: int | None = None
    open_issues: int = 0
    closed_issues: int = 0
    open_prs: int = 0
    closed_prs: int = 0
    last_release_date: datetime | None = None

    # Quality signals
    has_readme: bool = False
    has_license: bool = False
    has_ci: bool = False  # GitHub Actions workflow files
    has_contributing: bool = False
    has_code_of_conduct: bool = False
    has_funding: bool = False
    issue_response_time_median_hours: float | None = None
    days_since_last_release: int | None = None
    bus_factor_top3_pct: float | None = None
    archived: bool = False

    # GitHub-specific enrichments
    community_health_pct: int | None = None
    dependabot_open_total: int | None = None
    dependabot_open_critical: int | None = None
    dependabot_open_high: int | None = None
    owner_commit_pct: float | None = None  # % of commits by repo owner (52w)
    code_churn_additions_52w: int | None = None
    code_churn_deletions_52w: int | None = None
    ci_success_rate: float | None = None  # % of recent workflow runs passing

    # Owner profile
    owner_followers: int | None = None
    owner_public_repos: int | None = None
    owner_account_age_days: int | None = None
    owner_type: str | None = None  # "User" or "Organization"

    # Derived from raw
    repo_age_days: int | None = None
    stars_per_year: float | None = None
    latest_release_tag: str | None = None
    top_contributors: list[dict[str, str | float]] = Field(default_factory=list)

    # README
    readme_excerpt: str | None = None  # first 3000 chars

    # Computed
    maintenance_level: Literal["active", "maintained", "minimal", "abandoned"] = "abandoned"
    health_score: float = 0.0

    # LLM-generated
    summary: str | None = None
    verdict: Literal["adopt", "evaluate", "hold", "avoid"] | None = None
    tags: list[str] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    concerns: list[str] = Field(default_factory=list)

    # Metadata
    fetched_at: datetime = Field(default_factory=datetime.now)
    card_version: str = "1.1"
