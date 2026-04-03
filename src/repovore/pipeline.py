"""Stage runner / orchestrator for the Repovore pipeline."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from repovore.config import RepovoreConfig
from repovore.db import Database
from repovore.github.client import GitHubClient
from repovore.github.parser import ParsedRepo, parse_github_url
from repovore.models import RepoCard
from repovore.output import load_card, write_card, write_cards_jsonl
from repovore.scoring import compute_health_score, compute_maintenance_level
from repovore.utils import get_logger, setup_logging

STAGES = ["fetch", "enrich", "score", "summarize"]


class Pipeline:
    """Orchestrates the three Repovore processing stages."""

    def __init__(self, config: RepovoreConfig) -> None:
        setup_logging()
        self.logger = get_logger(__name__)
        self.config = config

        self.data_dir = Path(config.output.data_dir)
        db_path = self.data_dir / "repovore.db"
        self.db = Database(db_path)
        self.client = GitHubClient(config.github)

    def run(
        self,
        urls: list[str],
        stages: list[str] | None = None,
        from_stage: str | None = None,
        force: bool = False,
    ) -> None:
        """Run the pipeline on the given URLs."""
        stages_to_run = self._resolve_stages(stages, from_stage)

        config_hash = self._hash_config()
        run_id = self.db.start_run(config_hash, stages_to_run)
        self.logger.info("Pipeline run %d started (stages: %s)", run_id, stages_to_run)

        parsed_repos = self._register_urls(urls)

        for stage in stages_to_run:
            t0 = time.time()
            self.logger.info("Stage [%s] starting", stage)
            try:
                self._run_stage(stage, parsed_repos, force=force)
                elapsed = time.time() - t0
                self.logger.info("Stage [%s] completed in %.2fs", stage, elapsed)
            except Exception as exc:
                elapsed = time.time() - t0
                self.logger.error(
                    "Stage [%s] failed after %.2fs: %s",
                    stage, elapsed, exc, exc_info=True,
                )

        self.db.complete_run(run_id)
        self.logger.info("Pipeline run %d finished", run_id)

    def _register_urls(self, urls: list[str]) -> list[ParsedRepo]:
        """Parse URLs and register them in the database."""
        parsed = []
        for url in urls:
            try:
                repo = parse_github_url(url)
                self.db.get_or_create_repo(repo.full_name, url=url)
                parsed.append(repo)
            except ValueError as exc:
                self.logger.warning("Skipping invalid URL %r: %s", url, exc)
        self.logger.info("Registered %d repos from %d URLs", len(parsed), len(urls))
        return parsed

    def _resolve_stages(
        self,
        stages: list[str] | None,
        from_stage: str | None,
    ) -> list[str]:
        if stages is not None and from_stage is not None:
            raise ValueError(
                "Provide at most one of 'stages' or 'from_stage', not both."
            )

        if stages is not None:
            for s in stages:
                if s not in STAGES:
                    raise ValueError(
                        f"Unknown stage {s!r}. Valid stages: {STAGES}"
                    )
            return list(stages)

        if from_stage is not None:
            if from_stage not in STAGES:
                raise ValueError(
                    f"Unknown stage {from_stage!r}. Valid stages: {STAGES}"
                )
            return STAGES[STAGES.index(from_stage):]

        return list(STAGES)

    def _hash_config(self) -> str:
        config_json = self.config.model_dump_json()
        return hashlib.sha256(config_json.encode()).hexdigest()

    def _run_stage(
        self, stage: str, parsed_repos: list[ParsedRepo], force: bool = False
    ) -> None:
        dispatch = {
            "fetch": self._run_fetch,
            "enrich": self._run_enrich,
            "score": self._run_score,
            "summarize": self._run_summarize,
        }
        dispatch[stage](parsed_repos, force)

    def _run_fetch(
        self, parsed_repos: list[ParsedRepo], force: bool = False
    ) -> None:
        """Fetch core repo metadata from GitHub."""
        repos_needing = self.db.get_repos_needing_stage("fetch", force=force)
        paths_needing = {r["project_path"] for r in repos_needing}
        to_fetch = [r for r in parsed_repos if r.full_name in paths_needing]

        if not to_fetch:
            self.logger.info("No repos needing fetch — skipping")
            return

        self.logger.info("Fetching metadata for %d repos", len(to_fetch))

        async def _fetch_all() -> None:
            self.client.reset_async_primitives()
            tasks = [self._fetch_one(repo) for repo in to_fetch]
            await asyncio.gather(*tasks, return_exceptions=True)

        asyncio.run(_fetch_all())

    async def _fetch_one(self, repo: ParsedRepo) -> None:
        self.db.update_stage_status(repo.full_name, "fetch", "running")
        try:
            data = await self.client.fetch_repo(repo.full_name)
            raw_dir = self.data_dir / "raw"
            raw_dir.mkdir(parents=True, exist_ok=True)
            out_path = raw_dir / f"{repo.slug}.json"
            out_path.write_text(
                json.dumps(data, indent=2, default=str), encoding="utf-8"
            )
            self.db.update_stage_status(repo.full_name, "fetch", "done")
            self.logger.info("Fetched: %s", repo.full_name)
        except Exception as exc:
            self.db.update_stage_status(
                repo.full_name, "fetch", "failed", error=str(exc)
            )
            self.logger.error("Failed to fetch %s: %s", repo.full_name, exc)

    def _run_enrich(
        self, parsed_repos: list[ParsedRepo], force: bool = False
    ) -> None:
        """Enrich fetched repos with additional API data."""
        repos_needing = self.db.get_repos_needing_stage("enrich", force=force)
        paths_needing = {r["project_path"] for r in repos_needing}
        to_enrich = [r for r in parsed_repos if r.full_name in paths_needing]

        if not to_enrich:
            self.logger.info("No repos needing enrichment — skipping")
            return

        self.logger.info("Enriching %d repos", len(to_enrich))

        # Warm GitHub stats endpoints so they compute in the background while
        # we fetch other data.  By the time _enrich_one reaches _stats_get the
        # results are usually ready, avoiding most 202 retries.
        self.client.warm_stats([r.full_name for r in to_enrich])

        async def _enrich_all() -> None:
            self.client.reset_async_primitives()
            tasks = [self._enrich_one(repo) for repo in to_enrich]
            await asyncio.gather(*tasks, return_exceptions=True)

        asyncio.run(_enrich_all())

    async def _enrich_one(self, repo: ParsedRepo) -> None:
        self.db.update_stage_status(repo.full_name, "enrich", "running")
        try:
            labels = [
                "languages", "contributors", "commits_90d", "prs",
                "releases", "root_contents", "issues",
                "community_profile", "dependabot", "participation", "code_frequency", "workflow",
                "readme_content", "owner_profile",
            ]
            fetches = [
                self.client.fetch_languages(repo.full_name),
                self.client.fetch_contributors(repo.full_name),
                self.client.fetch_recent_commits(repo.full_name, since_days=90),
                self.client.fetch_pull_requests(repo.full_name),
                self.client.fetch_releases(repo.full_name),
                self.client.fetch_root_contents(repo.full_name),
                self.client.fetch_issues_stats(repo.full_name),
                self.client.fetch_community_profile(repo.full_name),
                self.client.fetch_dependabot_alerts(repo.full_name),
                self.client.fetch_participation_stats(repo.full_name),
                self.client.fetch_code_frequency(repo.full_name),
                self.client.fetch_workflow_conclusion(repo.full_name),
                self.client.fetch_readme_content(repo.full_name),
                self.client.fetch_owner_profile(repo.owner),
            ]

            results = await asyncio.gather(
                *fetches,
                return_exceptions=True,
            )

            # Load raw data
            raw_path = self.data_dir / "raw" / f"{repo.slug}.json"
            raw = json.loads(raw_path.read_text(encoding="utf-8"))

            enriched: dict[str, Any] = {"raw": raw}
            for label, result in zip(labels, results):
                if isinstance(result, Exception):
                    self.logger.warning(
                        "Enrichment %s failed for %s: %s",
                        label, repo.full_name, result,
                    )
                    enriched[label] = None
                else:
                    enriched[label] = result

            enriched_dir = self.data_dir / "enriched"
            enriched_dir.mkdir(parents=True, exist_ok=True)
            out_path = enriched_dir / f"{repo.slug}.json"
            out_path.write_text(
                json.dumps(enriched, indent=2, default=str), encoding="utf-8"
            )
            self.db.update_stage_status(repo.full_name, "enrich", "done")
            self.logger.info("Enriched: %s", repo.full_name)
        except Exception as exc:
            self.db.update_stage_status(
                repo.full_name, "enrich", "failed", error=str(exc)
            )
            self.logger.error("Failed to enrich %s: %s", repo.full_name, exc)

    def _run_score(
        self, parsed_repos: list[ParsedRepo], force: bool = False
    ) -> None:
        """Score enriched repos and produce cards."""
        repos_needing = self.db.get_repos_needing_stage("score", force=force)
        paths_needing = {r["project_path"] for r in repos_needing}
        to_score = [r for r in parsed_repos if r.full_name in paths_needing]

        if not to_score:
            self.logger.info("No repos needing scoring — skipping")
            return

        self.logger.info("Scoring %d repos", len(to_score))
        cards: list[RepoCard] = []

        for repo in to_score:
            self.db.update_stage_status(repo.full_name, "score", "running")
            try:
                card = self._score_one(repo)
                cards_dir = self.data_dir / "cards"
                write_card(card, cards_dir, db=self.db)
                cards.append(card)
                self.db.update_stage_status(repo.full_name, "score", "done")
                self.logger.info(
                    "Scored: %s (health=%.1f, level=%s)",
                    repo.full_name, card.health_score, card.maintenance_level,
                )
            except Exception as exc:
                self.db.update_stage_status(
                    repo.full_name, "score", "failed", error=str(exc)
                )
                self.logger.error(
                    "Failed to score %s: %s", repo.full_name, exc
                )

        if cards:
            write_cards_jsonl(cards, self.data_dir / "cards.jsonl")

    def _score_one(self, repo: ParsedRepo) -> RepoCard:
        """Build a RepoCard from enriched data."""
        enriched_path = self.data_dir / "enriched" / f"{repo.slug}.json"
        enriched = json.loads(enriched_path.read_text(encoding="utf-8"))

        raw = enriched["raw"]
        languages = enriched.get("languages") or {}
        contributors = enriched.get("contributors") or []
        commits_90d = enriched.get("commits_90d")
        prs = enriched.get("prs") or {"open": 0, "closed": 0}
        releases = enriched.get("releases") or []
        root_contents = enriched.get("root_contents") or []
        issues = enriched.get("issues") or {"open": 0, "closed": 0}
        community_profile = enriched.get("community_profile") or {}
        dependabot = enriched.get("dependabot") or {}
        participation = enriched.get("participation") or {}
        code_frequency = enriched.get("code_frequency") or {}
        workflow = enriched.get("workflow") or {}
        readme_content: str | None = enriched.get("readme_content")
        owner_profile = enriched.get("owner_profile") or {}

        # Parse dates
        last_commit_date = self._parse_datetime(raw.get("pushed_at"))
        last_release_date = None
        if releases:
            last_release_date = self._parse_datetime(
                releases[0].get("published_at")
            )

        # File existence checks from root contents
        root_names = {item["name"].lower() for item in root_contents}
        has_readme = any(n.startswith("readme") for n in root_names)
        has_license = any(
            n.startswith("license") or n.startswith("licence") for n in root_names
        )
        has_ci = ".github" in root_names  # GitHub Actions in .github/
        has_contributing = any(
            n.startswith("contributing") for n in root_names
        )
        has_code_of_conduct = any(
            n.startswith("code_of_conduct") or n.startswith("code-of-conduct")
            for n in root_names
        )
        # Community profile provides authoritative file presence — override root checks
        cp_files = community_profile.get("files") or {}
        if cp_files.get("contributing"):
            has_contributing = True
        if cp_files.get("code_of_conduct"):
            has_code_of_conduct = True
        has_funding = "funding" in cp_files

        # GitHub-specific enrichment values
        community_health_pct: int | None = community_profile.get("health_percentage")
        dependabot_open_total: int | None = dependabot.get("total") if dependabot else None
        dependabot_open_critical: int | None = dependabot.get("critical") if dependabot else None
        dependabot_open_high: int | None = dependabot.get("high") if dependabot else None
        owner_commit_pct: float | None = participation.get("owner_pct")
        code_churn_additions: int | None = code_frequency.get("additions_52w")
        code_churn_deletions: int | None = code_frequency.get("deletions_52w")
        ci_success_rate: float | None = workflow.get("success_rate")

        # Bus factor + top contributors
        bus_factor_top3_pct: float | None = None
        top_contributors: list[dict[str, str | float]] = []
        if contributors:
            sorted_contribs = sorted(
                contributors, key=lambda c: c.get("commits", 0), reverse=True
            )
            total = sum(c.get("commits", 0) for c in sorted_contribs)
            if total > 0:
                top3 = sum(c.get("commits", 0) for c in sorted_contribs[:3])
                bus_factor_top3_pct = (top3 / total) * 100
                top_contributors = [
                    {"login": c["login"], "pct": round(c["commits"] / total * 100, 1)}
                    for c in sorted_contribs[:5]
                    if c.get("commits", 0) > 0
                ]

        # Owner profile
        owner_account_age_days: int | None = None
        now = datetime.now(UTC)
        if owner_profile.get("created_at"):
            owner_created = self._parse_datetime(owner_profile["created_at"])
            if owner_created:
                owner_account_age_days = (now - owner_created).days

        # Days since last release + latest tag
        days_since_last_release: int | None = None
        latest_release_tag: str | None = releases[0].get("tag_name") if releases else None
        if last_release_date:
            days_since_last_release = (now - last_release_date).days

        # Repo age + star velocity
        repo_age_days: int | None = None
        stars_per_year: float | None = None
        created_at = self._parse_datetime(raw.get("created_at"))
        if created_at:
            repo_age_days = max((now - created_at).days, 1)
            stars_per_year = raw.get("stargazers_count", 0) / (repo_age_days / 365)

        maintenance_level = compute_maintenance_level(
            last_commit_date=last_commit_date,
            last_release_date=last_release_date,
            open_prs=prs.get("open", 0),
            archived=raw.get("archived", False),
        )

        health_score = compute_health_score(
            config=self.config.scoring,
            maintenance_level=maintenance_level,
            last_commit_date=last_commit_date,
            commit_frequency_90d=commits_90d,
            last_release_date=last_release_date,
            stars=raw.get("stargazers_count", 0),
            forks=raw.get("forks_count", 0),
            contributor_count=len(contributors) if contributors else None,
            issue_response_time_median_hours=None,
            has_readme=has_readme,
            has_license=has_license,
            has_ci=has_ci,
            has_contributing=has_contributing,
            bus_factor_top3_pct=bus_factor_top3_pct,
            community_health_pct=community_health_pct,
            dependabot_open_critical=dependabot_open_critical,
            dependabot_open_high=dependabot_open_high,
            ci_success_rate=ci_success_rate,
            has_code_of_conduct=has_code_of_conduct,
        )

        # Preserve existing LLM fields from a previous card if available
        existing_card: RepoCard | None = None
        card_path = self.data_dir / "cards" / f"{repo.slug}.json"
        if card_path.exists():
            try:
                existing_card = load_card(card_path)
            except Exception:
                pass

        return RepoCard(
            name=repo.repo,
            owner=repo.owner,
            description=raw.get("description"),
            url=raw.get("html_url", f"https://github.com/{repo.full_name}"),
            stars=raw.get("stargazers_count", 0),
            forks=raw.get("forks_count", 0),
            languages=languages,
            license=self._extract_license(raw),
            topics=raw.get("topics", []),
            last_commit_date=last_commit_date,
            commit_frequency_90d=commits_90d,
            open_issues=issues.get("open", 0),
            closed_issues=issues.get("closed", 0),
            open_prs=prs.get("open", 0),
            closed_prs=prs.get("closed", 0),
            last_release_date=last_release_date,
            has_readme=has_readme,
            has_license=has_license,
            has_ci=has_ci,
            has_contributing=has_contributing,
            has_code_of_conduct=has_code_of_conduct,
            has_funding=has_funding,
            issue_response_time_median_hours=None,
            days_since_last_release=days_since_last_release,
            latest_release_tag=latest_release_tag,
            bus_factor_top3_pct=bus_factor_top3_pct,
            top_contributors=top_contributors,
            repo_age_days=repo_age_days,
            stars_per_year=stars_per_year,
            archived=raw.get("archived", False),
            community_health_pct=community_health_pct,
            dependabot_open_total=dependabot_open_total,
            dependabot_open_critical=dependabot_open_critical,
            dependabot_open_high=dependabot_open_high,
            owner_commit_pct=owner_commit_pct,
            code_churn_additions_52w=code_churn_additions,
            code_churn_deletions_52w=code_churn_deletions,
            ci_success_rate=ci_success_rate,
            owner_followers=owner_profile.get("followers"),
            owner_public_repos=owner_profile.get("public_repos"),
            owner_account_age_days=owner_account_age_days,
            owner_type=owner_profile.get("type"),
            readme_excerpt=readme_content,
            maintenance_level=maintenance_level,
            health_score=health_score,
            fetched_at=datetime.now(UTC),
            # Preserve LLM fields from previous card
            summary=existing_card.summary if existing_card else None,
            verdict=existing_card.verdict if existing_card else None,
            tags=existing_card.tags if existing_card else [],
            strengths=existing_card.strengths if existing_card else [],
            concerns=existing_card.concerns if existing_card else [],
        )

    def _run_summarize(
        self, parsed_repos: list[ParsedRepo], force: bool = False
    ) -> None:
        """Run LLM summarization on scored repos."""
        if not self.config.llm.enabled:
            self.logger.info("LLM stage disabled in config — skipping")
            return

        import os
        api_key = os.environ.get(self.config.llm.token_env_var, "").strip()
        if not api_key:
            self.logger.warning(
                "LLM stage enabled but %s not set — skipping",
                self.config.llm.token_env_var,
            )
            return

        repos_needing = self.db.get_repos_needing_stage("summarize", force=force)
        paths_needing = {r["project_path"] for r in repos_needing}
        to_summarize = [r for r in parsed_repos if r.full_name in paths_needing]

        if not to_summarize:
            self.logger.info("No repos needing summarization — skipping")
            return

        self.logger.info("Summarizing %d repos", len(to_summarize))

        async def _summarize_all() -> None:
            async def _one(repo: ParsedRepo) -> None:
                self.db.update_stage_status(repo.full_name, "summarize", "running")
                try:
                    await asyncio.to_thread(self._summarize_one, repo)
                    self.db.update_stage_status(repo.full_name, "summarize", "done")
                except Exception as exc:
                    self.db.update_stage_status(
                        repo.full_name, "summarize", "failed", error=str(exc)
                    )
                    self.logger.error("Failed to summarize %s: %s", repo.full_name, exc)

            await asyncio.gather(*[_one(repo) for repo in to_summarize])

        asyncio.run(_summarize_all())

    def _summarize_one(self, repo: ParsedRepo) -> None:
        from repovore import llm as llm_module

        cards_dir = self.data_dir / "cards"
        card_path = cards_dir / f"{repo.slug}.json"
        if not card_path.exists():
            raise FileNotFoundError(
                f"Card not found for {repo.full_name}; run 'score' first"
            )

        card = load_card(card_path)
        result = llm_module.summarize_card(card, self.config.llm)
        if result is None:
            raise RuntimeError("summarize_card returned None — API key missing?")

        card.summary, card.verdict, card.tags, card.strengths, card.concerns = result
        write_card(card, cards_dir, db=self.db)
        self.logger.info(
            "Summarized: %s (verdict=%s)", repo.full_name, card.verdict
        )

    @staticmethod
    def _parse_datetime(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            return None

    @staticmethod
    def _extract_license(raw: dict[str, Any]) -> str | None:
        lic = raw.get("license")
        if isinstance(lic, dict):
            return lic.get("name") or lic.get("key")
        if isinstance(lic, str):
            return lic
        return None
