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


# Senders worth fetching at all. Used to build the IMAP search, so a board that
# is not named here is never even downloaded.
CONFIRMATION_SENDERS = ("indeedapply@indeed.com", "jobstreet.com")

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
        # stopping at one yields "KPMG R". It ends where the template's next
        # sentence begins instead. If Jobstreet rewrites that sentence the rule
        # stops matching rather than starts matching wrongly, which is the right
        # way round for the one column the program fills in on the user's behalf.
        re.compile(
            r"your application for\s+(?P<title>.+?)\s+was successfully submitted\s+to\s+"
            r"(?P<company>.{1,120}?)\s*(?=Each employer|Keep track|$)",
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
    """
    sender = (message.get("from") or "").lower()
    subject = _flat(message.get("subject"))
    body = _flat(message.get("text"))

    for board, host, where, pattern, receipt in _RULES:
        if host not in sender:
            continue
        match = pattern.search(subject if where == "subject" else body)
        if not match:
            continue
        groups = match.groupdict()
        title = (groups.get("title") or "").strip()
        # Jobstreet ends the sentence with a full stop, and a company whose own
        # name ends in one ("… & Co.") then arrives with two.
        company = (groups.get("company") or "").strip().rstrip(".").strip()
        if not title:
            continue
        return Confirmation(title, company, board, subject, receipt)
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
    """
    index: dict[tuple[str, str], list[dict]] = {}
    by_title: dict[str, list[dict]] = {}
    for record in records:
        title = normalize_title(record.get("job_title"))
        company = normalize_company(record.get("company"))
        if not title:
            continue
        index.setdefault((title, company), []).append(record)
        by_title.setdefault(title, []).append(record)

    matched: dict[str, Confirmation] = {}
    unmatched: list[Confirmation] = []
    for confirmation in confirmations:
        title = normalize_title(confirmation.title)
        candidates: list[dict] = []
        if confirmation.company:
            candidates = index.get((title, normalize_company(confirmation.company)), [])
        if not candidates:
            hits = by_title.get(title, [])
            # Only when it is unambiguous; see the docstring.
            candidates = hits if len(hits) == 1 else []

        urls = [r.get("url") for r in candidates if r.get("url") and r["url"] != "N/A"]
        if not urls:
            unmatched.append(confirmation)
            continue
        for url in urls:
            matched.setdefault(url, confirmation)
    return matched, unmatched
