"""Configuration loading: secrets from .env, settings from config.yaml."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

# Per-provider default models (used when config.yaml doesn't set them).
DEFAULT_MODELS = {
    "anthropic": {"extract": "claude-opus-4-8", "enrich": "claude-opus-4-8", "score": "claude-opus-4-8"},
    "openai": {"extract": "gpt-4o-mini", "enrich": "gpt-4o-mini", "score": "gpt-4o"},
}


@dataclass
class GoogleSheetConfig:
    enabled: bool = False
    sheet_id: str = ""
    worksheet: str = "Jobs"
    service_account_file: str = "./service-account.json"


@dataclass
class Config:
    # secrets (.env)
    llm_provider: str
    openai_api_key: str
    anthropic_api_key: str
    gmail_address: str
    gmail_app_password: str
    telegram_bot_token: str
    telegram_chat_id: str
    # settings (config.yaml)
    poll_interval_seconds: int
    lookback_days: int
    first_run_lookback_days: int
    known_senders: dict
    job_subject_keywords: list
    skip_link_domains: list
    score_threshold: int
    database_path: str
    resume_path: str
    models: dict
    telegram_enabled: bool
    google_sheet: GoogleSheetConfig = field(default_factory=GoogleSheetConfig)

    @property
    def telegram_active(self) -> bool:
        return bool(
            self.telegram_enabled and self.telegram_bot_token and self.telegram_chat_id
        )


def load_config(config_path: str = "config.yaml", env_path: str = ".env") -> Config:
    load_dotenv(env_path)

    cfg_file = Path(config_path)
    if not cfg_file.exists():
        raise SystemExit(
            f"Config not found: {config_path}. Copy config.example.yaml to config.yaml and edit it."
        )
    data = yaml.safe_load(cfg_file.read_text(encoding="utf-8")) or {}

    provider = str(data.get("llm_provider", "anthropic")).lower()
    if provider not in DEFAULT_MODELS:
        raise SystemExit(
            f"Unknown llm_provider: {provider!r} in {config_path}. Use one of: {', '.join(DEFAULT_MODELS)}"
        )

    gs = data.get("google_sheet") or {}
    google_sheet = GoogleSheetConfig(
        enabled=bool(gs.get("enabled", False)),
        sheet_id=str(gs.get("sheet_id", "")),
        worksheet=str(gs.get("worksheet", "Jobs")),
        service_account_file=str(gs.get("service_account_file", "./service-account.json")),
    )

    config = Config(
        llm_provider=provider,
        openai_api_key=os.getenv("OPENAI_API_KEY", ""),
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
        gmail_address=os.getenv("GMAIL_ADDRESS", ""),
        gmail_app_password=os.getenv("GMAIL_APP_PASSWORD", ""),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
        poll_interval_seconds=int(data.get("poll_interval_seconds", 300)),
        lookback_days=int(data.get("lookback_days", 2)),
        first_run_lookback_days=int(data.get("first_run_lookback_days", 7)),
        known_senders=dict(data.get("known_senders") or {}),
        job_subject_keywords=list(data.get("job_subject_keywords") or []),
        skip_link_domains=list(data.get("skip_link_domains") or []),
        score_threshold=int(data.get("score_threshold", 60)),
        database_path=str(data.get("database_path", "./data/jobs.db")),
        resume_path=str(data.get("resume_path", "./resume.txt")),
        models=dict(data.get("models") or {}),
        telegram_enabled=bool(data.get("telegram_enabled", True)),
        google_sheet=google_sheet,
    )

    # Validate the API key for the chosen provider + Gmail creds.
    missing = []
    provider_key = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}[provider]
    if not os.getenv(provider_key):
        missing.append(f"{provider_key} (required for llm_provider: {provider})")
    if not config.gmail_address:
        missing.append("GMAIL_ADDRESS")
    if not config.gmail_app_password:
        missing.append("GMAIL_APP_PASSWORD")
    if missing:
        raise SystemExit(f"Missing required secrets in .env: {', '.join(missing)}")

    if not Path(config.resume_path).exists():
        raise SystemExit(
            f"Resume file not found: {config.resume_path}. "
            "Copy resume.example.txt to resume.txt and put your profile in it."
        )

    # Fill in per-provider model defaults for any stage the user didn't set.
    for stage, model_id in DEFAULT_MODELS[provider].items():
        config.models.setdefault(stage, model_id)

    return config
