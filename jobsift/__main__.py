"""Entry point:  python -m jobsift [--once]"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from .config import load_config
from .gmail import GmailReader
from .llm import build_llm
from .pipeline import run_once
from .store import Store

# Module level, because the helpers below run outside main() and main's own
# `log` is local to it. Same logger either way - getLogger is a lookup.
log = logging.getLogger("jobsift")


def _worker_paths(config, args) -> dict:
    """Where the core keeps what a residential worker may read or replace."""
    evidence = config.evidence or {}
    inbox = ((config.scrape_sources or {}).get("worker_inbox") or {}).get("dir")
    return {"database": config.database_path, "inbox": inbox or "./data/inbox",
            "brief": evidence.get("brief") or "./data/career-brief.md",
            "career": evidence.get("career") or "./career.yaml",
            "resume": config.resume_path, "profile": args.profile}


def _make_draft(config, llm, job, posting, resume, profile):
    """One draft, by whichever path config.yaml selects.

    Both paths return the same shape, so everything downstream - render, the
    Drafts tab, the status column - is unaware of which one ran.
    """
    if config.multi_agent_drafting:
        from .agents import run, to_draft_result

        evidence = ""
        rules: list = []
        settings = config.evidence or {}
        if settings.get("enabled"):
            from . import career
            from .evidence import load_index, summarise

            # The brief when it has been built, since it carries what the counts
            # cannot: which repo is which product, and what must never be said.
            brief = Path(settings.get("brief") or "./data/career-brief.md")
            if settings.get("auto_refresh", True):
                # Just in time, unthrottled: this is the moment a stale brief
                # would put last month's work, or none of this week's, in a letter.
                career.refresh(settings, settings.get("career") or "./career.yaml", str(brief),
                               github_every_hours=float(settings.get("github_every_hours") or 24))
            evidence = (brief.read_text(encoding="utf-8") if brief.is_file()
                        else summarise(load_index(settings.get("out", "./data/evidence.yaml"))))
            rules = career.load(settings.get("career") or "./career.yaml").get("rules") or []
        models = {
            "extract": config.models.get("extract", config.models["score"]),
            "draft": config.models.get("draft", config.models["score"]),
            "verify": config.models.get("verify", config.models["score"]),
        }
        raw = run(llm, models, job, posting, resume, profile, evidence, rules)
        if raw:
            return to_draft_result(raw, profile, len(posting))
        log.warning("Multi-agent drafting produced nothing; falling back to one call")

    from .draft import draft_application

    return draft_application(llm, config.models["score"], job, resume, profile)


def _refresh_usage(profile_path: str) -> None:
    """Bring profile.yaml's Claude Code figures up to date before they are quoted.

    Here rather than on a timer, because the only moment the figure matters is the
    moment a draft is about to state it to an employer. Reading a local JSON costs
    nothing, and never fails a draft: a missing file leaves the profile as it is.
    """
    try:
        from .usage import sync_profile

        if sync_profile(profile_path):
            log.info("Refreshed Claude Code figures in %s", profile_path)
    except Exception:
        log.exception("Could not refresh usage figures; drafting with what is there")


def _run_draft(args, config) -> None:
    """Print a reviewable application draft for one stored job. Sends nothing."""
    import json
    import sqlite3

    import yaml

    from .draft import draft_application, render

    _refresh_usage(args.profile)
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
    posting = str(job.get("description_summary") or job.get("description") or "")
    result = _make_draft(config, llm, job, posting, resume, profile)
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

# Below this many characters of posting, a draft is not worth its call. Measured
# rather than picked: across the stored jobs, onlinejobs.ph carries a median of
# 3,013 characters and jobicy 7,016, while indeed carries 155, jobstreet's search
# API 182 and linkedin 3. Nothing real sits between those two groups.
MIN_POSTING_CHARS = 400


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
        # First, make the status column true again. A pass that was killed
        # partway, or a tick removed after one, otherwise leaves a status that
        # nothing will ever correct.
        fixed = sheet.reconcile_draft_status()
        if fixed:
            log.info("Corrected %d stale draft status cell(s)", fixed)
        wanted = sheet.drafts_requested() - sheet.drafted_urls()
    except Exception:
        log.exception("Could not read draft requests from the sheet")
        return 0
    if not wanted:
        return 0

    _refresh_usage(args.profile)
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

        # How much posting text this draft would actually see, from the best
        # source available: the page text kept at enrich time, else a live fetch,
        # else whatever the board's own listing carried.
        available = posting or (job.get("description_summary") or "")
        if len(available) < MIN_POSTING_CHARS:
            # Refuse rather than spend. A draft off two sentences comes back with
            # no employer questions (there are none in the text to find), a letter
            # written from the title, and a requirements table judging nothing -
            # and it looks exactly like a real draft in the tab.
            #
            # This is a property of the SOURCE, not of the row. Indeed and
            # Jobstreet are both in skip_link_domains, so nothing is ever fetched
            # for them; Indeed blocks it and the Jobstreet job page is 403 behind
            # Cloudflare. onlinejobs.ph rows carry thousands of characters because
            # that scraper reads the page itself, and they draft well.
            log.info("Skipping draft for %s @ %s: only %d chars of posting",
                     job.get("job_title"), job.get("company"), len(available))
            sheet.set_draft_status({
                url: f"needs the posting text - run: --draft {row['id']} --posting FILE"
            })
            continue

        log.info("Drafting for %s @ %s (%d chars of posting, %s)",
                 job.get("job_title"), job.get("company"), len(available),
                 "fetched" if posting else "from the listing")
        sheet.set_draft_status({url: "drafting..."})
        if posting:
            # Same substitution --posting makes: the full text replaces the stored
            # snippet outright, because it is the same content only complete, and
            # what the drafter most needs to see sits at the end of it.
            job = {**job, "description_summary": posting, "description": posting}
        try:
            result = _make_draft(config, llm, job, available, resume, profile)
            if not result:
                # The call came back unusable - see draft_application. Say so in
                # the cell: `continue` alone left the row on "drafting..." until
                # some later pass reconciled it, which reads as still working.
                log.warning("Draft for %s came back unusable; leaving it unticked-ready",
                            url)
                sheet.set_draft_status({url: "the model returned nothing usable - tick again to retry"})
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
        "--setup",
        action="store_true",
        help="Connect the accounts jobsift needs - an AI key, Gmail, and optionally "
             "Telegram and a Google Sheet - and check each one works. Writes .env and "
             "config.yaml, keeping every comment. Safe to run again.",
    )
    parser.add_argument(
        "--suggest-senders",
        nargs="?",
        const=30,
        type=int,
        metavar="DAYS",
        help="Read the sender and subject of recent inbox mail (nothing opened or marked "
             "read) and list senders that look like job alerts but are not in "
             "known_senders, with the lines to paste. Changes nothing. Defaults to 30 days.",
    )
    parser.add_argument(
        "--draft",
        metavar="MATCH",
        help="Draft an application for a stored job (match on company or title, or a job id). "
             "Prints a cover letter and proposed answers for review; sends nothing.",
    )
    parser.add_argument("--profile", default="profile.yaml", help="Path to profile.yaml")
    parser.add_argument(
        "--index-repos",
        action="store_true",
        help="Rebuild the evidence index and exit: find and count every repo, "
             "merge career.yaml, write data/career-brief.md. The brief is what "
             "lets a draft claim work the resume forgot to mention.",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="With --index-repos or --index-refresh: count only what is on disk, "
             "never touch GitHub.",
    )
    parser.add_argument(
        "--index-status",
        action="store_true",
        help="Say what in the evidence brief is stale and when GitHub was last "
             "checked; exit 1 if anything is. Rebuilds nothing.",
    )
    parser.add_argument(
        "--index-refresh",
        action="store_true",
        help="Rebuild the evidence brief only if something changed: offline for "
             "local commits or a career.yaml edit, with GitHub once a day. The same "
             "refresh the loop runs hourly and every draft runs first.",
    )
    parser.add_argument(
        "--worker-serve",
        nargs="?",
        const="",
        metavar="REQUEST",
        help="Answer one residential-worker request and exit (docs/residential-worker.md). "
             "Meant to be pinned to an SSH key on the core, which passes the request in "
             "SSH_ORIGINAL_COMMAND; the argument is for trying one by hand.",
    )
    parser.add_argument(
        "--check-draft",
        nargs="+",
        metavar="FILE",
        help="Run career.yaml's rules over finished documents (.txt, .md, .pdf); "
             "exit 1 on any violation. No model involved.",
    )
    parser.add_argument(
        "--render-init",
        metavar="COMPANY",
        help="Start the spec for one application from a master resume: writes "
             "render.specs_dir/COMPANY.yaml holding the master's content, to be tailored. "
             "COMPANY is one token (WhiteCloak). Choose the master with --master.",
    )
    parser.add_argument(
        "--master",
        metavar="DOCX",
        help="With --render-init: the resume .docx to start from (default: the newest of "
             "render.masters in config.yaml).",
    )
    parser.add_argument(
        "--render",
        metavar="SPEC",
        help="Render one application from its spec (a file, or the COMPANY given to "
             "--render-init): the resume PDF fitted to one full page, plus the email, "
             "message or cover letter its board needs, then every check that needs no "
             "model. Exit 1 when something must be fixed. No model call.",
    )
    parser.add_argument(
        "--render-out",
        metavar="DIR",
        help="With --render: write the files here instead of render.output_dir.",
    )
    parser.add_argument(
        "--publish",
        metavar="SPEC",
        help="For boards that take no file: publish the rendered resume under "
             "render.publish_repo at a random, permanent name, push, wait until the live "
             "file matches, log it, and put the link into the message.",
    )
    parser.add_argument(
        "--sync-usage",
        action="store_true",
        help="Refresh profile.yaml's Claude Code figures from the kimlj.dev usage file "
             "and exit. Runs by itself before every draft; this is for checking it.",
    )
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
        "--resync-sheet",
        action="store_true",
        help="Rewrite every row of the Google Sheet from the database, using this "
             "version's columns and formatting. Free - no model calls. Run it after "
             "upgrading: new rows are written correctly anyway, but rows already in "
             "the sheet keep whatever the version that wrote them produced. Your "
             "your stage and draft columns are not touched.",
    )
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
        "--sync-stages",
        action="store_true",
        help="Copy the Google Sheet's stage column into the database now, and exit. "
             "Free - two column reads per tab, nothing written to the sheet. The "
             "running loop already does this once a pass; this is for the first "
             "backfill, or when you have just moved a row and want the database to "
             "know before the next pass.",
    )
    parser.add_argument(
        "--skip-applied",
        action="store_true",
        help="With --export or --backfill-sheet: drop jobs whose stage in the "
             "Google Sheet says an application went in (Applied, Interviewing or "
             "Rejected), read live from the sheet and from the database. The column "
             "is yours; the program only ever advances it to Applied.",
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
    parser.add_argument(
        "--check-listings",
        nargs="?",
        const=25,
        type=int,
        metavar="N",
        help="Re-check the N highest-scoring stored onlinejobs.ph postings and mark "
             "the ones that have left the board's search results. Free of LLM cost, "
             "but one paced request per job at the site's 5s Crawl-delay, so 25 jobs "
             "takes about two minutes. Applied jobs and ones already marked are "
             "skipped. Defaults to 25.",
    )
    parser.add_argument(
        "--sweep-closed",
        action="store_true",
        help="Move every sheet row whose stage says Closed into the Closed tab, "
             "now. Free - two column reads and one move, no board touched and no "
             "model call. The running loop already does this once a pass; this is "
             "for when you have just filed a row and would rather not wait.",
    )
    parser.add_argument(
        "--vet",
        metavar="JOB",
        help="Record the employer check's verdict on one stored job, by id or url. "
             "Needs --verdict; --why and --as are optional. Written to the database "
             "and to the job's sheet row. Free - no model call, no board touched: the "
             "research happens elsewhere (the employer-vetting skill) and this only "
             "stores the answer. Running it again replaces the earlier verdict.",
    )
    parser.add_argument("--verdict", choices=("safe", "caution", "avoid"),
                        help="With --vet: the verdict")
    parser.add_argument("--why", default="",
                        help="With --vet: the one-line reason. It becomes the sheet cell, so keep it short")
    parser.add_argument("--as", dest="vetted_as", default="",
                        help="With --vet: who the research found the employer to be, "
                             "e.g. \"Mogul (usemogul.com)\" or \"Acme (probable)\"")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    parser.add_argument("--env", default=".env", help="Path to .env")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger("jobsift")  # the module-level one, by name
    # httpx logs every request's full URL at INFO, and a Telegram URL carries the
    # bot token (api.telegram.org/bot<token>/sendMessage). Warnings still show.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    if args.setup:
        # Before load_config, which refuses to start without the very files this makes.
        from .onboard import run as run_setup

        raise SystemExit(run_setup(args.config, args.env, args.profile))

    config = load_config(args.config, args.env)

    # The residential worker's two ends, settled before anything below runs.
    # --worker-serve is what a pinned SSH key executes on the core: it answers and
    # exits without fetching a rate or opening a model client. And a worker must
    # never write the database it no longer owns.
    if args.worker_serve is not None:
        import os

        from .worker import serve_stdio

        raise SystemExit(serve_stdio(
            os.environ.get("SSH_ORIGINAL_COMMAND") or args.worker_serve,
            _worker_paths(config, args)))
    if config.worker.get("enabled"):
        needs_db = [flag for flag, on in (
            ("--draft", args.draft), ("--export", args.export),
            ("--backfill-sheet", args.backfill_sheet), ("--resync-sheet", args.resync_sheet),
            ("--check-listings", args.check_listings), ("--scan-applied", args.scan_applied),
            ("--sync-stages", args.sync_stages), ("--vet", args.vet),
        ) if on]
        if needs_db:
            raise SystemExit(
                f"{' '.join(needs_db)} needs the database, and this machine is a residential "
                f"worker (worker.enabled in config.yaml). The database lives on the core, "
                f"{config.worker.get('ssh') or 'worker.ssh'}: run it there.")

    if args.suggest_senders is not None:
        from .onboard import SetupError
        from .senders import add_to_config, lines_to_add, render, suggest

        headers = GmailReader(config.gmail_address, config.gmail_app_password).fetch_headers(
            args.suggest_senders)
        report = suggest(headers, config.known_senders, config.job_subject_keywords,
                         own_address=config.gmail_address)
        entries = lines_to_add(report)
        # Asked only at a keyboard. Piped or scheduled, it prints and changes nothing,
        # as it always did.
        ask = bool(entries) and sys.stdin.isatty()
        print(render(report, args.suggest_senders, ask=ask))
        if not ask:
            return
        which = "it" if len(entries) == 1 else f"these {len(entries)}"
        try:
            answer = input(f"\nAdd {which} to config.yaml? [Y/n] ").strip().lower()
        except EOFError:
            answer = "n"
        if answer not in ("", "y", "yes"):
            print("Nothing was changed. Paste the lines you want under known_senders: yourself.")
            return
        try:
            added = add_to_config(args.config, entries)
        except (OSError, SetupError) as exc:
            print(f"Could not edit {args.config} ({exc}), so nothing was changed. "
                  "Paste the lines above under known_senders: instead.")
            return
        print(f"Added {len(added)} line(s) under known_senders in {args.config}. "
              "If jobsift is running, it starts reading them on its next pass.")
        return

    # The USD rate multiplies every dollar-quoted listing, so it decides what
    # clears min_salary_php and how the sheet sorts. Fetched once here, at the
    # one place every branch below passes through, and pushed into filters as a
    # module value as well as into settings: several call sites reach the parser
    # without a settings dict (the sheet writer among them), and a rate that
    # applied to some of them and not others would be worse than a stale one.
    import os

    from . import filters as _filters
    from .fx import usd_to_php

    _rate, _note = usd_to_php(
        config.filters.get("usd_to_php") or _filters.USD_TO_PHP,
        cache_path=os.path.join(os.path.dirname(config.database_path) or ".", "fx.json"),
    )
    _filters.set_usd_rate(_rate)
    config.filters["usd_to_php"] = _rate
    log.info("USD to PHP: %.2f (%s)", _rate, _note)

    evidence_settings = config.evidence or {}
    career_path = evidence_settings.get("career") or "./career.yaml"
    brief_path = evidence_settings.get("brief") or "./data/career-brief.md"

    if args.index_repos:
        from . import career
        from .evidence import summarise, write_evidence

        if not (evidence_settings.get("roots") or evidence_settings.get("repos")):
            raise SystemExit("No `evidence.roots` or `evidence.repos` in config.yaml to index.")
        # From scratch on purpose: this is the command for after changing what
        # gets counted. Keeping the index current is --index-refresh's job.
        payload = write_evidence(evidence_settings, fetch=not args.offline, reuse=False)
        print(summarise(payload["repos"]))
        problems = career.write_brief(career_path, payload, brief_path)
        for name, why in (payload.get("not_counted") or {}).items():
            print(f"not counted: {name} - {why}")
        for pair in payload.get("merged_clones") or []:
            print(f"one repo, two copies: {pair}")
        if not evidence_settings.get("authors"):
            print("\nNo `evidence.authors` in config.yaml, so every tracked file counts, "
                  "including code you cloned or started from. Set it to count only your commits.")
        print(f"\nindexed {len(payload['repos'])} repo(s) into {evidence_settings.get('out')}"
              f"; brief written to {brief_path}"
              + ("" if payload.get("fetched_from_github") else " (GitHub not consulted)"))
        if problems:
            print(f"\n{len(problems)} curated line(s) failed their source check and were "
                  "LEFT OUT of the brief:")
            print("\n".join(f"  {p}" for p in problems))
        # The master resume is the document most likely to break a rule, because
        # it predates most of them. Say so here, where it will be seen.
        rules = career.load(career_path).get("rules") or []
        resume_hits = career.check_text(Path(config.resume_path).read_text(encoding="utf-8"),
                                        rules) if Path(config.resume_path).is_file() else []
        if resume_hits:
            print(f"\n{config.resume_path} breaks {len(resume_hits)} rule(s):")
            print("\n".join(f"  {h['rule']}: {h['problem']}" for h in resume_hits))
        return

    github_hours = float(evidence_settings.get("github_every_hours") or 24)

    if args.index_status:
        from . import career

        state = career.index_state(evidence_settings, career_path, brief_path, github_hours)
        age = state["github_age_hours"]
        print("GitHub last checked: " + ("never" if age is None else f"{age:.0f}h ago")
              + (f" (due: over {github_hours:.0f}h)" if state["github_due"] else ""))
        stale = state["local"] + (["GitHub check due"] if state["github_due"] else [])
        if stale:
            print("STALE - " + "; ".join(stale) + ". --index-refresh rebuilds it.")
            raise SystemExit(1)
        print(f"current - {brief_path}")
        return

    if args.index_refresh:
        from . import career

        done = career.refresh(evidence_settings, career_path, brief_path,
                              online=not args.offline, github_every_hours=github_hours)
        print({"none": "current - nothing to rebuild",
               "offline": "rebuilt offline",
               "online": "rebuilt, GitHub included",
               "failed": "refresh FAILED, the last brief stays in use"}[done["action"]]
              + (": " + "; ".join(done["why"]) if done["why"] else ""))
        if done["action"] == "failed":
            raise SystemExit(1)
        return

    if args.check_draft:
        from . import career

        rules = career.load(career_path).get("rules") or []
        if not rules:
            raise SystemExit(f"No rules in {career_path} to check against.")
        broken = 0
        for name in args.check_draft:
            path = Path(name)
            if path.suffix.lower() == ".pdf":
                from pypdf import PdfReader

                text = "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
            else:
                text = path.read_text(encoding="utf-8")
            hits = career.check_text(text, rules)
            broken += len(hits)
            print(f"{name}: " + ("ok" if not hits else f"{len(hits)} rule(s) broken"))
            print("\n".join(f"  {h['rule']}: {h['problem']}. {h['why']}" for h in hits))
        if broken:
            raise SystemExit(1)
        return

    if args.render_init or args.render or args.publish:
        import re

        import yaml

        from . import render as _render

        settings = _render.settings_for(config.render)
        profile_file = Path(args.profile)
        profile = (yaml.safe_load(profile_file.read_text(encoding="utf-8")) or {}
                   if profile_file.exists() else {})

        if args.render_init:
            company = args.render_init
            if not re.fullmatch(r"[A-Za-z0-9]+", company):
                raise SystemExit("COMPANY is one token, letters and digits: WhiteCloak, not White Cloak.")
            candidates = [Path(m).expanduser() for m in ([args.master] if args.master
                                                          else settings["masters"] or [])]
            candidates = [m for m in candidates if m.is_file()]
            if not candidates:
                raise SystemExit("No master resume to start from: pass --master FILE.docx, or "
                                 "list render.masters in config.yaml.")
            master = max(candidates, key=lambda m: m.stat().st_mtime)
            dest = Path(settings["specs_dir"]) / f"{company}.yaml"
            if dest.exists():
                raise SystemExit(f"{dest} already exists. Edit it, or delete it to start over.")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(_render.starter_spec(master, company), encoding="utf-8")
            # Render the untouched starter once, so the first draft is written to the
            # page it has. The _AI master runs about four lines over at the house type
            # scale; a draft that did not know that came back seven lines over.
            import tempfile

            from . import career

            rules = (career.load(career_path).get("rules") or []) if Path(career_path).exists() else []
            try:
                with tempfile.TemporaryDirectory(prefix="jobsift-init-") as scratch:
                    fit = _render.render(dest, profile, settings, rules, out_dir=scratch, offline=True)
                over = next((f.split(". ")[0] for f in fit["fail"] if f.startswith("resume:")), "")
                budget = (f"As it stands it does NOT fit: {over.removeprefix('resume: ')}."
                          if fit["pages"] > 1 else
                          f"As it stands it fits one page with {fit['waste_in']}in to spare.")
            except (_render.SpecError, _render.RenderError) as exc:
                budget = f"(Could not render the starter to measure it: {exc})"
            print(f"Started {dest}\n  from {master}\n{budget}\nTailor the words and set the "
                  f"board, then run: --render {company}")
            return

        if args.render:
            from . import career

            rules = (career.load(career_path).get("rules") or []) if Path(career_path).exists() else []
            try:
                report = _render.render(_render.spec_path(args.render, settings), profile, settings,
                                        rules, out_dir=args.render_out, offline=args.offline)
            except (_render.SpecError, _render.RenderError) as exc:
                raise SystemExit(str(exc))
            print(_render.report_text(report))
            if report["fail"]:
                raise SystemExit(1)
            return

        from .publish import PublishError, publish

        try:
            done = publish(_render.spec_path(args.publish, settings), profile, settings)
        except (_render.SpecError, PublishError) as exc:
            raise SystemExit(str(exc))
        print(f"Published {done['url']}\n  logged: {done['row']}")
        for path in done["filled"]:
            print(f"  link written into {path}")
        if not done["verified"]:
            raise SystemExit("The live file did not match the local PDF within 5 minutes. Do not "
                             "send the link until it does; open it and compare.")
        print("The live file matches the local PDF.")
        return

    if args.sync_usage:
        from .usage import read_usage, sync_profile

        data = read_usage()
        if not data:
            raise SystemExit("No usage file to read. Has the sync task run yet?")
        changed = sync_profile(args.profile)
        print(f"{args.profile}: {'updated' if changed else 'already current'} — "
              f"{data['hours']}h, {data['prompts']} prompts, {data['projects']} projects "
              f"(as of {data['as_of']})")
        return

    if args.draft:
        _run_draft(args, config)
        return

    if args.resync_sheet:
        import json
        import sqlite3

        from .sheets import SheetWriter

        if not config.google_sheet.enabled:
            raise SystemExit("google_sheet.enabled is false in config - nothing to resync.")

        conn = sqlite3.connect(config.database_path)
        conn.row_factory = sqlite3.Row
        records = {r["url"]: json.loads(r["data"])
                   for r in conn.execute("select url, data from jobs") if r["url"]}

        # Constructing the writer migrates the columns; resync then rewrites the
        # values under them. Both are needed: one moves the labels, the other
        # brings what is beneath them up to what this version computes.
        writer = SheetWriter(config.google_sheet, config.score_threshold)
        count = writer.resync(records)
        print(f"resynced {count} row(s) from {len(records)} stored job(s)")
        return

    if args.vet:
        from datetime import date

        if not args.verdict:
            raise SystemExit("--vet needs --verdict: safe, caution or avoid.")
        records = Store(config.database_path).mark_vetted(
            args.vet, args.verdict, args.why.strip(), args.vetted_as.strip(),
            date.today().isoformat())
        if not records:
            raise SystemExit(f"no stored job matches {args.vet!r} - give its id or its url")
        first = records[0]
        print(f"{first.get('job_title')} [{first.get('source')}]: {args.verdict}"
              + (f" - {args.vetted_as.strip()}" if args.vetted_as.strip() else "")
              + (f" ({len(records)} rows, one posting)" if len(records) > 1 else ""))
        # The database is the record; the sheet is a view of it. A sheet that
        # cannot be reached leaves the verdict stored, and --resync-sheet writes
        # it out later, so this reports and carries on rather than failing.
        if config.google_sheet.enabled and first.get("url"):
            try:
                from .sheets import SheetWriter, _cell

                written = SheetWriter(config.google_sheet, config.score_threshold).set_vetting(
                    {first["url"]: _cell("vetting", first)})
                print(f"sheet: {written} row(s) updated" if written else
                      "sheet: no row for this job (filtered out, or never written) - "
                      "the database has the verdict")
            except Exception as err:
                print(f"database updated; the sheet was not ({err}). "
                      "--resync-sheet will catch it up.")
        return

    if args.check_listings:
        from datetime import datetime

        import httpx

        from .export import rows as stored_rows
        from .sources.onlinejobs import USER_AGENT, DEFAULT_DELAY, still_listed

        store = Store(config.database_path)
        # Sent by either word, a board's receipt or the sheet's stage. Receipts
        # alone once let an application already sent be filed into Closed.
        skip = store.sent_urls() | store.delisted_urls()
        candidates = [
            r for r in stored_rows(config.database_path, settings=config.filters)
            if r.get("source") == "onlinejobs_ph" and r.get("url") and r["url"] not in skip
        ][: args.check_listings]

        if not candidates:
            print("nothing to check")
            return

        delay = float((config.scrape_sources.get("onlinejobs_ph") or {}).get(
            "delay_seconds", DEFAULT_DELAY))
        delay = max(delay, DEFAULT_DELAY)
        print(f"checking {len(candidates)} posting(s) at {delay:.0f}s apart "
              f"(~{len(candidates) * delay / 60:.1f} min)")

        gone_urls: list[str] = []
        unknown = 0
        now = datetime.now().isoformat(timespec="seconds")
        with httpx.Client(timeout=30, headers={"User-Agent": USER_AGENT},
                          follow_redirects=True) as client:
            for i, record in enumerate(candidates):
                if i:
                    time.sleep(delay)
                listed = still_listed(client, record["url"], record.get("job_title") or "", delay)
                if listed is False:
                    store.mark_delisted(record["url"], now)
                    gone_urls.append(record["url"])
                    print(f"  gone    [{record.get('score')}] {record.get('job_title')}")
                elif listed is None:
                    unknown += 1
                    print(f"  unknown [{record.get('score')}] {record.get('job_title')}")

        gone = len(gone_urls)
        print(f"{gone} delisted, {unknown} inconclusive, "
              f"{len(candidates) - gone - unknown} still listed")

        # The database is the record either way; the sheet is where the decision
        # gets made, so a job that cannot be applied to should not still be
        # sitting in the shortlist. Only reached when something actually closed.
        if gone_urls and config.google_sheet.enabled:
            from .sheets import SheetWriter

            writer = SheetWriter(config.google_sheet, config.score_threshold)
            moved = writer.move_to_closed(gone_urls)
            print(f"moved {moved} row(s) to the {writer.closed_title!r} tab")
        return

    if args.sweep_closed:
        if not config.google_sheet.enabled:
            raise SystemExit("google_sheet.enabled is false in config - there is no "
                             "sheet to sweep.")
        from .sheets import SheetWriter

        writer = SheetWriter(config.google_sheet, config.score_threshold)
        moved = writer.sweep_closed()
        print(f"moved {moved} row(s) to the {writer.closed_title!r} tab")
        return

    if args.sync_stages:
        if not config.google_sheet.enabled:
            raise SystemExit("google_sheet.enabled is false in config - there is no "
                             "sheet to read.")
        from .sheets import SheetWriter

        stages = SheetWriter(config.google_sheet, config.score_threshold).stages()
        changed = Store(config.database_path).record_stages(stages)
        # Counted by hand: an import of Counter here would make the name local to
        # all of main(), the trap described under --scan-applied below.
        counts: dict[str, int] = {}
        for stage, _tab in stages.values():
            counts[stage] = counts.get(stage, 0) + 1
        print(f"read {len(stages)} row(s): " + ", ".join(
            f"{n} {stage}" for stage, n in sorted(counts.items(), key=lambda kv: -kv[1])))
        print(f"{changed} stage change(s) recorded")
        return

    if args.scan_applied:
        # GmailReader and Store are imported at module level. Importing either
        # here would make the name local to main() and unbind it everywhere else
        # in the function, including the normal run path further down.
        from .applied import (
            CONFIRMATION_PHRASES, CONFIRMATION_SENDERS, detect, match_to_jobs,
        )
        from .export import rows as stored_rows

        gmail = GmailReader(config.gmail_address, config.gmail_app_password)
        messages = gmail.fetch_confirmations(
            CONFIRMATION_SENDERS, args.scan_applied, phrases=CONFIRMATION_PHRASES)
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

    if config.worker.get("enabled"):
        # In place of the pipeline, not beside it: two copies on two databases
        # score and alert every job twice. See jobsift/worker.py.
        from .worker import run as run_worker

        log.info("jobsift started as a residential worker for %s", config.worker.get("ssh"))
        run_worker(config, args.profile, once=args.once)
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

    # The evidence index keeps itself current here, so nobody has to remember to.
    # Checked at start-up and then every refresh_minutes, on the wall clock
    # rather than a monotonic one: after a night with the laptop asleep the check
    # must be overdue on waking, not eight hours away. A check that finds nothing
    # costs one git call per repo, and a rebuild counts only the repos that moved.
    auto_refresh = bool(evidence_settings.get("enabled")
                        and evidence_settings.get("auto_refresh", True))
    refresh_every = 60 * float(evidence_settings.get("refresh_minutes") or 60)
    next_refresh = 0.0

    while True:
        try:
            if auto_refresh and time.time() >= next_refresh:
                next_refresh = time.time() + refresh_every
                from . import career

                done = career.refresh(evidence_settings, career_path, brief_path,
                                      github_every_hours=github_hours)
                if done["action"] != "none":
                    log.info("Evidence index: %s (%s)", done["action"], "; ".join(done["why"]))

            # Re-read every pass rather than once at start-up. On a core fed by a
            # residential worker, resume.txt is replaced whenever it is edited at
            # home, and a copy held from start-up would go on scoring every job
            # against the old resume until someone restarted this process. The
            # worker replaces it atomically, so a pass never reads half a file.
            with open(config.resume_path, encoding="utf-8") as fh:
                resume = fh.read()

            # known_senders too, so a line --suggest-senders added, or an `ignore`
            # taking back a sender added by itself, needs no restart. A config.yaml
            # caught mid-edit keeps the senders already loaded.
            try:
                from .config import reload_known_senders

                config.known_senders = reload_known_senders(args.config, config.database_path)
            except Exception:
                log.exception("Could not re-read known_senders; keeping the ones loaded")

            # Once a day, start reading senders that are clearly job boards, so a
            # new user never has to learn known_senders exists (senders.learn).
            # Before the pass, so today's alerts from them are read today. Not on
            # --once, which is for trying things and should not write data/.
            if config.learn_senders and not args.once:
                try:
                    from .senders import learn, learned_path, notice

                    added = learn(learned_path(config.database_path), gmail.fetch_headers,
                                  config.known_senders, config.job_subject_keywords,
                                  own_address=config.gmail_address)
                    if added:
                        for picked in added:
                            config.known_senders.setdefault(picked["key"], picked["label"])
                        log.info("Now reading job alerts from %s (found in the inbox)",
                                 ", ".join(picked["key"] for picked in added))
                        if telegram_send is not None:
                            from .notify import send_text

                            send_text(config.telegram_bot_token, config.telegram_chat_id,
                                      notice(added))
                except Exception:
                    log.exception("Looking for new job-alert senders failed; continuing")

            # Both of these read cells the user edited, and both run before the
            # pass rather than after it. A pass spends minutes in the scrape
            # sources - onlinejobs.ph is paced to its robots.txt Crawl-delay,
            # about five seconds a page - and an edit sitting behind all of that
            # is indistinguishable from an edit that did nothing.
            #
            # Closed before drafts. A row staged Closed with the draft box still
            # ticked would otherwise be drafted on its way out: a model call
            # spent on the one job the user has just said they are done with.
            try:
                if sheet is not None:
                    filed = sheet.sweep_closed()
                    if filed:
                        log.info("Filed %d row(s) staged Closed into the %s tab",
                                 filed, sheet.closed_title)
            except Exception:
                # Same rule as the drafts below: the sheet is an optional output
                # and nothing in it may stop the inbox being read.
                log.exception("Filing rows staged Closed failed; continuing")

            # After the sweep, so a row just filed is read in its new tab. The
            # stage column is the user's record of what was applied to; the
            # database keeps a copy so everything that reads it alone - exports,
            # --check-listings - gets the same answer (docs/decisions.md).
            try:
                if sheet is not None:
                    changed = store.record_stages(sheet.stages())
                    if changed:
                        log.info("Stage column: %d change(s) copied to the database", changed)
            except Exception:
                log.exception("Copying the stage column failed; continuing")

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
