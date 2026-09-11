"""Which inbox senders look like job alerts that config.yaml does not know yet.

`python -m jobsift --suggest-senders`. This was the worst onboarding gap in the
project: a new user subscribes to a dozen boards and is then left to guess which
From addresses belong under `known_senders`. Here the inbox answers instead. Only
the sender and subject of recent mail are read (see GmailReader.fetch_headers),
nothing is marked read, and the output is the lines to paste.

It never edits config.yaml. A suggestion is a guess from subject lines, and a
wrong one costs money: every mail from a known sender goes to the extract model,
so a newsletter that says "careers" once a week would buy a call per issue. The
person reads the list and copies what they agree with.
"""

from __future__ import annotations

import re
from collections import defaultdict

from .classify import classify

# Subject words that suggest a job alert. Whole words, any case. Titles are here
# because on many boards the subject IS the title: "Python Developer at Acme".
JOB_WORDS = re.compile(
    r"\b(jobs?|hiring|vacanc(?:y|ies)|openings?|positions?|roles?|careers?|"
    r"opportunit(?:y|ies)|recruit(?:er|ers|ing|ment)?|developers?|engineers?|"
    r"programmers?)\b",
    re.I,
)

# An address qualifies when at least this share of its mail looks like jobs, and
# a board is suggested once its qualifying addresses sent at least this many. One
# job-shaped subject is a coincidence; a sender that keeps writing them is a board.
MIN_SHARE = 0.5
MIN_JOB_EMAILS = 2

# Mail providers anyone can have an address at. A job-shaped subject from one of
# these is a person - a recruiter, a friend forwarding a link - and that is exactly
# the mail that should not be handed to a model, so they are never suggested.
FREEMAIL = {"gmail", "googlemail", "yahoo", "ymail", "outlook", "hotmail", "live",
            "msn", "icloud", "me", "aol", "proton", "protonmail", "gmx", "yandex",
            "zoho", "mail"}

# Second-level names under a two-letter country code: jobsdb.com.ph, not com.ph.
_SECOND_LEVEL = {"com", "co", "net", "org", "gov", "edu", "ac", "or", "ne"}


def base_domain(host: str) -> str:
    """mail.kalibrr.com -> kalibrr.com; alerts.jobsdb.com.ph -> jobsdb.com.ph.

    known_senders matches by substring, so the base domain is what catches every
    subdomain a board sends from. A rough rule rather than the public suffix list:
    two labels, or three when the second-last is a generic second-level name
    under a country code.
    """
    labels = host.lower().strip(".").split(".")
    if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in _SECOND_LEVEL:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def looks_like_jobs(subject: str, keywords) -> bool:
    text = subject or ""
    lowered = text.lower()
    return bool(JOB_WORDS.search(text)) or any(k and k.lower() in lowered for k in keywords)


