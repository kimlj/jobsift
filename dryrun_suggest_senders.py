"""Do --suggest-senders and the daily check name the boards config.yaml is missing, and nothing else?

No network and no account. The inbox below is invented, in the shapes a real one
has, and the IMAP half is fed a canned server answer. What can go wrong is all
in the grouping and in what gets written, so that is what is attacked:

  * a board sending from a subdomain must be suggested as its base domain, and a
    board on a two-part suffix (jobsdb.com.ph) must not become `com.ph`,
  * a board config.yaml already knows must never come back as a suggestion,
  * a domain that also sends other mail - LinkedIn's messages and updates - must
    be suggested one address at a time, never as the whole domain,
  * personal mail about a job, from Gmail, must never be suggested at all,
  * one coincidental subject from a newsletter is not a job board,
  * once the suggested lines are pasted, the report must go quiet: the lines it
    prints have to be the lines that work,
  * saying yes writes exactly those lines into config.yaml and moves nothing
    else, not one comment,
  * the daily check adds only what is clearly a board, at most once a day, never
    twice, and never again once config.yaml says `ignore`.

    python dryrun_suggest_senders.py
"""

import base64
import shutil
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from jobsift.classify import classify
from jobsift.config import known_senders_for
from jobsift.gmail import parse_header_rows
from jobsift.onboard import SetupError
from jobsift.senders import (add_to_config, automatic, base_domain, learn, lines_to_add,
                             notice, read_learned, render, suggest)

KNOWN = {"indeed.com": "indeed", "jobs-noreply@linkedin.com": "linkedin",
         "foundit.com": "foundit"}
KEYWORDS = ["job alert", "new job", "jobs for you", "job match", "hiring", "we found"]
OWN = "me@gmail.com"


def mail(sender, subject):
    return {"from": sender, "subject": subject}


INBOX = [
    # a board config.yaml does not know, sending from a subdomain
    mail("alerts@mail.kalibrr.com", "5 new jobs for Full Stack Developer"),
    mail("alerts@mail.kalibrr.com", "Hiring now: React Developer at Acme"),
    mail("alerts@mail.kalibrr.com", "Python Engineer and 3 more"),
    # a board it does know
    mail("donotreply@jobalert.indeed.com", "Python Developer - Acme"),
    mail("donotreply@jobalert.indeed.com", "5 new jobs for developer"),
    # LinkedIn: one job address known, a second one not, and a social address that
    # gets one post in on the "hiring" keyword
    mail("jobs-noreply@linkedin.com", "Software Engineer at Globex"),
    mail("jobs-listings@linkedin.com", "Backend Engineer roles you may like"),
    mail("jobs-listings@linkedin.com", "New opportunities: Node developer"),
    mail("updates-noreply@linkedin.com", "Louise posted: WE'RE HIRING!!"),
    mail("updates-noreply@linkedin.com", "Maria commented on your post"),
    mail("updates-noreply@linkedin.com", "You appeared in 9 searches this week"),
    mail("updates-noreply@linkedin.com", "Congratulate Ana on 3 years at Initech"),
    # a recruiter's post is a post, not an alert: "Recruiter" once made this
    # address a suggested job board on a real inbox
    mail("updates-noreply@linkedin.com", "Ann Preolco - Senior Technical Recruiter posted: Happy Friday!"),
    mail("updates-noreply@linkedin.com", "Ann Preolco - Senior Technical Recruiter posted: New week"),
    # a PH board on a two-part suffix
    mail("noreply@alerts.jobsdb.com.ph", "Jobs for you: web developer"),
    mail("noreply@alerts.jobsdb.com.ph", "Web Developer vacancies in Makati"),
    # personal mail about jobs, from a freemail address
    mail("friend@gmail.com", "job opening at my company?"),
    mail("friend@gmail.com", "re: the developer role"),
    # the user writing to themselves
    mail(OWN, "job links to read later"),
    mail(OWN, "more job links"),
    # not jobs
    mail("notifications@github.com", "[kimlj/jobsift] Run failed: tests - main"),
    mail("noreply@glassdoor.com", "Have you ever been fired?"),
    mail("noreply@glassdoor.com", "What is the worst interview you had?"),
    # a newsletter that said "careers" once in four issues
    mail("news@techweekly.io", "This week: careers in Rust"),
    mail("news@techweekly.io", "This week: WebGPU lands"),
    mail("news@techweekly.io", "This week: the state of JS"),
    mail("news@techweekly.io", "This week: SQLite everywhere"),
]

