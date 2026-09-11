"""Does a pass leave the inbox as it found it, and download only what it needs?

No network and no account. imaplib is replaced by a fake server that records
every command it is sent, and the pipeline runs against a real SQLite store in a
temporary folder, with the extract model replaced by a tripwire. Three things
are attacked:

  * nothing is marked read. Fetching RFC822 sets \\Seen on a mailbox opened
    read-write, so every pass used to mark the whole lookback window read,
    personal mail included. Every fetch must be BODY.PEEK and every mailbox
    read-only;
  * mail already processed is not downloaded again. Every pass used to pull
    the whole window in full, 288 times a day, and then skip most of it;
  * an application receipt is filed as processed rather than handed to the
    extract model, which found no jobs in it and was paid for the call anyway
    (seen 2026-09-10 on every Indeed receipt).

    python dryrun_inbox.py
"""

import email.message
import tempfile
from pathlib import Path
from types import SimpleNamespace

import jobsift.gmail as gmail_module
import jobsift.pipeline as pipeline
from jobsift.gmail import GmailReader
from jobsift.store import Store

failures = 0


def check(label, ok, detail=""):
    global failures
    if not ok:
        failures += 1
    print(f"  {'ok  ' if ok else 'FAIL'}  {label:<58} {str(detail)[:60]}")


def message(sender, subject, body):
    msg = email.message.EmailMessage()
    msg["From"] = sender
    msg["Subject"] = subject
    msg["Date"] = "Fri, 11 Sep 2026 08:00:00 +0000"
    msg.set_content(body)
    return msg.as_bytes()


MAILBOX = {
    "101": message("Indeed <indeedapply@indeed.com>", "Indeed Application: Full-Stack Developer",
                   "Your application has been submitted. Good luck!"),
    "102": message("Mum <mum@example.com>", "dinner on sunday?", "bring rice"),
    "103": message("Kalibrr <alerts@kalibrr.com>", "5 new jobs for you",
                   "Python Developer at Acme, Makati"),
}


class FakeIMAP:
    """The handful of commands GmailReader sends, answered and remembered."""

    log: list = []

    def __init__(self, host, *args, **kwargs):
        pass

    def login(self, user, password):
        FakeIMAP.log.append(("login",))

    def select(self, mailbox, readonly=False):
        FakeIMAP.log.append(("select", mailbox, readonly))
        return "OK", [str(len(MAILBOX)).encode()]

    def uid(self, command, *args):
        if command == "search":
            FakeIMAP.log.append(("search",))
            return "OK", [" ".join(MAILBOX).encode()]
        ids, spec = args
        ids = ids.decode() if isinstance(ids, bytes) else ids
        FakeIMAP.log.append(("fetch", ids, spec))
        rows = []
        for key in ids.split(","):
            raw = MAILBOX[key]
            if "HEADER.FIELDS" in spec:
                raw = raw.split(b"\n\n", 1)[0] + b"\n\n"
            rows += [(f"1 (UID {key} BODY[] {{{len(raw)}}}".encode(), raw), b")"]
        return "OK", rows

    def logout(self):
        pass


def fetches():
    return [entry for entry in FakeIMAP.log if entry[0] == "fetch"]


def selects():
    return [entry for entry in FakeIMAP.log if entry[0] == "select"]


gmail_module.imaplib.IMAP4_SSL = FakeIMAP
reader = GmailReader("me@example.com", "abcd efgh ijkl mnop")

print("downloading\n" + "-" * 78)
got = reader.fetch_recent(1, wanted=lambda uid: uid != "102")
check("only the wanted mail comes back", [m["uid"] for m in got] == ["101", "103"],
      [m["uid"] for m in got])
check("mail not wanted is never downloaded", not any("102" in f[1] for f in fetches()),
      fetches())
check("and what comes back is whole",
      got and got[0]["from"] == "indeedapply@indeed.com" and "submitted" in got[0]["text"])

FakeIMAP.log.clear()
reader.fetch_headers(30)
reader.fetch_confirmations(["indeedapply@indeed.com"], 90)
reader.fetch_recent(1)

print("\nleaving the inbox as it was\n" + "-" * 78)
check("every mailbox is opened read-only", selects() and all(s[2] for s in selects()),
      selects())
check("every fetch is a PEEK", fetches() and all("PEEK" in f[2] for f in fetches()),
      [f[2] for f in fetches()])

print("\na pass through the pipeline\n" + "-" * 78)
extracted = []
pipeline.extract_jobs = lambda llm, model, text, source: extracted.append(source) or []

with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
    db = Path(tmp) / "jobs.db"
    store = Store(str(db))
    config = SimpleNamespace(
        first_run_lookback_days=7, lookback_days=1, database_path=str(db),
        known_senders={"indeed.com": "indeed", "kalibrr.com": "kalibrr"},
        job_subject_keywords=["job alert"], filters={}, skip_link_domains=[],
        models={"extract": "x", "enrich": "x", "score": "x"}, scrape_sources={},
        scrape_interval_seconds=1200, salary_baseline_php=70000, priority_keywords=[],
        priority_points=0, min_fit_ratio=0.3, score_threshold=60)

    FakeIMAP.log.clear()
    pipeline.run_once(config, None, store, reader, "resume", None, None)
    check("the job alert goes to the extract model", extracted == ["kalibrr"], extracted)
    check("the receipt does not", "indeed" not in extracted, extracted)
    check("all three are filed as processed",
          all(store.is_email_processed(uid) for uid in MAILBOX))

    FakeIMAP.log.clear()
    pipeline.run_once(config, None, store, reader, "resume", None, None)
    check("the next pass downloads nothing", not fetches(), fetches())
    check("and calls no model", extracted == ["kalibrr"], extracted)
    store.conn.close()

print("\n" + ("-" * 78) +
      f"\n{'all cases behaved' if not failures else f'{failures} FAILED'}")
raise SystemExit(1 if failures else 0)
