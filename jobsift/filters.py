"""Hard filters applied before a job reaches the LLM.

Scoring is a soft signal: salary is only 40 of 100 points, so a low-paying job
with a strong skills match still clears the alert threshold. These are the hard
rules — a job that fails one is dropped outright, whatever it would have scored,
and it is dropped BEFORE enrich/score so it costs nothing.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# Rough PHP per USD. Only used to compare a USD listing against a PHP floor —
# it does not need to be a live rate, just the right order of magnitude.
USD_TO_PHP = 58.0

# Hours assumed per month when a listing quotes an hourly rate. 160 = 40h/week.
# Part-time remote work is commonly 15-20h/week, which is why this is tunable and
# why a listing tagged part-time uses the smaller figure.
WEEKS_PER_MONTH = 4.0
DEFAULT_HOURS_FULL_TIME = 40.0 * WEEKS_PER_MONTH   # 160
DEFAULT_HOURS_PART_TIME = 20.0 * WEEKS_PER_MONTH   # 80

# "30 hrs/week", "20-30 hours per week", "35 hours weekly", "25 hrs pw".
# The week qualifier is required, so a bare "40 hours" cannot match.
_HOURS_PER_WEEK_RE = re.compile(
    r"(\d{1,2})\s*(?:-|to|–)?\s*(\d{1,2})?\s*\+?\s*(?:hours?|hrs?)\b"
    r"[^.;]{0,14}?(?:per\s*week|/\s*wk|/\s*week|a\s*week|each\s*week|weekly|pw)\b",
    re.IGNORECASE,
)


def hours_per_week_from_text(text: str) -> float | None:
    """Hours a listing explicitly states per week, if any.

    A range takes its LOW end, matching how salary ranges are read: "20-30 hours"
    is a commitment that may only be 20.
    """
    if not text:
        return None
    match = _HOURS_PER_WEEK_RE.search(text)
    if not match:
        return None
    values = [float(g) for g in match.groups() if g]
    values = [v for v in values if 1 <= v <= 80]
    return min(values) if values else None

_PERIOD_TO_MONTHLY = {
    "hour": DEFAULT_HOURS_FULL_TIME,
    "hr": DEFAULT_HOURS_FULL_TIME,
    "day": 22.0,
    "week": 4.33,
    "month": 1.0,
    "mo": 1.0,
    "year": 1 / 12,
    "annum": 1 / 12,
}


def normalize_salary_php(
    salary: str,
    job_type: str = "",
    hours_per_month: float | None = None,
    text: str = "",
) -> float | None:
    """Best-effort monthly-PHP figure for a listing's salary string.

    Returns the LOW end of a range — "PHP 18,000 - PHP 40,000" is a job that may
    pay 18,000, so that is what a minimum-salary rule has to judge. Returns None
    when the string carries no usable number, which callers treat as "unknown"
    rather than "zero".
    """
    if not salary:
        return None
    cleaned = salary.replace(",", "").replace("–", "-").replace("—", "-")
    low = cleaned.lower()

    nums = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", cleaned)]
    nums = [n for n in nums if n > 0]
    if not nums:
        return None

    # "20-25k" / "25k" — k suffix multiplies
    if re.search(r"\d\s*k\b", low):
        nums = [n * 1000 if n < 1000 else n for n in nums]

    if "$" in cleaned or "usd" in low:
        rate = USD_TO_PHP
    elif "₱" in cleaned or "php" in low or re.search(r"\bp\d", low):
        rate = 1.0
    else:
        # No currency marker. PHP listings routinely write "25k-40k" bare, and
        # this project's boards are PH-centric, so assume PHP rather than guess.
        rate = 1.0

    hours = hours_per_month
    if hours is None:
        # Prefer what the listing actually says over any assumption.
        stated = hours_per_week_from_text(" ".join([text or "", salary or ""]))
        if stated:
            hours = stated * WEEKS_PER_MONTH
        elif "part" in (job_type or "").lower():
            hours = DEFAULT_HOURS_PART_TIME
        else:
            hours = DEFAULT_HOURS_FULL_TIME

    period = 1.0
    for key, mult in _PERIOD_TO_MONTHLY.items():
        if re.search(rf"(?:per|an|a|/)\s*{key}\b", low) or re.search(rf"\b{key}ly\b", low):
            period = hours if key in ("hour", "hr") else mult
            break

    return min(nums) * rate * period


def _company_blocked(company: str, patterns: list[str]) -> str | None:
    name = (company or "").lower()
    if not name:
        return None
    for pat in patterns:
        pat = (pat or "").strip().lower()
        if pat and pat in name:
            return pat
    return None


def _title_blocked(title: str, patterns: list[str]) -> str | None:
    text = (title or "").lower()
    for pat in patterns:
        pat = (pat or "").strip().lower()
        # Whole word, so "jr" does not fire on "jrxyz" and "intern" does not
        # fire on "international".
        if pat and re.search(rf"(?<!\w){re.escape(pat)}(?!\w)", text):
            return pat
    return None


def check(job: dict, settings: dict) -> tuple[bool, str]:
    """Return (keep, reason). reason is only meaningful when keep is False."""
    if not settings:
        return True, ""

    blocked = _company_blocked(job.get("company"), settings.get("exclude_companies") or [])
    if blocked:
        return False, f"company matches {blocked!r}"

    blocked = _title_blocked(job.get("title"), settings.get("exclude_titles") or [])
    if blocked:
        return False, f"title matches {blocked!r}"

    floor = settings.get("min_salary_php")
    if floor:
        value = normalize_salary_php(
            job.get("salary") or "",
            job_type=job.get("job_type") or "",
            hours_per_month=settings.get("hours_per_month"),
            text=" ".join(
                [
                    job.get("title") or "",
                    job.get("description_summary") or job.get("description") or "",
                ]
            ),
        )
        if value is None:
            if settings.get("drop_when_salary_unknown"):
                return False, "no salary listed"
        elif value < float(floor):
            return False, f"salary ~{value:,.0f} below {float(floor):,.0f}"

    return True, ""