failures = 0


def check(label, ok, detail=""):
    global failures
    if not ok:
        failures += 1
    print(f"  {'ok  ' if ok else 'FAIL'}  {label:<52} {detail}")


print("base domains\n" + "-" * 78)
for host, want in [("mail.kalibrr.com", "kalibrr.com"), ("alerts.jobsdb.com.ph", "jobsdb.com.ph"),
                   ("linkedin.com", "linkedin.com"), ("a.b.co.uk", "b.co.uk"),
                   ("jobalert.indeed.com", "indeed.com")]:
    check(f"{host} -> {want}", base_domain(host) == want, base_domain(host))

print("\nthe report\n" + "-" * 78)
report = suggest(INBOX, KNOWN, KEYWORDS, own_address=OWN)
found = {s["base"]: s for s in report["add"]}
keys = [key for s in report["add"] for key in s["keys"]]

check("the unknown board, as its base domain", found.get("kalibrr.com", {}).get("keys") == ["kalibrr.com"],
      found.get("kalibrr.com", {}).get("keys"))
check("labelled after the board", found.get("kalibrr.com", {}).get("label") == "kalibrr")
check("a two-part suffix keeps the name", found.get("jobsdb.com.ph", {}).get("keys") == ["jobsdb.com.ph"],
      found.get("jobsdb.com.ph", {}).get("keys"))
check("LinkedIn by address, never the whole domain",
      found.get("linkedin.com", {}).get("keys") == ["jobs-listings@linkedin.com"],
      found.get("linkedin.com", {}).get("keys"))
check("a known board is not suggested again", not any("indeed" in k for k in keys), keys)
check("personal Gmail is never suggested", not any("gmail" in k for k in keys), keys)
check("the user's own address is not counted", report["messages"] == len(INBOX) - 2,
      report["messages"])
check("github, glassdoor and the newsletter are not boards",
      not {"github.com", "glassdoor.com", "techweekly.io"} & set(found), sorted(found))
check("a recruiter's post does not make a job board",
      "updates-noreply@linkedin.com" not in keys, keys)
check("each address of a split domain gets its own line",
      [row[0] for row in found.get("linkedin.com", {}).get("addresses", [])]
      == ["jobs-listings@linkedin.com"])
check("the post let in by 'hiring' is reported",
      [s["address"] for s in report["leaky"]] == ["updates-noreply@linkedin.com"],
      [s["address"] for s in report["leaky"]])
check("a known board that sent nothing is named", report["silent"] == ["foundit.com"],
      report["silent"])

print("\nthe lines it prints\n" + "-" * 78)
text = render(report, 30)
paste = [line.strip() for line in text.splitlines()
         if line.startswith("    ") and not line.startswith("     ") and ": " in line]
pasted = yaml.safe_load("\n".join(paste)) or {}
check("they parse as YAML", isinstance(pasted, dict), paste)
check("and say exactly the suggestions",
      pasted == {"kalibrr.com": "kalibrr", "jobsdb.com.ph": "jobsdb",
                 "jobs-listings@linkedin.com": "linkedin"}, pasted)
check("they are what a yes would write", pasted == lines_to_add(report), lines_to_add(report))
check("it says nothing was changed", text.rstrip().endswith("Nothing was changed."))
check("but not when a question follows",
      not render(report, 30, ask=True).rstrip().endswith("Nothing was changed."))

again = suggest(INBOX, {**KNOWN, **pasted}, KEYWORDS, own_address=OWN)
check("pasted in, the next report suggests nothing", again["add"] == [],
      [s["keys"] for s in again["add"]])

print("\ntaking a sender back with `ignore`\n" + "-" * 78)
IGNORED = {**KNOWN, "kalibrr.com": "ignore"}
check("an ignored sender's mail is not a job email",
      classify("alerts@mail.kalibrr.com", "Python Engineer", IGNORED, KEYWORDS) == (False, "ignore"))
