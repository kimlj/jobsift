"""Entry point:  python -m jobsift [--once]"""

from __future__ import annotations

import argparse
import logging
import time

from .config import load_config
from .gmail import GmailReader
from .llm import build_llm
from .pipeline import run_once
from .store import Store


def main() -> None:
    parser = argparse.ArgumentParser(prog="jobsift", description="Job-alert email watcher")
    parser.add_argument("--once", action="store_true", help="Run a single pass and exit")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    parser.add_argument("--env", default=".env", help="Path to .env")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger("jobsift")

    config = load_config(args.config, args.env)

    llm = build_llm(
        config.llm_provider,
        openai_api_key=config.openai_api_key,
        anthropic_api_key=config.anthropic_api_key,
    )
    log.info("LLM provider: %s", config.llm_provider)

    store = Store(config.database_path)
    gmail = GmailReader(config.gmail_address, config.gmail_app_password)
    with open(config.resume_path, encoding="utf-8") as fh:
        resume = fh.read()

    # Optional outputs
    sheet = None
    if config.google_sheet.enabled:
        try:
            from .sheets import SheetWriter

            sheet = SheetWriter(config.google_sheet)
            log.info("Google Sheet output enabled (%s)", config.google_sheet.worksheet)
        except Exception:
            log.exception("Could not init Google Sheet — continuing without it")

    telegram_send = None
    if config.telegram_active:
        from .notify import send_telegram

        telegram_send = lambda record: send_telegram(
            config.telegram_bot_token, config.telegram_chat_id, record
        )
        log.info("Telegram alerts enabled (threshold %d)", config.score_threshold)

    log.info("jobsift started (%s)", "single run" if args.once else f"every {config.poll_interval_seconds}s")

    while True:
        try:
            handled = run_once(config, llm, store, gmail, resume, sheet, telegram_send)
            log.info("Pass complete — %d new job(s)", handled)
        except Exception:
            log.exception("Run failed")

        if args.once:
            break
        time.sleep(config.poll_interval_seconds)


if __name__ == "__main__":
    main()
