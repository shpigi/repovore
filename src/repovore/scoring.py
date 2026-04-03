"""Pure scoring functions for repository quality assessment."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from repovore.config import ScoringConfig


def compute_maintenance_level(
    last_commit_date: datetime | None,
    last_release_date: datetime | None,
    open_prs: int,
    archived: bool,
) -> Literal["active", "maintained", "minimal", "abandoned"]:
    """Classify maintenance level based on activity signals."""
    if archived:
        return "abandoned"

    if last_commit_date is None:
        return "abandoned"

    now = datetime.now(UTC)
    days_since_commit = (now - last_commit_date).days

    if days_since_commit <= 30:
        has_recent_release = (
            last_release_date is not None
            and (now - last_release_date).days <= 180
        )
        has_pr_activity = open_prs > 0
        if has_recent_release or has_pr_activity:
            return "active"
        return "maintained"

    if days_since_commit <= 90:
        return "maintained"

    if days_since_commit <= 365:
        return "minimal"

    return "abandoned"


def _activity_score(
    last_commit_date: datetime | None,
    commit_frequency_90d: int | None,
    last_release_date: datetime | None,
) -> float:
    """Score 0-100 based on development activity."""
    now = datetime.now(UTC)
    score = 0.0

    # Commit recency (0-40)
    if last_commit_date is not None:
        days = (now - last_commit_date).days
        if days <= 7:
            score += 40
        elif days <= 30:
            score += 35
        elif days <= 90:
            score += 25
        elif days <= 180:
            score += 15
        elif days <= 365:
            score += 5

    # Commit frequency (0-35)
    if commit_frequency_90d is not None:
        if commit_frequency_90d >= 100:
            score += 35
        elif commit_frequency_90d >= 50:
            score += 28
        elif commit_frequency_90d >= 20:
            score += 20
        elif commit_frequency_90d >= 5:
            score += 12
        elif commit_frequency_90d >= 1:
            score += 5

    # Release cadence (0-25)
    if last_release_date is not None:
        days = (now - last_release_date).days
        if days <= 30:
            score += 25
        elif days <= 90:
            score += 20
        elif days <= 180:
            score += 12
        elif days <= 365:
            score += 5

    return min(score, 100.0)


def _community_score(
    stars: int,
    forks: int,
    contributor_count: int | None,
    issue_response_time_median_hours: float | None,
) -> float:
    """Score 0-100 based on community engagement."""
    score = 0.0

    # Stars — log-scaled (0-30)
    if stars > 0:
        score += min(30.0, math.log10(stars) * 10)

    # Forks — log-scaled (0-25)
    if forks > 0:
        score += min(25.0, math.log10(forks) * 10)

    # Contributors (0-25)
    if contributor_count is not None:
        if contributor_count >= 20:
            score += 25
        elif contributor_count >= 10:
            score += 20
        elif contributor_count >= 5:
            score += 15
        elif contributor_count >= 2:
            score += 8

    # Issue response time (0-20)
    if issue_response_time_median_hours is not None:
        if issue_response_time_median_hours <= 24:
            score += 20
        elif issue_response_time_median_hours <= 72:
            score += 15
        elif issue_response_time_median_hours <= 168:
            score += 8
        elif issue_response_time_median_hours <= 720:
            score += 3

    return min(score, 100.0)


def _quality_score(
    has_readme: bool,
    has_license: bool,
    has_ci: bool,
    has_contributing: bool,
    bus_factor_top3_pct: float | None,
    community_health_pct: int | None = None,
    dependabot_open_critical: int | None = None,
    dependabot_open_high: int | None = None,
    ci_success_rate: float | None = None,
    has_code_of_conduct: bool = False,
) -> float:
    """Score 0-100 based on project quality signals."""
    score = 0.0

    # If GitHub's community health percentage is available, use it as the base
    # (it already encodes README, LICENSE, CONTRIBUTING, etc.)
    if community_health_pct is not None:
        # community_health_pct is 0-100; scale to 0-60 to leave room for other signals
        score += community_health_pct * 0.60
    else:
        # Presence checks (fallback when community profile unavailable)
        if has_readme:
            score += 15
        if has_license:
            score += 15
        if has_ci:
            score += 15
        if has_contributing:
            score += 10
        if has_code_of_conduct:
            score += 5

    # CI success rate (0-15) — rewards actually green CI, not just having CI
    if ci_success_rate is not None:
        if ci_success_rate >= 90:
            score += 15
        elif ci_success_rate >= 70:
            score += 10
        elif ci_success_rate >= 50:
            score += 5

    # Dependabot penalty — open vulnerabilities reduce quality
    if dependabot_open_critical is not None and dependabot_open_critical > 0:
        score -= min(15, dependabot_open_critical * 5)
    if dependabot_open_high is not None and dependabot_open_high > 0:
        score -= min(10, dependabot_open_high * 2)

    # Bus factor — lower concentration is better (0-25)
    if bus_factor_top3_pct is not None:
        if bus_factor_top3_pct <= 50:
            score += 25
        elif bus_factor_top3_pct <= 70:
            score += 18
        elif bus_factor_top3_pct <= 85:
            score += 10
        elif bus_factor_top3_pct <= 95:
            score += 5

    return max(0.0, min(score, 100.0))


def _maintenance_score(
    maintenance_level: Literal["active", "maintained", "minimal", "abandoned"],
) -> float:
    """Score 0-100 based on maintenance level."""
    return {
        "active": 100.0,
        "maintained": 70.0,
        "minimal": 30.0,
        "abandoned": 0.0,
    }[maintenance_level]


def compute_health_score(
    *,
    config: ScoringConfig,
    maintenance_level: Literal["active", "maintained", "minimal", "abandoned"],
    last_commit_date: datetime | None,
    commit_frequency_90d: int | None,
    last_release_date: datetime | None,
    stars: int,
    forks: int,
    contributor_count: int | None,
    issue_response_time_median_hours: float | None,
    has_readme: bool,
    has_license: bool,
    has_ci: bool,
    has_contributing: bool,
    bus_factor_top3_pct: float | None,
    community_health_pct: int | None = None,
    dependabot_open_critical: int | None = None,
    dependabot_open_high: int | None = None,
    ci_success_rate: float | None = None,
    has_code_of_conduct: bool = False,
) -> float:
    """Compute weighted composite health score (0-100)."""
    activity = _activity_score(last_commit_date, commit_frequency_90d, last_release_date)
    community = _community_score(stars, forks, contributor_count, issue_response_time_median_hours)
    quality = _quality_score(
        has_readme, has_license, has_ci, has_contributing, bus_factor_top3_pct,
        community_health_pct=community_health_pct,
        dependabot_open_critical=dependabot_open_critical,
        dependabot_open_high=dependabot_open_high,
        ci_success_rate=ci_success_rate,
        has_code_of_conduct=has_code_of_conduct,
    )
    maintenance = _maintenance_score(maintenance_level)

    weighted = (
        config.weight_activity * activity
        + config.weight_community * community
        + config.weight_quality * quality
        + config.weight_maintenance * maintenance
    )

    return round(min(weighted, 100.0), 1)