check("not even with a keyword in the subject",
      classify("alerts@mail.kalibrr.com", "5 new jobs for you", IGNORED, KEYWORDS)[0] is False)
for order, senders in [("address first", {"jobs-noreply@linkedin.com": "linkedin", "linkedin.com": "ignore"}),
                       ("domain first", {"linkedin.com": "ignore", "jobs-noreply@linkedin.com": "linkedin"})]:
    check(f"the longer key wins ({order})",
          classify("jobs-noreply@linkedin.com", "Engineer", senders, KEYWORDS) == (True, "linkedin")
          and classify("updates-noreply@linkedin.com", "hiring!", senders, KEYWORDS)[0] is False)
quiet = suggest(INBOX, IGNORED, KEYWORDS, own_address=OWN)
check("an ignored board is not suggested again",
      "kalibrr.com" not in {s["base"] for s in quiet["add"]}, [s["base"] for s in quiet["add"]])
check("nor named as a known sender that sent nothing", "kalibrr.com" not in quiet["silent"],
      quiet["silent"])

scratch = Path(tempfile.mkdtemp(prefix="jobsift-senders-"))
try:
    print("\none keypress: writing config.yaml\n" + "-" * 78)
    cfg = scratch / "config.yaml"
    shutil.copy("config.example.yaml", cfg)
    before = cfg.read_text(encoding="utf-8")
    entries = lines_to_add(report)
    added = add_to_config(cfg, {**entries, "indeed.com": "not_indeed"})
    after = cfg.read_text(encoding="utf-8")
    old, new = yaml.safe_load(before), yaml.safe_load(after)

    def comments(text):
        return [line for line in text.splitlines() if line.strip().startswith("#")]

    missing = sorted(k for k in entries if k not in old["known_senders"])
    check("it writes the suggestions the file lacks", sorted(added) == missing, (added, missing))
    check("(the example already has kalibrr.com)", "kalibrr.com" not in added)
    check("they load back as known_senders",
          all(new["known_senders"].get(k) == v for k, v in entries.items()))
    check("a key config.yaml has is left as it was", new["known_senders"]["indeed.com"] == "indeed")
    check("every comment is kept", comments(before) == comments(after))
    check("nothing outside known_senders moved",
          {k: v for k, v in old.items() if k != "known_senders"}
          == {k: v for k, v in new.items() if k != "known_senders"})
    check("said yes again, it adds nothing", add_to_config(cfg, entries) == [])
    check("and the next report is quiet",
          suggest(INBOX, new["known_senders"], KEYWORDS, own_address=OWN)["add"] == [])

    crlf = scratch / "crlf.yaml"
    crlf.write_bytes(before.replace("\n", "\r\n").encode("utf-8"))
    add_to_config(crlf, entries)
    raw = crlf.read_bytes()
    check("a Windows (CRLF) config stays CRLF", raw.count(b"\n") == raw.count(b"\r\n"))

    bare = scratch / "bare.yaml"
    bare.write_text("poll_interval_seconds: 300\n", encoding="utf-8")
    try:
        add_to_config(bare, entries)
        refused = False
    except SetupError:
        refused = True
    check("no known_senders section: refused, not guessed", refused)
    check("and the file is untouched", bare.read_text(encoding="utf-8") == "poll_interval_seconds: 300\n")

    print("\nby itself: what is sure enough to add\n" + "-" * 78)
    check("only the board with 3 alerts and nothing else",
          [p["key"] for p in automatic(report, KNOWN)] == ["kalibrr.com"],
          [p["key"] for p in automatic(report, KNOWN)])
    MIXED = ([mail("jobs@boardx.io", f"New jobs for developer {n}") for n in range(3)]
             + [mail("news@boardx.io", f"Our quarterly update {n}") for n in range(3)]
             + [mail("digest@careerblog.net", f"Careers: {n} roles open") for n in range(3)]
             + [mail("digest@careerblog.net", f"Tips for your CV {n}") for n in range(2)])
    mixed = suggest(MIXED, {}, KEYWORDS)
    picked = [p["key"] for p in automatic(mixed, {})]
    check("a board's jobs address, not its newsletter", picked == ["jobs@boardx.io"], picked)
    check("3 of 5 is suggested, but not added unasked",
          "careerblog.net" in {s["base"] for s in mixed["add"]} and "careerblog.net" not in picked)

    print("\nby itself: the daily check\n" + "-" * 78)
    db = scratch / "data" / "jobs.db"
    learned = scratch / "data" / "learned_senders.yaml"
    reads = []

    def fetch(days):
        reads.append(days)
        return INBOX

    t0 = datetime(2026, 9, 11, 8, 0, tzinfo=timezone.utc)
    first = learn(learned, fetch, dict(KNOWN), KEYWORDS, OWN, now=t0) or []
    check("the first check adds the clear board", [p["key"] for p in first] == ["kalibrr.com"],
          [p["key"] for p in first])
    check("from 30 days of headers", reads == [30], reads)
    state = read_learned(learned)
    check("it is kept in data/learned_senders.yaml", state["senders"] == {"kalibrr.com": "kalibrr"},
          state["senders"])
    check("with the reason", "3 of 3" in state["why"].get("kalibrr.com", ""), state["why"])
    check("and how to take it back", "kalibrr.com: ignore" in learned.read_text(encoding="utf-8"))
    check("not due again within the day",
          learn(learned, fetch, dict(KNOWN), KEYWORDS, OWN, now=t0 + timedelta(hours=23)) is None
          and len(reads) == 1, reads)

    merged = known_senders_for({"known_senders": KNOWN}, str(db))
    check("the senders the service loads include it", merged.get("kalibrr.com") == "kalibrr")
    check("a day later nothing is added twice",
          learn(learned, fetch, merged, KEYWORDS, OWN, now=t0 + timedelta(hours=25)) == [])

    taken = known_senders_for({"known_senders": IGNORED}, str(db))
    check("config.yaml's ignore beats the learned file", taken.get("kalibrr.com") == "ignore")
    check("so its mail is not read",
          classify("alerts@mail.kalibrr.com", "5 new jobs", taken, KEYWORDS)[0] is False)
    check("and the next check does not add it back",
          learn(learned, fetch, taken, KEYWORDS, OWN, now=t0 + timedelta(hours=50)) == [])

    message = notice(first)
    check("the Telegram notice names it", "kalibrr.com" in message and "3 of 3" in message)
    check("and says how to undo it", "kalibrr.com: ignore" in message)
