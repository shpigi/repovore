"""Tests for scoring functions."""

from datetime import UTC, datetime, timedelta

from repovore.config import ScoringConfig
from repovore.scoring import compute_health_score, compute_maintenance_level


class TestMaintenanceLevel:
    def test_archived_is_abandoned(self) -> None:
        assert compute_maintenance_level(
            last_commit_date=datetime.now(UTC),
            last_release_date=datetime.now(UTC),
            open_prs=10,
            archived=True,
        ) == "abandoned"

    def test_no_commits_is_abandoned(self) -> None:
        assert compute_maintenance_level(
            last_commit_date=None,
            last_release_date=None,
            open_prs=0,
            archived=False,
        ) == "abandoned"

    def test_recent_commit_with_release_is_active(self) -> None:
        now = datetime.now(UTC)
        assert compute_maintenance_level(
            last_commit_date=now - timedelta(days=5),
            last_release_date=now - timedelta(days=60),
            open_prs=0,
            archived=False,
        ) == "active"

    def test_recent_commit_with_open_prs_is_active(self) -> None:
        now = datetime.now(UTC)
        assert compute_maintenance_level(
            last_commit_date=now - timedelta(days=10),
            last_release_date=None,
            open_prs=3,
            archived=False,
        ) == "active"

    def test_recent_commit_no_release_no_prs_is_maintained(self) -> None:
        now = datetime.now(UTC)
        assert compute_maintenance_level(
            last_commit_date=now - timedelta(days=15),
            last_release_date=None,
            open_prs=0,
            archived=False,
        ) == "maintained"

    def test_60d_commit_is_maintained(self) -> None:
        now = datetime.now(UTC)
        assert compute_maintenance_level(
            last_commit_date=now - timedelta(days=60),
            last_release_date=None,
            open_prs=0,
            archived=False,
        ) == "maintained"

    def test_200d_commit_is_minimal(self) -> None:
        now = datetime.now(UTC)
        assert compute_maintenance_level(
            last_commit_date=now - timedelta(days=200),
            last_release_date=None,
            open_prs=0,
            archived=False,
        ) == "minimal"

    def test_400d_commit_is_abandoned(self) -> None:
        now = datetime.now(UTC)
        assert compute_maintenance_level(
            last_commit_date=now - timedelta(days=400),
            last_release_date=None,
            open_prs=0,
            archived=False,
        ) == "abandoned"


class TestHealthScore:
    def _default_config(self) -> ScoringConfig:
        return ScoringConfig()

    def test_score_in_range(self) -> None:
        score = compute_health_score(
            config=self._default_config(),
            maintenance_level="active",
            last_commit_date=datetime.now(UTC),
            commit_frequency_90d=50,
            last_release_date=datetime.now(UTC),
            stars=100,
            forks=20,
            contributor_count=10,
            issue_response_time_median_hours=48.0,
            has_readme=True,
            has_license=True,
            has_ci=True,
            has_contributing=True,
            bus_factor_top3_pct=60.0,
        )
        assert 0 <= score <= 100

    def test_abandoned_scores_low(self) -> None:
        score = compute_health_score(
            config=self._default_config(),
            maintenance_level="abandoned",
            last_commit_date=None,
            commit_frequency_90d=None,
            last_release_date=None,
            stars=0,
            forks=0,
            contributor_count=None,
            issue_response_time_median_hours=None,
            has_readme=False,
            has_license=False,
            has_ci=False,
            has_contributing=False,
            bus_factor_top3_pct=None,
        )
        assert score < 10

    def test_healthy_project_scores_high(self) -> None:
        now = datetime.now(UTC)
        score = compute_health_score(
            config=self._default_config(),
            maintenance_level="active",
            last_commit_date=now - timedelta(days=1),
            commit_frequency_90d=200,
            last_release_date=now - timedelta(days=10),
            stars=1000,
            forks=100,
            contributor_count=25,
            issue_response_time_median_hours=12.0,
            has_readme=True,
            has_license=True,
            has_ci=True,
            has_contributing=True,
            bus_factor_top3_pct=40.0,
        )
        assert score >= 80
