"""LLM-powered summarization for repository cards."""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from repovore.config import LLMConfig
    from repovore.models import RepoCard

_VALID_VERDICTS = {"adopt", "evaluate", "hold", "avoid"}


def summarize_card(card: RepoCard, config: LLMConfig) -> tuple[str, str, list[str], list[str], list[str]] | None:
    """Call Claude and return (summary, verdict, tags, strengths, concerns), or None if no API key."""
    api_key = os.environ.get(config.token_env_var, "").strip()
    if not api_key:
        return None

    import anthropic  # lazy import — only required when llm.enabled: true

    client = anthropic.Anthropic(api_key=api_key)
    msg = client.messages.create(
        model=config.model,
        max_tokens=config.max_tokens,
        temperature=config.temperature,
        messages=[{"role": "user", "content": _build_prompt(card, config.readme_max_chars)}],
    )
    return _parse_response(msg.content[0].text)


def _build_prompt(card: RepoCard, readme_max_chars: int = 8000) -> str:
    def _pct(v: float | None) -> str:
        return f"{v:.0f}%" if v is not None else "unknown"

    def _val(v: object) -> str:
        return str(v) if v is not None else "unknown"

    top_langs = ", ".join(
        f"{lang} {pct:.0f}%"
        for lang, pct in sorted(card.languages.items(), key=lambda x: -x[1])[:3]
    ) or "unknown"

    last_commit = str(card.last_commit_date)[:10] if card.last_commit_date else "unknown"

    readme_section = (card.readme_excerpt or "(not available)")[:readme_max_chars]

    return f"""You are a senior engineer who has seen too many overhyped GitHub repos and has opinions.
Given the signals below, return ONLY a valid JSON object with two fields:
- "summary": 3-5 sentences. Describe what the project actually does, who's really behind it, and your honest take on whether it delivers. Be specific, sarcastic where warranted, and don't be dry — have a voice. Do NOT cite raw numbers or repeat stats (no star counts, commit counts, percentages, dates, etc.) — those are shown separately. Do not comment on community participation files (CONTRIBUTING, CODE_OF_CONDUCT, etc.). Focus on what the project is, what it's for, and whether it's worth using.
- "verdict": one of exactly "adopt", "evaluate", "hold", or "avoid" (ThoughtWorks Technology Radar style)
- "tags": list of 5-10 short lowercase tags describing the project's domain, tech stack, and use case. Draw from the existing GitHub topics where relevant, but add or replace as needed to best capture what the project actually is.
- "strengths": list of exactly 2-3 short phrases (max 8 words each) naming the most compelling reasons to use this project. No numbers. No fluff.
- "concerns": list of exactly 2-3 short phrases (max 8 words each) naming the most serious red flags. No numbers. No fluff.

No markdown, no explanation, just the JSON object.

## Metrics
name: {card.owner}/{card.name}  health: {card.health_score:.0f}/100  maintenance: {card.maintenance_level}
stars: {card.stars:,}  forks: {card.forks:,}  commits_90d: {_val(card.commit_frequency_90d)}
last_commit: {last_commit}  ci_workflows_passing: {_pct(card.ci_success_rate)}  dependabot_critical: {_val(card.dependabot_open_critical)}
bus_factor_top3: {_pct(card.bus_factor_top3_pct)}  owner_commit_pct: {_pct(card.owner_commit_pct)}
has: readme={card.has_readme} license={card.has_license} ci={card.has_ci}
languages: {top_langs}  license: {card.license or "none"}  archived: {card.archived}
description: {card.description or "(none)"}
github_topics: {", ".join(card.topics) or "none"}

## Owner ({card.owner_type or "unknown"})
followers: {_val(card.owner_followers)}  public_repos: {_val(card.owner_public_repos)}
account_age_days: {_val(card.owner_account_age_days)}

## README (excerpt)
{readme_section}"""


def _parse_response(text: str) -> tuple[str, str, list[str], list[str], list[str]]:
    """Parse Claude's JSON response into (summary, verdict, tags, strengths, concerns)."""
    # Strip markdown code fences if present
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.split("\n")
        stripped = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])

    data = json.loads(stripped)
    summary = str(data["summary"]).strip()
    verdict = str(data["verdict"]).strip().lower()
    tags = [str(t).strip().lower() for t in data.get("tags", [])]
    strengths = [str(s).strip() for s in data.get("strengths", [])]
    concerns = [str(s).strip() for s in data.get("concerns", [])]

    if verdict not in _VALID_VERDICTS:
        raise ValueError(f"Invalid verdict {verdict!r}; must be one of {_VALID_VERDICTS}")

    return summary, verdict, tags, strengths, concerns
