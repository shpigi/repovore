"""Pydantic v2 configuration models for Repovore."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field


class GitHubConfig(BaseModel):
    token_env_var: str = "GITHUB_TOKEN"
    max_concurrent: int = 10
    requests_per_second: float = 10.0  # GitHub: 5000 req/hr authenticated
    max_retries: int = 3
    base_backoff: float = 2.0
    max_backoff: float = 60.0
    backoff_jitter: bool = True


class ScoringConfig(BaseModel):
    weight_activity: float = 0.3
    weight_community: float = 0.2
    weight_quality: float = 0.3
    weight_maintenance: float = 0.2


class OutputConfig(BaseModel):
    format: Literal["json", "jsonl"] = "json"
    data_dir: str = "data"


class LLMConfig(BaseModel):
    enabled: bool = False
    model: str = "claude-haiku-4-5-20251001"
    token_env_var: str = "ANTHROPIC_API_KEY"
    max_tokens: int = 400
    temperature: float = 0.2
    readme_max_chars: int = 8000


class RepovoreConfig(BaseModel):
    github: GitHubConfig = Field(default_factory=GitHubConfig)
    scoring: ScoringConfig = Field(default_factory=ScoringConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)


def load_config(path: Path | None = None) -> RepovoreConfig:
    """Load configuration from a YAML file with environment variable overrides.

    Resolution order:
    1. Defaults baked into the Pydantic models.
    2. YAML file at *path* (or ``REPOVORE_CONFIG`` env var, or ``config/default.yml``).
    3. Environment variable overrides applied after loading.
    """
    if path is None:
        env_path = os.environ.get("REPOVORE_CONFIG")
        if env_path:
            path = Path(env_path)
        else:
            path = Path("config/default.yml")

    raw: dict = {}
    if path.exists():
        with path.open("r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}

    config = RepovoreConfig.model_validate(raw)

    data_dir = os.environ.get("REPOVORE_DATA_DIR", "").strip()
    if data_dir:
        config.output.data_dir = data_dir

    return config
