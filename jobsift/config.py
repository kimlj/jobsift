"""Configuration loading: secrets from .env, settings from config.yaml."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

# Per-provider default models, for any stage config.yaml does not name. A cheap
# model reads (extract pulls listings out of an email, enrich reads a page) and a
# stronger one judges (score, which drafting also uses unless `draft` is named).
# Prices per 1M input / output tokens, from each provider's docs on 2026-09-11.
# In the order --setup offers them.
DEFAULT_MODELS = {
    # $0.15 / $0.60 off-peak, double at peak: cheap enough to judge with too.
    "deepseek": {"extract": "deepseek-flash", "enrich": "deepseek-flash",
                 "score": "deepseek-flash"},
    # luna $0.20 / $1.20 to read, terra $2 / $12 to judge.
    "openai": {"extract": "gpt-5.6-luna", "enrich": "gpt-5.6-luna", "score": "gpt-5.6-terra"},
    # haiku $1 / $5 to read, sonnet $2 / $10 to judge. Was claude-opus-4-8
    # ($5 / $25) for all three, which made every new install pay Opus rates to
    # pull a title out of an email.
    "anthropic": {"extract": "claude-haiku-4-5", "enrich": "claude-haiku-4-5",
                  "score": "claude-sonnet-5"},
}


@dataclass
class GoogleSheetConfig:
    enabled: bool = False
    sheet_id: str = ""
    worksheet: str = "Shortlist"
    # Second tab for jobs the scorer put below score_threshold. Empty turns the
    # split off and everything lands in `worksheet`.
    worksheet_below: str = "Below the bar"
    # Where a posting goes once the board stops listing it. Created lazily, so a
    # sheet whose jobs are all still open never grows an empty tab. Empty turns
    # the move off and a delisted row just keeps its status column.
    worksheet_closed: str = "Closed"
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
    salary_baseline_php: int
    database_path: str
    resume_path: str
    models: dict
    telegram_enabled: bool
    # Floor on skills+experience before salary and priority may carry a job over
    # score_threshold. See min_fit_ratio in config.yaml.
    min_fit_ratio: float = 0.3
    priority_keywords: list = field(default_factory=list)
    priority_points: int = 0
    telegram_options: dict = field(default_factory=dict)
    scrape_sources: dict = field(default_factory=dict)
    filters: dict = field(default_factory=dict)
    scrape_interval_seconds: int = 1200
    google_sheet: GoogleSheetConfig = field(default_factory=GoogleSheetConfig)
    # Repos that back application claims. See jobsift/evidence.py.
    evidence: dict = field(default_factory=dict)
    multi_agent_drafting: bool = False
    # Set on a machine that only scrapes onlinejobs.ph for a core elsewhere.
    # See jobsift/worker.py.
    worker: dict = field(default_factory=dict)
    # Last, with a default, so nothing that builds a Config positionally breaks.
    deepseek_api_key: str = ""
    # The daily check that adds clear job boards by itself. See senders.learn.
    learn_senders: bool = True

    @property
    def telegram_active(self) -> bool:
        return bool(
            self.telegram_enabled and self.telegram_bot_token and self.telegram_chat_id
        )


def known_senders_for(data: dict, database_path: str) -> dict:
    """config.yaml's known_senders plus the ones jobsift added by itself.

    config.yaml wins on a key both name, which is how a learned sender is taken
    back: `kalibrr.com: ignore` there. Read at start-up and again every pass, so
    an edit - or --suggest-senders run while the service is up - takes effect
    without a restart.
    """
    from .senders import learned_path, read_learned

    senders = dict(data.get("known_senders") or {})
    for key, label in read_learned(learned_path(database_path))["senders"].items():
        senders.setdefault(key, label)
    return senders


def reload_known_senders(config_path: str, database_path: str) -> dict:
    data = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
    return known_senders_for(data, database_path)


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
        worksheet=str(gs.get("worksheet", "Shortlist")),
        worksheet_below=str(gs.get("worksheet_below", "Below the bar")),
        worksheet_closed=str(gs.get("worksheet_closed", "Closed")),
        service_account_file=str(gs.get("service_account_file", "./service-account.json")),
    )

    config = Config(
        llm_provider=provider,
        openai_api_key=os.getenv("OPENAI_API_KEY", ""),
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
        deepseek_api_key=os.getenv("DEEPSEEK_API_KEY", ""),
        gmail_address=os.getenv("GMAIL_ADDRESS", ""),
        gmail_app_password=os.getenv("GMAIL_APP_PASSWORD", ""),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
        poll_interval_seconds=int(data.get("poll_interval_seconds", 300)),
        lookback_days=int(data.get("lookback_days", 2)),
        first_run_lookback_days=int(data.get("first_run_lookback_days", 7)),
        known_senders=known_senders_for(data, str(data.get("database_path", "./data/jobs.db"))),
        job_subject_keywords=list(data.get("job_subject_keywords") or []),
        skip_link_domains=list(data.get("skip_link_domains") or []),
        score_threshold=int(data.get("score_threshold", 60)),
        min_fit_ratio=float(data.get("min_fit_ratio", 0.3)),
        salary_baseline_php=int(data.get("salary_baseline_php", 70000)),
        priority_keywords=list(data.get("priority_keywords") or []),
        priority_points=int(data.get("priority_points", 0)),
        telegram_options=dict(data.get("telegram") or {}),
        database_path=str(data.get("database_path", "./data/jobs.db")),
        resume_path=str(data.get("resume_path", "./resume.txt")),
        models=dict(data.get("models") or {}),
        evidence=dict(data.get("evidence") or {}),
        multi_agent_drafting=bool(data.get("multi_agent_drafting", False)),
        worker=dict(data.get("worker") or {}),
        telegram_enabled=bool(data.get("telegram_enabled", True)),
        scrape_sources=dict(data.get("scrape_sources") or {}),
        filters=dict(data.get("filters") or {}),
        scrape_interval_seconds=int(data.get("scrape_interval_seconds") or 1200),
        google_sheet=google_sheet,
        learn_senders=bool(data.get("learn_senders", True)),
    )

    # Validate the API key for the chosen provider + Gmail creds.
    missing = []
    provider_key = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY",
                    "deepseek": "DEEPSEEK_API_KEY"}[provider]
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
