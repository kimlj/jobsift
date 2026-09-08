"""Read application confirmations out of the inbox and mark those jobs applied.

The tick box in the sheet is the user's, and keeping it by hand is the part of
this that gets abandoned. The boards already say when an application went in —
they mail a receipt — and jobsift already reads that inbox, so the tick can come
from mail that is addressed to the user rather than from scraping a logged-in
page on the board. No new access, no terms to breach, and no LLM call: every
rule below is a regex over text already fetched.

**What the rules were written from.** Each one is a template observed in a real
inbox, not a guess at what a board might send:

| board | evidence | carries |
|---|---|---|
| Indeed | 109 mails, `indeedapply@indeed.com`, subject `Indeed Application: <title>` | title only |
| Jobstreet | 65 mails, subject `Your application was successfully submitted`, body names both | title + company |
| Jobstreet | 99 mails, subject `<company> has viewed your application for <title>` | title + company |

The third is not a receipt — an employer viewing an application is a later,
separate event, and plenty of applications are never viewed. It is kept because
it only ever fires in one direction: nobody can view an application that was not
submitted, so it can add a tick and can never remove one. Treat it as partial
coverage, not as the ledger.

onlinejobs.ph has no rule because there is no evidence for one either way — the
inbox it was checked against had never applied there. Add an entry below the day
a receipt shows up; the shape of the list is the whole interface.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from .utils import normalize_company, normalize_title

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Confirmation:
    """One board saying an application went in."""

    title: str
    company: str  # "" when the mail does not name one — Indeed never does
    board: str
    subject: str
    receipt: bool  # False for the weaker "employer viewed it" signal
    sender: str = ""  # the From address, so a one-word company can be corroborated


# Senders worth fetching at all. Used ONLY to build the --scan-applied IMAP
# search, which reaches back 90 days over All Mail and would otherwise download
# everything. The normal pass does not use this: it runs over the messages
# `fetch_recent` already pulled, which is all recent mail, so a rule with no host
# below sees every sender without anything being listed here.
CONFIRMATION_SENDERS = ("indeedapply@indeed.com", "jobstreet.com")

# Applications do not come back from the board you applied through. Both of the
# receipts that prompted this arrived from the employer's ATS instead - a role
# found on Working Nomads was confirmed by JazzHR - and no allowlist can keep up
# with every ATS, let alone employers mailing from their own domain. So these
# rules carry no host and match on phrasing alone, and `match_to_jobs` supplies
# the safety by refusing anything whose company is not already a stored job.
_ANY_SENDER = ""

# What --scan-applied asks the IMAP server to find, since those rules name no
# sender to search on. One literal fragment per host-less rule above, short
# enough to survive the template being reworded around it. These only decide
# what gets DOWNLOADED; `detect` still has to match, and `match_to_jobs` still
# has to find the job, so a loose phrase costs bandwidth rather than a wrong tick.
CONFIRMATION_PHRASES = ("received your application", "your interest in joining")

# (board, sender substring, where to look, pattern). The pattern names its groups:
# `title` is required, `company` optional.
#
# Searched against whitespace-collapsed text, because both boards wrap these
# lines mid-sentence — "successfully submitted to KPMG\nR.G. Manabat & Co." is
# one company, and a pattern anchored to a line would find neither field.
_RULES: list[tuple[str, str, str, re.Pattern, bool]] = [
    (
        "indeed",
        "indeedapply@indeed.com",
        "subject",
        re.compile(r"^Indeed Application:\s*(?P<title>.+?)\s*$", re.I),
        True,
    ),
    (
        "jobstreet",
        "jobstreet.com",
        "body",
        # First match only: the same mail lists "similar jobs you might like"
        # further down, and those are other people's openings, not applications.
        # The company runs to the end of the sentence, and cannot be terminated
        # at the first full stop: "KPMG R.G. Manabat & Co.." holds three, and
        # stopping at one yields "KPMG R". It ends at whatever comes next
        # instead. If Jobstreet rewrites all of those the rule stops matching
        # rather than starts matching wrongly, which is the right way round for
        # the one column the program fills in on the user's behalf.
        #
        # Both groups are LENGTH-CAPPED, and that is load-bearing rather than
        # tidiness. With an open `.+?` on the title, a body whose sentence is
        # followed by none of the terminators backtracks: the title grows across
        # the whole email until a LATER "was successfully submitted to" lets the
        # company reach the end. A real receipt did exactly that and produced a
        # 900-character title. A cap makes that failure a non-match instead.
        #
        # %% and http:// are terminators because the live template puts
        # "%%str_to_replace_open_tracking%%" and a tracking URL immediately after
        # the sentence, before the prose the earlier version anchored on.
        re.compile(
            r"your application for\s+(?P<title>.{1,150}?)\s+was successfully submitted\s+to\s+"
            r"(?P<company>.{1,120}?)\s*(?=Each employer|Keep track|%%|https?://|$)",
            re.I,
        ),
        True,
    ),
    (
        "jobstreet",
        "jobstreet.com",
        "subject",
        re.compile(
            r"^(?P<company>.+?)\s+has viewed your application for\s+(?P<title>.+?)\s*$", re.I
        ),
        False,
    ),
    (
        # Greenhouse. Observed: no-reply@us.greenhouse-mail.io, subject "We
        # received your application for the Experienced Software Engineer role
        # at Automattic". The best-shaped receipt in the inbox - title and
        # company both, in the subject - and it is the ATS rather than the board,
        # so the same rule covers every employer hosted on it.
        "ats",
        _ANY_SENDER,
        "subject",
        re.compile(
            r"we(?:'ve|\s+have)?\s*received your application for(?:\s+the)?\s+"
            r"(?P<title>.{1,150}?)\s+role\s+at\s+(?P<company>.{1,120}?)\s*$",
            re.I,
        ),
        True,
    ),
    (
        # JazzHR. Observed: noreply@applytojob.com, subject "Kim, we've received
        # your resume", body "Thank you for your interest in joining Bamboo
        # Works." The subject names no job at all, so this is company-only and
        # relies on the stored shortlist to say which role it meant.
        "ats",
        _ANY_SENDER,
        "body",
        re.compile(
            r"thank you for your interest in joining\s+(?P<company>.{1,120}?)\s*[.!]",
            re.I,
        ),
        True,
    ),
]

_WS = re.compile(r"\s+")


def _flat(text: str) -> str:
    return _WS.sub(" ", text or "").strip()


def detect(message: dict) -> Confirmation | None:
    """The confirmation in one message, or None if it is not one.

    Ordinary job-alert mail from the same senders falls through: every pattern
    demands a phrase that only a confirmation carries. "X is still accepting
    applications" and a job titled "Application Engineer" both reach here and
    both fail, which is why the rules are phrases rather than keywords.

    That property is what lets the ATS rules run with no host at all. A rule
    that matched keywords could not be turned loose on every sender in the
    mailbox; one that demands "we received your application for X role at Y" can,
    because a job alert never says it. The remaining risk is a confirmation for
    a job that was never stored, and `match_to_jobs` drops those.

    A confirmation may name a company and no title. JazzHR's receipt does: the
    subject is "Kim, we've received your resume" and the body names only the
    employer. Requiring a title here would have thrown it away after matching.
    """
    sender = (message.get("from") or "").lower()
    subject = _flat(message.get("subject"))
    body = _flat(message.get("text"))

    for board, host, where, pattern, receipt in _RULES:
        # An empty host is deliberate, not a bug: that rule applies to any sender.
        if host and host not in sender:
            continue
        match = pattern.search(subject if where == "subject" else body)
        if not match:
            continue
        groups = match.groupdict()
        title = (groups.get("title") or "").strip()
        # Jobstreet ends the sentence with a full stop, and a company whose own
        # name ends in one ("… & Co.") then arrives with two.
        company = (groups.get("company") or "").strip().rstrip(".").strip()
        # One or the other is enough. Neither is not: a phrase with nothing to
        # identify cannot be matched to a job and would only be noise.
        if not title and not company:
            continue
        return Confirmation(title, company, board, subject, receipt, sender)
    return None


def match_to_jobs(confirmations, records) -> tuple[dict[str, Confirmation], list[Confirmation]]:
    """Map confirmations onto stored jobs by url. Returns (matched, unmatched).

    Matching is on the normalised title, and on the company too when the mail
    names one — the same pair `job_key` already dedups on, so a confirmation and
    the listing it refers to agree for the same reason two listings of one job do.

    A title-only confirmation that fits more than one stored job is left
    unmatched rather than guessed at. "Full-Stack Developer" is not rare, and a
    tick on the wrong row is worse than no tick: it hides a job the user never
    applied to, silently, and the tick is the one column the program promises not
    to invent.

    A COMPANY-ONLY confirmation gets the same treatment for the same reason, and
    it is also where the sender-agnostic ATS rules are made safe: a phrase that
    matched on any sender only ticks something if the employer it names is
    already a stored job, and only if exactly one of them is.

    One extra guard on short names. Of 190 stored companies, 17 normalise to a
    single word of six characters or less - "atos", "quora", "sgs" - and those
    are words that turn up in mail having nothing to do with an application. For
    those the sender's own domain has to corroborate the name. A longer or
    multi-word company is distinctive enough to stand alone.
    """
    index: dict[tuple[str, str], list[dict]] = {}
    by_title: dict[str, list[dict]] = {}
    by_company: dict[str, list[dict]] = {}
    for record in records:
        title = normalize_title(record.get("job_title"))
        # employer_name is what the POSTING called itself, and on the boards that
        # hide the employer it is the only one of the two that says anything.
        company = normalize_company(
            record.get("company") or record.get("employer_name") or ""
        )
        if company:
            by_company.setdefault(company, []).append(record)
        if not title:
            continue
        index.setdefault((title, company), []).append(record)
        by_title.setdefault(title, []).append(record)

    matched: dict[str, Confirmation] = {}
    unmatched: list[Confirmation] = []
    for confirmation in confirmations:
        title = normalize_title(confirmation.title)
        company = normalize_company(confirmation.company)
        candidates: list[dict] = []
        if title and company:
            candidates = index.get((title, company), [])
        if not candidates and title:
            hits = by_title.get(title, [])
            # Only when it is unambiguous; see the docstring.
            candidates = hits if len(hits) == 1 else []
        if not candidates and not title and company:
            hits = by_company.get(company, [])
            if len(company) <= 6 and " " not in company:
                # Too short to stand on its own; the sender has to agree.
                domain = (confirmation.sender or "").split("@")[-1]
                if company not in domain.replace("-", "").replace(".", ""):
                    hits = []
            candidates = hits if len(hits) == 1 else []

        urls = [r.get("url") for r in candidates if r.get("url") and r["url"] != "N/A"]
        if not urls:
            unmatched.append(confirmation)
            continue
        for url in urls:
            matched.setdefault(url, confirmation)
    return matched, unmatched
