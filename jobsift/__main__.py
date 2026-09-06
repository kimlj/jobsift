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

# Module level, because the helpers below run outside main() and main's own
# `log` is local to it. Same logger either way - getLogger is a lookup.
log = logging.getLogger("jobsift")


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

    # The terminal is where a draft is read; the sheet is where it is kept. A
    # scrollback buffer is not somewhere to leave a cover letter you paid for.
    if config.google_sheet.enabled and not args.no_sheet:
        try:
            from .sheets import SheetWriter

            tab = SheetWriter(config.google_sheet, config.score_threshold).append_draft(
                job, result)
            print(f"\nSaved to the {tab!r} tab of your sheet.")
        except Exception as err:
            # Never lose a draft to a sheet problem: it is already printed above.
            print(f"\nCould not write to the sheet ({err}). The draft above is unaffected.")


# Each tick is a model call, so a careless tick of forty rows should cost three
# calls and a log line, not forty. The rest are picked up on later passes.
MAX_DRAFTS_PER_PASS = 3


def _serve_sheet_drafts(args, config, llm, sheet, resume) -> int:
    """Draft applications for rows ticked `draft` in the sheet. Returns how many.

    The tick is never cleared. Clearing it would be the program writing to a
    column it promised only to read, and the user would lose the record of what
    they asked for. What stops a re-draft every five minutes is the Drafts tab
    itself: a url already in it is already answered.
    """
    import json
    import sqlite3

    import yaml

    from .draft import draft_application, fetch_posting, render

    if sheet is None:
        return 0
    try:
        wanted = sheet.drafts_requested() - sheet.drafted_urls()
    except Exception:
        log.exception("Could not read draft requests from the sheet")
        return 0
    if not wanted:
        return 0

    try:
        profile = yaml.safe_load(open(args.profile, encoding="utf-8")) or {}
    except FileNotFoundError:
        log.warning("%d draft(s) requested but there is no %s to answer from",
                    len(wanted), args.profile)
        return 0

    conn = sqlite3.connect(config.database_path)
    conn.row_factory = sqlite3.Row
    allow_hosts = (config.filters.get("fetch") or {}).get("allow_hosts")
    done = 0

    # Say something before spending a minute on the first one. Everything ticked
    # is acknowledged now; the ones past the per-pass cap keep saying queued,
    # which is true, rather than nothing, which reads as broken.
    batch = sorted(wanted)[:MAX_DRAFTS_PER_PASS]
    sheet.set_draft_status({url: "queued" for url in sorted(wanted)})

    for url in batch:
        row = conn.execute(
            "SELECT * FROM jobs WHERE url = ? ORDER BY id DESC LIMIT 1", (url,)
        ).fetchone()
        if not row:
            log.info("draft requested for a url with no stored job: %s", url)
            sheet.set_draft_status({url: "no stored job for this row"})
            continue
        job = json.loads(row["data"])
        job["id"] = row["id"]

        # Stored page text first - it was captured when the posting was live, and
        # a job worth drafting for is often one that has since been taken down.
        posting = job.get("full_text") or ""
        if len(posting) < 400:
            posting = fetch_posting(url, allow_hosts) or posting

        log.info("Drafting for %s @ %s (%d chars of posting)",
                 job.get("job_title"), job.get("company"), len(posting))
        sheet.set_draft_status({url: "drafting..."})
        if posting:
            # Same substitution --posting makes: the full text replaces the stored
            # snippet outright, because it is the same content only complete, and
            # what the drafter most needs to see sits at the end of it.
            job = {**job, "description_summary": posting, "description": posting}
        try:
            result = draft_application(llm, config.models["score"], job, resume, profile)
            if not result:
                continue
            sheet.append_draft(job, result)
            sheet.set_draft_status({url: sheet.draft_link() or "done"})
            done += 1
        except Exception as err:
            log.exception("Draft failed for %s", url)
            # The reason goes in the cell. A row that silently stays "drafting..."
            # is indistinguishable from one still being worked on.
            sheet.set_draft_status({url: f"failed: {str(err)[:90]}"})

    remaining = len(wanted) - done
    if remaining > 0:
        log.info("%d more draft(s) ticked; they run on later passes", remaining)
    return done


