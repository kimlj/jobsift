"""Does --suggest-senders name the boards config.yaml is missing, and nothing else?

No network and no account. The inbox below is invented, in the shapes a real one
has, and the IMAP half is fed a canned server answer. What can go wrong is all
in the grouping, so that is what is attacked:

  * a board sending from a subdomain must be suggested as its base domain, and a
    board on a two-part suffix (jobsdb.com.ph) must not become `com.ph`,
  * a board config.yaml already knows must never come back as a suggestion,
  * a domain that also sends other mail - LinkedIn's messages and updates - must
    be suggested one address at a time, never as the whole domain,
  * personal mail about a job, from Gmail, must never be suggested at all,
  * one coincidental subject from a newsletter is not a job board,
  * and once the suggested lines are pasted, the report must go quiet: the
    lines it prints have to be the lines that work.

    python dryrun_suggest_senders.py
"""

import base64

import yaml

from jobsift.gmail import parse_header_rows
from jobsift.senders import base_domain, render, suggest

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
check("it says nothing was changed", text.rstrip().endswith("Nothing was changed."))

again = suggest(INBOX, {**KNOWN, **pasted}, KEYWORDS, own_address=OWN)
check("pasted in, the next report suggests nothing", again["add"] == [],
      [s["keys"] for s in again["add"]])

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
