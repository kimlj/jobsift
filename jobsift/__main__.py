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


def _run_draft(args, config) -> None:
    """Print a reviewable application draft for one stored job. Sends nothing."""
    import json
    import sqlite3

    import yaml

    from .draft import draft_application, render

    try:
        profile = yaml.safe_load(open(args.profile, encoding="utf-8")) or {}
    except FileNotFoundError:
        raise SystemExit(
            f"No {args.profile}. Copy profile.example.yaml to profile.yaml and fill it in — "
            "it is the only thing the drafter may state as fact about you."
        )

    conn = sqlite3.connect(config.database_path)
    conn.row_factory = sqlite3.Row
    match = args.draft
    rows = conn.execute(
        "SELECT * FROM jobs WHERE id = ? OR company LIKE ? OR title LIKE ? ORDER BY score DESC",
        (match if match.isdigit() else -1, f"%{match}%", f"%{match}%"),
    ).fetchall()
    if not rows:
        raise SystemExit(f"No stored job matches {match!r}.")
    if len(rows) > 1:
        print(f"{len(rows)} jobs match {match!r} — drafting for the highest scoring:")
        for row in rows[:6]:
            print(f"   id={row['id']:<5} {row['score']:>3}  {row['title'][:44]} @ {row['company'][:26]}")
        print()

    row = rows[0]
    job = json.loads(row["data"]) if row["data"] else {}

    if args.posting:
        import sys

        text = sys.stdin.read() if args.posting == "-" else open(args.posting, encoding="utf-8").read()
        text = text.strip()
        if text:
            # The full posting replaces the stored snippet outright: it is the same
            # content, only complete, and the instructions we most need sit at its end.
            job["description_summary"] = text
            job["description"] = text
            origin = "stdin" if args.posting == "-" else args.posting
            print(f"(using {len(text)} chars of posting text from {origin})\n")
    job.setdefault("job_title", row["title"])
    job.setdefault("company", row["company"])
    job.setdefault("score", row["score"])

    llm = build_llm(
        config.llm_provider,
        openai_api_key=config.openai_api_key,
        anthropic_api_key=config.anthropic_api_key,
    )
    resume = open(config.resume_path, encoding="utf-8").read()
    result = draft_application(llm, config.models["score"], job, resume, profile)
    if not result:
        raise SystemExit("The model returned nothing usable.")
    print(render(job, result))


def main() -> None:
    parser = argparse.ArgumentParser(prog="jobsift", description="Job-alert email watcher")
    parser.add_argument("--once", action="store_true", help="Run a single pass and exit")
    parser.add_argument(
        "--draft",
        metavar="MATCH",
        help="Draft an application for a stored job (match on company or title, or a job id). "
             "Prints a cover letter and proposed answers for review; sends nothing.",
    )
    parser.add_argument("--profile", default="profile.yaml", help="Path to profile.yaml")
    parser.add_argument(
        "--posting",
        metavar="FILE",
        help="File holding the FULL posting text (use '-' for stdin). Alert emails carry a "
             "truncated snippet and Indeed blocks fetching the page, so paste the real "
             "posting here to draft against everything the employer actually wrote.",
    )
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    parser.add_argument("--env", default=".env", help="Path to .env")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger("jobsift")

    config = load_config(args.config, args.env)

    if args.draft:
        _run_draft(args, config)
        return

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
            config.telegram_bot_token, config.telegram_chat_id, record, config.telegram_options
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