finally:
    shutil.rmtree(scratch, ignore_errors=True)

print("\nreading the server's answer\n" + "-" * 78)
encoded = base64.b64encode("✅ New jobs for you".encode()).decode()
rows = [
    (b"1 (UID 101 BODY[HEADER.FIELDS (FROM SUBJECT)] {60}",
     b"From: Kalibrr <ALERTS@Mail.Kalibrr.com>\r\nSubject: 5 new jobs for you\r\n\r\n"),
    b")",
    (b"2 (UID 102 BODY[HEADER.FIELDS (FROM SUBJECT)] {90}",
     b"From: =?UTF-8?B?Sm9ic3RyZWV0?= <noreply@jobstreet.com>\r\n"
     b"Subject: =?UTF-8?B?" + encoded.encode() + b"?=\r\n\r\n"),
    b")",
    (b"3 (UID 103 BODY[HEADER.FIELDS (FROM SUBJECT)] {30}", b"Subject: no sender at all\r\n\r\n"),
    b")",
]
parsed = parse_header_rows(rows)
check("one entry per message that has a sender", len(parsed) == 2, parsed)
check("the address is lower-cased", parsed and parsed[0]["from"] == "alerts@mail.kalibrr.com",
      parsed and parsed[0]["from"])
check("an encoded subject is decoded",
      len(parsed) > 1 and parsed[1]["subject"] == "✅ New jobs for you",
      len(parsed) > 1 and parsed[1]["subject"])

print("\n" + ("-" * 78) +
      f"\n{'all cases behaved' if not failures else f'{failures} FAILED'}")
raise SystemExit(1 if failures else 0)
