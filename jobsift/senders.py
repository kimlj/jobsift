"""Which inbox senders look like job alerts that config.yaml does not know yet.

This was the worst onboarding gap in the project: a new user subscribes to a
dozen boards and is then left to guess which From addresses belong under
`known_senders`. Here the inbox answers instead, two ways:

* `python -m jobsift --suggest-senders` prints what it found and asks once
  whether to add it to config.yaml.
* The running service makes the same check once a day and adds, by itself, only
  what is clearly a job board, then says on Telegram what it added (`learn`).

Either way only the sender and subject of recent mail are read (see
GmailReader.fetch_headers) and nothing is marked read. The care is in what gets
added: every mail from a known sender goes to the extract model, so a newsletter
that says "careers" once a week, added by mistake, buys a call per issue.
"""

from __future__ import annotations

import os
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from .classify import IGNORE, classify

# Subject words that suggest a job alert. Whole words, any case. Titles are here
# because on many boards the subject IS the title: "Python Developer at Acme".
# "Recruiter" is deliberately not: on a real inbox (2026-09-11) it matched LinkedIn
# post notifications - "Ann, Senior Technical Recruiter posted: Happy Friday!" -
# and put updates-noreply@linkedin.com forward as a job board.
JOB_WORDS = re.compile(
    r"\b(jobs?|hiring|vacanc(?:y|ies)|openings?|positions?|roles?|careers?|"
    r"opportunit(?:y|ies)|developers?|engineers?|programmers?)\b",
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
    known_hits = {key: 0 for key, label in known_senders.items()
                  if str(label).lower() != IGNORE}
    considered = 0

    for header in headers:
        sender = (header.get("from") or "").lower()
        if "@" not in sender or sender == own:
            continue
        considered += 1
        subject = header.get("subject") or ""
        for key in known_hits:
            if key.lower() in sender:
                known_hits[key] += 1
        # The same function the pipeline uses, so "already known" means what a
        # real pass would do with this mail, not an approximation of it.
        is_job, label = classify(sender, subject, known_senders, keywords)
        if (is_job and label != "other") or label == IGNORE:
            # Known, or somebody has already said no to it.
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
            "narrowed": narrowed,
            "total": sum(e["total"] for _, e in members),
            "job": sum(e["job"] for _, e in chosen),
            "caught": sum(e["caught"] for _, e in chosen),
            "samples": [s for _, e in chosen for s in e["samples"]][:2],
            # One line each when the domain is split, so the addresses can be
            # judged apart: a real inbox put LinkedIn's job alerts and its post
            # notifications in one suggestion, where only one of them belonged.
            "addresses": sorted(((a, e["job"], e["total"], (e["samples"] or [""])[0])
                                 for a, e in chosen), key=lambda row: (-row[1], row[0])),
        })

    add.sort(key=lambda s: (-s["job"], s["base"]))
    leaky.sort(key=lambda s: (-s["caught"], s["address"]))
    return {"messages": considered, "add": add, "leaky": leaky,
            "silent": [key for key, hits in known_hits.items() if not hits]}


def _short(text: str, limit: int = 70) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


def render(report: dict, days: int, ask: bool = False) -> str:
    """The report as text. `ask` when a yes/no question follows it, which then
    says what was or was not changed."""
    lines = [f"Read the sender and subject of {report['messages']} inbox email(s) from "
             f"the last {days} day(s). Nothing was opened or marked read."]

    add = report["add"]
    if add:
        lines += ["", f"These look like job alerts, but config.yaml does not know them ({len(add)}):"]
        for s in add:
            lines.append(f"  {s['base']:<30} {s['job']} of {s['total']} email(s) look like job alerts")
            if s["whole"]:
                for sample in s["samples"]:
                    lines.append(f'      "{_short(sample)}"')
            else:
                for address, job, total, sample in s["addresses"]:
                    lines.append(f"      {address:<36} {job} of {total}")
                    if sample:
                        lines.append(f'          "{_short(sample, 62)}"')
                lines.append(f"      The rest of {s['base']} sends other mail, so judge "
                             "these one address at a time.")
            if s["caught"]:
                lines.append(f"      {s['caught']} already get through on a subject keyword, "
                             "but only when the subject happens to match")
        if ask:
            lines += ["", "  The lines that would go under known_senders: in config.yaml:", ""]
        else:
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

    if not ask:
        lines += ["", "Nothing was changed."]
    return "\n".join(lines)


# ── Adding them, one keypress ────────────────────────────────────────────────


def lines_to_add(report: dict) -> dict:
    """The known_senders entries a report suggests, key -> label."""
    return {key: s["label"] for s in report["add"] for key in s["keys"]}