def _applied_urls(config) -> set[str]:
    """Everything marked applied, from both places it can be marked.

    The database holds what the boards confirmed; the sheet holds what the user
    ticked by hand. Neither is a superset of the other, and the sheet may be off
    entirely, so the union is the only honest answer.
    """
    from .store import Store

    urls = Store(config.database_path).applied_urls()
    if config.google_sheet.enabled:
        try:
            from .sheets import SheetWriter

            urls |= SheetWriter(config.google_sheet, config.score_threshold).applied_urls()
        except Exception as err:
            print(f"could not read ticks from the sheet ({err}); using the database only")
    return urls


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
        "--no-sheet",
        action="store_true",
        help="With --draft: print the draft but do not log it to the Drafts tab.",
    )
    parser.add_argument(
        "--posting",
        metavar="FILE",
        help="File holding the FULL posting text (use '-' for stdin). Alert emails carry a "
             "truncated snippet and Indeed blocks fetching the page, so paste the real "
             "posting here to draft against everything the employer actually wrote.",
    )
    parser.add_argument(
        "--export",
        metavar="FILE",
        nargs="?",
        const="jobs.csv",
        help="Write every stored job to a CSV for Excel and exit — every field, one row "
             "each, highest score first. Telegram only ever shows the jobs over the "
             "alert threshold; this is everything that was gathered.",
    )
    parser.add_argument("--min-score", type=int, default=0, help="With --export: only rows at or above this score")
    parser.add_argument("--source", default="", help="With --export: only rows whose source matches")
    parser.add_argument(
        "--scan-applied",
        nargs="?",
        const=90,
        type=int,
        metavar="DAYS",
        help="Read application receipts from the inbox (Indeed, Jobstreet) and tick "
             "those jobs applied in the sheet. Free — regex over mail already in the "
             "account, no LLM call and no board scraped. Defaults to 90 days back.",
    )
    parser.add_argument(
        "--skip-applied",
        action="store_true",
        help="With --export or --backfill-sheet: drop jobs ticked applied in the "
             "Google Sheet. The tick is yours and the program never writes it, so "
             "this is the one place the sheet is read back.",
    )
    parser.add_argument(
        "--backfill-sheet",
        action="store_true",
        help="Push already-stored jobs to the Google Sheet and exit. The sheet is "
             "only written as jobs are found, so a sheet enabled later starts empty "
             "while the database already holds everything. Honours --min-score, "
             "--source and --only-passing, so you can send just the shortlist.",
    )
    parser.add_argument(
        "--short-links",
        action="store_true",
        help="With --export: write the url column as a clickable Excel HYPERLINK "
             "labelled 'open' instead of the raw address. These links run long "
             "enough that the column crowds out everything else, and a raw URL in "
             "a CSV is only text to Excel until you edit the cell. Leave it off "
             "for a file anything other than Excel will read.",
    )
    parser.add_argument(
        "--only-passing",
        action="store_true",
        help="With --export: drop rows that today's filters would reject. Filters run "
             "at ingest, so tightening one leaves already-stored jobs in place — this "
             "re-checks them. Without it every row is kept and the `verdict` column "
             "says which would now be dropped and why.",
    )
    parser.add_argument(
        "--no-telegram",
        action="store_true",
        help="Run normally but send nothing to Telegram. Everything is still scored and "
             "stored, so a first run can be inspected with --export before any alert fires.",
    )
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    parser.add_argument("--env", default=".env", help="Path to .env")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger("jobsift")  # the module-level one, by name

    config = load_config(args.config, args.env)

    if args.draft:
        _run_draft(args, config)
        return

    if args.scan_applied:
        # GmailReader and Store are imported at module level. Importing either
        # here would make the name local to main() and unbind it everywhere else
        # in the function, including the normal run path further down.
        from .applied import CONFIRMATION_SENDERS, detect, match_to_jobs
        from .export import rows as stored_rows

        gmail = GmailReader(config.gmail_address, config.gmail_app_password)
        messages = gmail.fetch_confirmations(CONFIRMATION_SENDERS, args.scan_applied)
        confirmations = [c for c in (detect(m) for m in messages) if c]
        print(f"read {len(messages)} message(s), {len(confirmations)} confirmation(s)")

        store = Store(config.database_path)
        records = stored_rows(config.database_path, settings=config.filters)
        matched, unmatched = match_to_jobs(confirmations, records)

        fresh = [url for url, c in matched.items() if store.mark_applied(url, c)]
        print(f"matched {len(matched)} job(s) in the database, {len(fresh)} newly marked")

        if config.google_sheet.enabled:
            from .sheets import SheetWriter

            ticked = SheetWriter(config.google_sheet, config.score_threshold).mark_applied(
                store.applied_urls())
            print(f"ticked {ticked} row(s) in the sheet")

        # Named, not just counted. An application the program cannot place is
        # usually a job it never saw — applied to on the board directly, or
        # older than the database — and that is worth knowing rather than hiding.
        for c in unmatched:
            print(f"  no stored job for: {c.title}"
                  + (f" @ {c.company}" if c.company else "") + f"  [{c.board}]")
        return

    if args.backfill_sheet:
        from .export import rows as stored_rows
        from .sheets import SheetWriter
        if not config.google_sheet.enabled:
            raise SystemExit("google_sheet.enabled is false in config - nothing to write to.")
        # settings= is what computes `verdict`; without it every row comes back
        # with an empty one and --only-passing silently drops everything.
        records = stored_rows(config.database_path, min_score=args.min_score,
                              source=args.source, settings=config.filters)
        if args.only_passing:
            records = [r for r in records if r.get("verdict") == "kept"]
        if args.skip_applied:
            done = _applied_urls(config)
            before = len(records)
            records = [r for r in records if r.get("url") not in done]
            print(f"skipped {before - len(records)} already applied")
        writer = SheetWriter(config.google_sheet, config.score_threshold)
        # Backfill reads the whole database every time, so it drops what the
        # sheet already holds rather than writing a second copy of it.
        seen = writer.existing_urls()
        records = [r for r in records if r.get("url") not in seen]
        count = writer.append_many(records)
        print(f"appended {count} stored job(s) to the sheet")
        return

    if args.export:
        from .export import rows, summarise, to_csv

        records = rows(config.database_path, min_score=args.min_score,
                       source=args.source, settings=config.filters)
        if args.only_passing:
            records = [r for r in records if r.get("verdict") == "kept"]
        if args.skip_applied:
            done = _applied_urls(config)
            before = len(records)
            records = [r for r in records if r.get("url") not in done]
            print(f"skipped {before - len(records)} already applied")
        count = to_csv(records, args.export, args.short_links)
        print(summarise(records))
        print(f"\nwrote {count} row(s) to {args.export}")
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

            sheet = SheetWriter(config.google_sheet, config.score_threshold)
            log.info("Google Sheet output enabled (%s)", config.google_sheet.worksheet)
        except Exception:
            log.exception("Could not init Google Sheet — continuing without it")

    telegram_send = None
    if args.no_telegram:
        log.info("Telegram suppressed (--no-telegram); jobs are still scored and stored")
    elif config.telegram_active:
        from .notify import send_telegram

        telegram_send = lambda record: send_telegram(
            config.telegram_bot_token, config.telegram_chat_id, record, config.telegram_options
        )
        log.info("Telegram alerts enabled (threshold %d)", config.score_threshold)

    log.info("jobsift started (%s)", "single run" if args.once else f"every {config.poll_interval_seconds}s")

    while True:
        try:
            # Before the pass rather than after it. A pass spends minutes in
            # the scrape sources - onlinejobs.ph is paced to its robots.txt
            # Crawl-delay, about five seconds a page - and a tick sitting behind
            # all of that is indistinguishable from a tick that did nothing.
            try:
                _serve_sheet_drafts(args, config, llm, sheet, resume)
            except Exception:
                # The sheet is an optional output. A NameError in here once took
                # the whole daemon down mid-run; drafting is a convenience and
                # must never be able to stop the inbox being read.
                log.exception("Serving sheet draft requests failed; continuing")
            handled = run_once(config, llm, store, gmail, resume, sheet, telegram_send)
            log.info("Pass complete — %d new job(s)", handled)
        except Exception:
            log.exception("Run failed")

        if args.once:
            break
        time.sleep(config.poll_interval_seconds)


if __name__ == "__main__":
    main()
