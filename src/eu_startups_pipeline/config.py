from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values


@dataclass(slots=True)
class Paths:
    root: Path
    output_dir: Path
    state_dir: Path
    raw_dir: Path
    log_dir: Path
    db_path: Path
    policy_path: Path


@dataclass(slots=True)
class Settings:
    search_url: str
    min_delay_seconds: float
    max_delay_seconds: float
    request_timeout_seconds: int
    max_retries: int
    disable_funding_filter: bool
    refresh_search_pages: bool
    refresh_company_pages: bool
    refresh_website_pages: bool
    refresh_search_fallbacks: bool
    playwright_headless: bool
    user_agent: str
    enable_runtime_llm: bool
    openai_api_key: str
    openai_model: str
    openai_base_url: str
    funding_policy: dict
    target_roles: dict[str, list[str]]
    website_probe_paths: list[str]
    paths: Paths


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def load_settings(root: Path | None = None) -> Settings:
    root = (root or Path.cwd()).resolve()
    env_path = root / ".env"
    file_env = (
        dotenv_values(env_path) if env_path.exists() else dotenv_values(root / ".env.example")
    )
    env = {**file_env, **os.environ}
    policy_path = root / str(env.get("PIPELINE_POLICY_PATH", "config/pipeline_policy.json"))
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    paths = Paths(
        root=root,
        output_dir=root / str(env.get("OUTPUT_DIR", "runtime/exports")),
        state_dir=root / str(env.get("STATE_DIR", "runtime/state")),
        raw_dir=root / str(env.get("RAW_DIR", "runtime/raw")),
        log_dir=root / str(env.get("LOG_DIR", "runtime/logs")),
        db_path=root / str(env.get("DB_PATH", "runtime/state/pipeline.sqlite3")),
        policy_path=policy_path,
    )
    return Settings(
        search_url=str(env.get("EU_STARTUPS_SEARCH_URL", "")).strip(),
        min_delay_seconds=float(env.get("MIN_DELAY_SECONDS", 4.0)),
        max_delay_seconds=float(env.get("MAX_DELAY_SECONDS", 8.0)),
        request_timeout_seconds=int(env.get("REQUEST_TIMEOUT_SECONDS", 45)),
        max_retries=int(env.get("MAX_RETRIES", 2)),
        disable_funding_filter=_bool(env.get("DISABLE_FUNDING_FILTER")),
        refresh_search_pages=_bool(env.get("REFRESH_SEARCH_PAGES")),
        refresh_company_pages=_bool(env.get("REFRESH_COMPANY_PAGES")),
        refresh_website_pages=_bool(env.get("REFRESH_WEBSITE_PAGES")),
        refresh_search_fallbacks=_bool(env.get("REFRESH_SEARCH_FALLBACKS")),
        playwright_headless=_bool(env.get("PLAYWRIGHT_HEADLESS"), default=True),
        user_agent=str(env.get("USER_AGENT", "Mozilla/5.0")).strip(),
        enable_runtime_llm=_bool(
            env.get("ENABLE_RUNTIME_LLM"), default=_bool(env.get("LLM_ENABLED"))
        ),
        openai_api_key=str(env.get("OPENAI_API_KEY", "")).strip(),
        openai_model=str(env.get("OPENAI_MODEL", "")).strip(),
        openai_base_url=str(env.get("OPENAI_BASE_URL", "https://api.openai.com/v1")).rstrip("/"),
        funding_policy=policy.get("funding_policy", {}),
        target_roles=policy.get("target_roles", {}),
        website_probe_paths=policy.get("website_probe_paths", ["/"]),
        paths=paths,
    )


def ensure_runtime_dirs(settings: Settings) -> None:
    for path in (
        settings.paths.output_dir,
        settings.paths.state_dir,
        settings.paths.raw_dir,
        settings.paths.log_dir,
    ):
        path.mkdir(parents=True, exist_ok=True)