def suggest(headers: list[dict], known_senders: dict, keywords: list,
            own_address: str = "") -> dict:
    """Group recent mail by sender and say what known_senders is missing.

    Returns
      messages  how many headers were considered
      add       boards to add, each with the exact known_senders key(s) to paste
      leaky     senders that mostly send other mail but get a message through on
                a subject keyword, each one an extract call that finds nothing
      silent    known_senders entries that sent nothing in the window
    """
    own = (own_address or "").lower()
    stats: dict[str, dict] = defaultdict(
        lambda: {"total": 0, "job": 0, "caught": 0, "samples": []})
    known_hits = {key: 0 for key in known_senders}
    considered = 0

    for header in headers:
        sender = (header.get("from") or "").lower()
        if "@" not in sender or sender == own:
            continue
        considered += 1
        subject = header.get("subject") or ""
        for key in known_senders:
            if key.lower() in sender:
                known_hits[key] += 1
        # The same function the pipeline uses, so "already known" means what a
        # real pass would do with this mail, not an approximation of it.
        is_job, label = classify(sender, subject, known_senders, keywords)
        if is_job and label != "other":
            continue
        entry = stats[sender]
        entry["total"] += 1
        if is_job:
            entry["caught"] += 1
        if looks_like_jobs(subject, keywords):
            entry["job"] += 1
            if len(entry["samples"]) < 2:
                entry["samples"].append(subject)

    by_base: dict[str, list] = defaultdict(list)
    for address, entry in stats.items():
        by_base[base_domain(address.split("@", 1)[1])].append((address, entry))

    add, leaky = [], []
    for base, members in by_base.items():
        if base.split(".")[0] in FREEMAIL:
            continue
        qualifying = {address for address, e in members
                      if e["job"] and e["job"] / e["total"] >= MIN_SHARE}
        leaky += [{"address": address, **e} for address, e in members
                  if e["caught"] and address not in qualifying]
        chosen = [(a, e) for a, e in members if a in qualifying]
        if sum(e["job"] for _, e in chosen) < MIN_JOB_EMAILS:
            continue
        # The whole domain only when nothing else writes from it, and only when
        # config.yaml has not already narrowed that domain to one address. A key
        # like jobs-noreply@linkedin.com is somebody having learned that the
        # domain also sends messages and notifications, and suggesting
        # linkedin.com beside it would undo that.
        narrowed = any("@" in key and base_domain(key.split("@", 1)[1]) == base
                       for key in known_senders)
        whole = len(chosen) == len(members) and not narrowed
        add.append({
            "base": base,
            "keys": [base] if whole else sorted(a for a, _ in chosen),
            "label": re.sub(r"[^a-z0-9]+", "_", base.split(".")[0]).strip("_") or "other",
            "whole": whole,
            "total": sum(e["total"] for _, e in members),
            "job": sum(e["job"] for _, e in chosen),
            "caught": sum(e["caught"] for _, e in chosen),
            "samples": [s for _, e in chosen for s in e["samples"]][:2],
        })

    add.sort(key=lambda s: (-s["job"], s["base"]))
    leaky.sort(key=lambda s: (-s["caught"], s["address"]))
    return {"messages": considered, "add": add, "leaky": leaky,
            "silent": [key for key, hits in known_hits.items() if not hits]}


def _short(text: str, limit: int = 70) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


def render(report: dict, days: int) -> str:
    lines = [f"Read the sender and subject of {report['messages']} inbox email(s) from "
             f"the last {days} day(s). Nothing was opened or marked read."]

    add = report["add"]
    if add:
        lines += ["", f"These look like job alerts, but config.yaml does not know them ({len(add)}):"]
        for s in add:
            lines.append(f"  {s['base']:<30} {s['job']} of {s['total']} email(s) look like job alerts")
            for sample in s["samples"]:
                lines.append(f'      "{_short(sample)}"')
            if not s["whole"]:
                lines.append("      only from " + ", ".join(s["keys"])
                             + f"; the rest of {s['base']} sends other mail")
            if s["caught"]:
                lines.append(f"      {s['caught']} already get through on a subject keyword, "
                             "but only when the subject happens to match")
        lines += ["", "  Paste the ones you agree with under known_senders: in config.yaml:", ""]
        lines += [f"    {key}: {s['label']}" for s in add for key in s["keys"]]
    else:
        lines += ["", "No sender outside known_senders looks like a job board."]

    if report["leaky"]:
        lines += ["", "Let in by a subject keyword, though they mostly send other mail "
                      f"({len(report['leaky'])}):"]
        for s in report["leaky"]:
            lines.append(f"  {s['address']:<40} {s['caught']} of {s['total']} matched "
                         "job_subject_keywords")
        lines.append("  Each of those costs an AI call and usually finds no job. If that "
                     "is wrong for you, remove the phrase from job_subject_keywords.")

    if report["silent"]:
        lines += ["", f"In known_senders, but nothing from them in the last {days} day(s):",
                  "  " + ", ".join(report["silent"]),
                  "  Either no alert is set up on that site, or it goes to a different address."]

    lines += ["", "Nothing was changed."]
    return "\n".join(lines)