def add_to_config(config_path, entries: dict) -> list:
    """Write entries under known_senders in config.yaml, keeping every comment.

    Only keys config.yaml does not already have; returns the keys written.
    Raises onboard.SetupError when the section cannot be edited safely (no
    known_senders block, or one written on a single line) and OSError when the
    file cannot be written - a read-only mount under Docker. Either way nothing
    was changed and the caller points at the lines to paste instead.
    """
    from .onboard import _read, _write, set_yaml_value

    path = Path(config_path)
    text = _read(path)
    have = (yaml.safe_load(text) or {}).get("known_senders") or {}
    added = []
    for key, label in entries.items():
        if key in have:
            continue
        text = set_yaml_value(text, ("known_senders", key), label, insert=True)
        added.append(key)
    if added:
        _write(path, text)
    return added


# ── Adding them by itself ────────────────────────────────────────────────────
#
# A new user should not have to know known_senders exists: they subscribe to a
# board, and a few alerts later jobsift is reading it. Nobody reads these before
# they cost money, so the bar is higher than for a suggestion, and they go in a
# file under data/ rather than in config.yaml. Under systemd the service can
# write only data/ and logs/ (ProtectSystem=strict, see install.sh), and a file
# of its own can be rewritten without touching a comment of the user's.

LEARNED_FILE = "learned_senders.yaml"
LEARN_EVERY_HOURS = 24
LEARN_DAYS = 30
# At least three job-shaped emails, and at least four in five of the sender's
# mail job-shaped. The newsletter in the dry run that says "careers" once in four
# issues is at a quarter; a real board, measured on the author's inbox, is at 1.0.
AUTO_MIN_JOB_EMAILS = 3
AUTO_MIN_SHARE = 0.8


def learned_path(database_path: str) -> Path:
    """Beside the database, so it lives wherever data/ does - a Docker volume too."""
    return Path(database_path).parent / LEARNED_FILE


def read_learned(path) -> dict:
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        data = {}
    return {"checked_at": data.get("checked_at"),
            "senders": dict(data.get("senders") or {}),
            "why": dict(data.get("why") or {})}


def _write_learned(path, state: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = ("# Job-alert senders jobsift found in your inbox and added by itself.\n"
            "# To take one back, do not delete it here - the next check would add it\n"
            "# again. Put it under known_senders in config.yaml with the label ignore:\n"
            "#   kalibrr.com: ignore\n"
            "# Switch the daily check off with learn_senders: false in config.yaml.\n"
            + yaml.safe_dump(state, sort_keys=False, allow_unicode=True))
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def automatic(report: dict, known_senders: dict) -> list[dict]:
    """The suggestions sure enough to add with nobody looking.

    Stricter than a suggestion three ways: more job-shaped emails, a higher share
    of them, and never at a domain config.yaml has narrowed to single addresses.
    Somebody curated that domain by hand, and a new address there is theirs to
    judge; --suggest-senders still shows it.
    """
    picked = []
    for s in report["add"]:
        if s["narrowed"]:
            continue
        rows = ([(s["base"], s["job"], s["total"], (s["samples"] or [""])[0])]
                if s["whole"] else s["addresses"])
        for key, job, total, sample in rows:
            if job >= AUTO_MIN_JOB_EMAILS and job / total >= AUTO_MIN_SHARE:
                picked.append({"key": key, "label": s["label"], "job": job,
                               "total": total, "sample": sample})
    return picked


def learn(path, fetch_headers, known_senders: dict, keywords: list,
          own_address: str = "", now: datetime | None = None):
    """Add clear job boards to the learned file, at most once per LEARN_EVERY_HOURS.

    Returns None when no check is due, else the senders added (usually none).
    `fetch_headers(days)` is GmailReader.fetch_headers, passed in so the dry run
    can hand it an invented inbox. known_senders should already include the
    learned ones (config.known_senders does), so nothing is added twice.
    """
    now = now or datetime.now(timezone.utc)
    state = read_learned(path)
    if state["checked_at"]:
        try:
            last = datetime.fromisoformat(str(state["checked_at"]))
        except ValueError:
            last = None
        if last is not None:
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            if now - last < timedelta(hours=LEARN_EVERY_HOURS):
                return None

    report = suggest(fetch_headers(LEARN_DAYS), known_senders, keywords, own_address)
    added = [p for p in automatic(report, known_senders)
             if p["key"] not in state["senders"] and p["key"] not in known_senders]
    for p in added:
        state["senders"][p["key"]] = p["label"]
        state["why"][p["key"]] = (f"added {now:%Y-%m-%d}: {p['job']} of {p['total']} emails "
                                  f"in {LEARN_DAYS} days looked like job alerts")
    state["checked_at"] = now.isoformat(timespec="seconds")
    _write_learned(path, state)
    return added


def notice(added: list[dict]) -> str:
    """The Telegram message for what `learn` added. Plain text, no markup."""
    lines = [f"jobsift found {len(added)} new job-alert sender(s) in your inbox and "
             "now reads their emails:"]
    for p in added:
        lines.append(f"• {p['key']} - {p['job']} of {p['total']} emails looked like job alerts")
        if p["sample"]:
            lines.append(f'   e.g. "{_short(p["sample"], 60)}"')
    lines += ["", "If one is not a job board, add it under known_senders in config.yaml "
                  "with the label ignore:", f"  {added[0]['key']}: ignore"]
    return "\n".join(lines)
