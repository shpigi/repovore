"""Tests for Pydantic card models."""

import json
from datetime import UTC, datetime

from repovore.models import RepoCard


class TestRepoCard:
    def test_minimal_card(self) -> None:
        card = RepoCard(
            name="myproject",
            owner="myowner",
            url="https://github.com/myowner/myproject",
        )
        assert card.name == "myproject"
        assert card.stars == 0
        assert card.maintenance_level == "abandoned"
        assert card.health_score == 0.0
        assert card.card_version == "1.1"

    def test_full_card(self) -> None:
        card = RepoCard(
            name="repo",
            owner="org",
            description="A great project",
            url="https://github.com/org/repo",
            stars=150,
            forks=30,
            languages={"Python": 80.0, "Shell": 20.0},
            license="MIT",
            topics=["python", "cli"],
            last_commit_date=datetime(2025, 1, 15, tzinfo=UTC),
            commit_frequency_90d=42,
            open_issues=5,
            closed_issues=100,
            open_prs=3,
            closed_prs=50,
            has_readme=True,
            has_license=True,
            has_ci=True,
            has_contributing=False,
            bus_factor_top3_pct=75.0,
            archived=False,
            maintenance_level="active",
            health_score=82.5,
        )
        assert card.stars == 150
        assert card.languages["Python"] == 80.0
        assert card.maintenance_level == "active"

    def test_json_roundtrip(self) -> None:
        card = RepoCard(
            name="test",
            owner="ns",
            url="https://github.com/ns/test",
            stars=10,
            maintenance_level="maintained",
            health_score=55.0,
        )
        json_str = card.model_dump_json()
        loaded = RepoCard.model_validate(json.loads(json_str))
        assert loaded.name == card.name
        assert loaded.stars == card.stars
        assert loaded.health_score == card.health_score

    def test_optional_fields_none(self) -> None:
        card = RepoCard(
            name="test",
            owner="ns",
            url="https://github.com/ns/test",
        )
        assert card.description is None
        assert card.license is None
        assert card.last_commit_date is None
        assert card.commit_frequency_90d is None
        assert card.bus_factor_top3_pct is None
