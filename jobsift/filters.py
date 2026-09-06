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

# Fallback PHP per USD when config does not set filters.usd_to_php. Only used to
# compare a listing against a PHP floor, so it needs the right order of magnitude
# rather than a live rate.
USD_TO_PHP = 58.0

# PHP per unit of everything else. Same standard: order of magnitude, not a live
# rate, and overridable per-currency from config as filters.currency_rates.
#
# This exists because the remote feeds price in more than dollars. Measured over
# 600 live listings on 2026-09-01: himalayas quoted USD 141, CAD 3, EUR 3, PHP 2,
# INR 1, GBP 1; jobicy USD 99, EUR 9, CAD 4, GBP 3, ARS 1, MXN 1, PHP 1. Without a
# rate, "40000 EUR per year" fell through to the source's default currency, was read
# as 3,333 pesos a month and dropped for being underpaid — the same silent
# undervaluation that once binned the best onlinejobs.ph listings, in a new costume.
CURRENCY_TO_PHP = {
    "usd": USD_TO_PHP, "eur": 63.0, "gbp": 74.0, "cad": 42.0, "aud": 38.0,
    "nzd": 35.0, "sgd": 43.0, "hkd": 7.4, "chf": 68.0, "jpy": 0.39, "cny": 8.0,
    "inr": 0.69, "mxn": 3.1, "brl": 10.5, "ars": 0.04, "clp": 0.06,
    "zar": 3.2, "pln": 15.0, "czk": 2.5, "sek": 5.5, "nok": 5.4, "dkk": 8.4,
    "aed": 15.8, "sar": 15.5, "ils": 16.0, "myr": 13.0, "thb": 1.7,
    "idr": 0.0035, "vnd": 0.0023, "krw": 0.042, "twd": 1.8, "php": 1.0,
}

# TRY (Turkish lira) and COP (Colombian peso) are deliberately absent from the table
# above: both are ordinary English words, and "Try our benefits" or "cop" in a job
# description would set the rate to something 40x wrong without anything looking
# broken. Both countries are dropped by the geographic rule anyway.
#
# Only the unambiguous ones. "$" is deliberately absent: it is USD far more often
# than not on these boards, but CAD/AUD/SGD/NZD all write it too, so it is resolved
# after an explicit three-letter code has had its chance.
def set_usd_rate(rate: float) -> None:
    """Point every USD conversion at `rate`. Call once, at startup.

    Two places hold it, and setting only the obvious one silently does nothing:
    CURRENCY_TO_PHP is built at import and captures USD_TO_PHP by VALUE, so
    rebinding the constant afterwards leaves the table on the old number and
    every conversion keeps using it. That is what happened on the first live
    run - the log said 62.67 and the column was still computed at 58.
    """
    global USD_TO_PHP

    USD_TO_PHP = float(rate)
    CURRENCY_TO_PHP["usd"] = float(rate)


CURRENCY_SYMBOLS = {"₱": "php", "€": "eur", "£": "gbp", "¥": "jpy", "₹": "inr", "₩": "krw"}

# The words a Philippine employer actually types. "90000-150000 Peso" was being
# read as dollars: no three-letter code, no symbol, so it fell through to the
# source default of USD, then tripped the annual heuristic (90,000 USD a month is
# not a wage) and came out at 435,000 a month instead of 90,000.
#
# This is the opposite case to TRY and COP above. Those are excluded because they
# are ordinary English words that appear in prose; "peso" is not one, and on a
# board serving the Philippines it means this peso. The Mexican and Colombian ones
# are the theoretical collision, and the geography rules drop both countries.
CURRENCY_WORDS = {"peso": "php", "pesos": "php", "piso": "php", "pisos": "php"}

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

# "180,000" and "40.000" are both a number in the thousands; "31,2k" and "4.50" are
# both a number with a decimal part. Which one a separator means depends on how many
# digits follow it, and getting it wrong is not a rounding error:
#   * `.replace(",", "")` turned remotive's real "$31,2k- $52k" into $312k — a
#     tenfold OVERSTATEMENT that would have cleared the alert threshold on its own.
#   * "€40.000" read as 40.0 and "250 000 zl" read as 250 are the mirror image, and
#     silently drop a job for being underpaid.
# Three digits after the separator means thousands; one or two means a decimal.
_THOUSANDS_RE = re.compile(r"(?<=\d)[.,\u00a0 ](?=\d{3}(?!\d))")
_DECIMAL_COMMA_RE = re.compile(r"(?<=\d),(?=\d{1,2}(?!\d))")


def _normalize_separators(text: str) -> str:
    """Rewrite thousands and decimal separators into a plain 1234.5 form."""
    text = _DECIMAL_COMMA_RE.sub("\u0000", text)     # park real decimal commas
    text = _THOUSANDS_RE.sub("", text)                # drop thousands separators
    return text.replace("\u0000", ".")               # restore them as dots


# The band an ordinary monthly wage falls in, used ONLY to choose between two
# readings of an unmarked number. Not a filter — min_salary_php and
# max_salary_php are the filters, and a figure outside this band is still
# returned when it is the only sensible reading.
#
# The low end sits under PH minimum wage (~₱11,000/month); the high end is
# deliberately well below SANITY_CEILING_PHP, because a merely-not-absurd figure
# is not evidence. "40000-60000 per month" from a PH employer reads as
# ₱2,320,000 in dollars — under the sanity ceiling, and still obviously the
# wrong currency.
SALARY_TYPICAL_LOW = 15_000.0
SALARY_TYPICAL_HIGH = 500_000.0


def currency_rate(text: str, settings: dict | None = None, default: str = "PHP") -> float:
    """PHP per unit of whatever currency this salary string is quoted in.

    An explicit three-letter code wins over a symbol, because "$" is written by
    four currencies on these boards and "CAD 90,000" is unambiguous.
    """
    rates = {**CURRENCY_TO_PHP, **{
        str(k).lower(): float(v) for k, v in ((settings or {}).get("currency_rates") or {}).items()
    }}
    usd = (settings or {}).get("usd_to_php")
    if usd:
        rates["usd"] = float(usd)

    low = text.lower()
    for code in rates:
        if re.search(rf"(?<!\w){code}(?!\w)", low):
            return rates[code]
    for word, code in CURRENCY_WORDS.items():
        if re.search(rf"(?<!\w){word}(?!\w)", low):
            return rates.get(code, 1.0)
    for symbol, code in CURRENCY_SYMBOLS.items():
        if symbol in text:
            return rates.get(code, 1.0)
    if "$" in text:
        return rates["usd"]
    if re.search(r"\bp\d", low):        # "P50,000", a peso figure with no symbol
        return rates["php"]
    return rates["usd"] if str(default).upper() == "USD" else rates["php"]


_PERIOD_TO_MONTHLY = {
    "hour": DEFAULT_HOURS_FULL_TIME,
    "hr": DEFAULT_HOURS_FULL_TIME,
    "day": 22.0,
    "week": 4.33,
    "month": 1.0,
    "mo": 1.0,
    "year": 1 / 12,
    "annum": 1 / 12,
    # The JSON feeds label periods "annual" / "yearly" rather than "per year".
    "annual": 1 / 12,
}


# Which currency a bare, unmarked number means, per source. PH job boards quote
# pesos; onlinejobs.ph is an international marketplace that pays Filipinos in
# USD and routinely writes "1000+", "600" or "12-20/hr." with no symbol at all.
#
# Reading those as pesos threw away the best jobs on the board silently: "10/hr"
# scored as 1,600 PHP/month instead of 92,800, and "12-20/hr." as 1,920 instead
# of 111,360 - both then dropped for falling under a 50,000 floor they clear
# several times over. Nothing logged a problem, because nothing was wrong except
# the assumed unit.
BARE_NUMBER_CURRENCY = {
    "onlinejobs_ph": "USD", "virtualstaff_ph": "USD",
    # The remote feeds are international boards and quote dollars. remotive writes
    # "175k - 190k" with no symbol at all.
    "remotive": "USD", "workingnomads": "USD", "himalayas": "USD", "jobicy": "USD",
}

# When a listing states an amount but NO period, the SIZE of the result says which
# it was. "25k-40k" on a PH board is a monthly peso salary and always has been;
# "$150k - $230k" on a remote board is a year's pay, and reading it as monthly
# produced ₱8,700,000/month on a live remotive listing — a figure that clears every
# filter, scores a full 40/40 and alerts.
#
# The test is on the ANSWER rather than on the input, so it works in any currency:
# read the figure as monthly first, and if that monthly wage is implausible, it was
# an annual one. ₱400,000/month is about USD 6,900 a month or USD 83,000 a year —
# above what these boards carry monthly, and comfortably above the ₱240,000-350,000
# top of the real database, so no genuine monthly salary is re-read by mistake.
#
# This also covers pesos, which the earlier non-peso-only version did not: a PH
# listing saying "600,000" with no period means a year, and now reads as ₱50,000
# a month rather than as ₱600,000.
ANNUAL_IF_MONTHLY_EXCEEDS = 400_000.0

# Above this many pesos a month, the number is not one person's pay. It is a
# funding round, a payout total, or an employer typing a monthly salary into an
# hourly field — one stored Indeed row reads "PHP 30,000 - PHP 40,000 an hour",
# which normalises to ₱4,800,000/month and would alert on its own. Treated as
# UNKNOWN rather than as a huge salary, because the one thing it certainly is not
# is a reliable number.
#
# ₱2,500,000/month is about USD 517,000 a year. The first value tried here was
# ₱1,000,000 (~USD 207k/yr) and it was WRONG in the expensive direction: a live
# jobicy listing paying "230000 USD per year" was rejected as implausible and
# dropped for stating no salary at all. These boards genuinely carry US senior
# salaries above $200k, so a ceiling has to sit well clear of them. It still
# catches the cases it exists for — "$11M" reads as ₱53M/month and the hourly
# mix-up above as ₱4.8M.
SANITY_CEILING_PHP = 2_500_000.0


def currency_for(job: dict) -> str:
    """The currency an unmarked number means for this job's source."""
    return BARE_NUMBER_CURRENCY.get(str(job.get("source") or ""), "PHP")


def normalize_salary_php(
    salary: str,
    job_type: str = "",
    hours_per_month: float | None = None,
    text: str = "",
    usd_to_php: float | None = None,
    default_currency: str = "PHP",
    currency_rates: dict | None = None,
    end: str = "low",
) -> float | None:
    """Best-effort monthly-PHP figure for a listing's salary string.

    Returns the LOW end of a range — "PHP 18,000 - PHP 40,000" is a job that may
    pay 18,000, so that is what a minimum-salary rule has to judge. Returns None
    when the string carries no usable number, which callers treat as "unknown"
    rather than "zero".

    `end` picks which figure to read out, and only a caller with a different
    question should change it. A filter asks "could this pay too little", which is
    the low end. Answering "what should I ask for" is the opposite question, and
    the low end is the wrong answer to it — hence "high" and "mid". Every other
    step of the normalisation (currency, period, hourly conversion, the annual and
    sanity checks) is identical, which is the reason this is a parameter rather
    than a second function that would drift from this one.
    """
    if not salary:
        return None
    cleaned = _normalize_separators(
        salary.replace("–", "-").replace("—", "-")
    )
    low = cleaned.lower()

    # A bare "5 Hours" is a shift length typed into the salary box, not a rate.
    # Read as a salary it becomes 5 pesos and drops the job for being underpaid.
    # A real rate always carries "per hour" or "/hr" alongside the number.
    if re.fullmatch(r"\s*\d+(?:\.\d+)?\s*(?:hours?|hrs?)\s*", low):
        return None

    nums = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", cleaned)]
    nums = [n for n in nums if n > 0]
    if not nums:
        return None

    # "20-25k" / "25k" — k suffix multiplies
    if re.search(r"\d\s*k\b", low):
        nums = [n * 1000 if n < 1000 else n for n in nums]
    # "$11M" read as 11 is how "we have paid out over $11M to our engineers"
    # becomes a salary. Scaling it correctly is what lets the sanity ceiling
    # below reject it as not-a-wage instead of quietly accepting ₱638/month.
    if re.search(r"\d\s*m\b", low) and "month" not in low:
        nums = [n * 1_000_000 if n < 1_000_000 else n for n in nums]

    # Currency, in order: explicit three-letter code, then symbol, then — when the
    # listing marks nothing at all — what this SOURCE quotes in. PH email boards
    # write "25k-40k" meaning pesos; onlinejobs.ph writes "1000+" meaning dollars.
    # Guessing one global default is what broke this.
    settings_for_rate = {"usd_to_php": usd_to_php, "currency_rates": currency_rates}
    rate = currency_rate(cleaned, settings_for_rate, default=default_currency)

    # An UNMARKED number is a guess, and the guess can be checked. A board serving
    # the Philippines carries both conventions: jobicy published "700-1000 monthly"
    # for a PH content role meaning DOLLARS, and a PH employer writing
    # "40000-60000 monthly" means PESOS. One default is wrong for one of them —
    # read as USD, ₱40,000/month becomes ₱2,320,000 and looks like a staff role.
    #
    # So when nothing in the string names a currency, try the assumed one and fall
    # back to the other if the answer is not a wage anyone is paid. Same test as
    # the annual/monthly rule: decide by whether the result makes sense.
    marked = bool(re.search(r"[\$€£₱¥₹₩]", cleaned)) or bool(
        re.search(r"(?<!\w)(?:" + "|".join(CURRENCY_TO_PHP) + r")(?!\w)", low)
    )

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

    period = None
    for key, mult in _PERIOD_TO_MONTHLY.items():
        if re.search(rf"(?:per|an|a|/)\s*{key}\b", low) or re.search(rf"\b{key}ly\b", low):
            period = hours if key in ("hour", "hr") else mult
            break

    if end == "high":
        amount = max(nums)
    elif end == "mid":
        amount = (min(nums) + max(nums)) / 2.0
    else:
        amount = min(nums)
    if period is None:
        # Nothing stated: assume monthly, then reconsider if that is not a wage
        # anyone is paid. See ANNUAL_IF_MONTHLY_EXCEEDS.
        period = 1 / 12 if amount * rate > ANNUAL_IF_MONTHLY_EXCEEDS else 1.0

    monthly = amount * rate * period
    if not marked:
        # Try the other currency and keep whichever reading lands in the ordinary
        # wage band. Only when EXACTLY ONE does — if both are plausible the guess
        # cannot be improved, and if neither is (a genuine $190k/year, say) the
        # source default is still the better answer than a coin flip.
        alternate = float(usd_to_php or USD_TO_PHP) if rate == 1.0 else 1.0
        other = amount * alternate * period
        in_band = SALARY_TYPICAL_LOW <= monthly <= SALARY_TYPICAL_HIGH
        other_in_band = SALARY_TYPICAL_LOW <= other <= SALARY_TYPICAL_HIGH
        if other_in_band and not in_band:
            monthly = other

    # See SANITY_CEILING_PHP: implausible means unknown, not enormous.
    if monthly > SANITY_CEILING_PHP:
        return None
    return monthly


# ── Geography ───────────────────────────────────────────────────────────────
#
# Four rules, applied to every source before enrich/score:
#
#   1. The job is in the Philippines           -> keep, on-site/hybrid/remote alike.
#   2. Elsewhere, and remote                   -> keep only if a PH-based candidate
#                                                 is actually eligible.
#   3. Elsewhere, and needs physical presence  -> discard. Hybrid abroad means being
#                                                 there, so hybrid fails this too.
#   4. Location unknown or unrecognised        -> keep. Silently binning missing data
#                                                 is how jobs were lost before.
#
# Rule 2 is the one that matters. Run dryrun_geography.py over the live remote
# feeds and only 6-47% of "remote" listings are open to a PH candidate at all
# (2026-09-01: remotive 9/19, workingnomads 14/45, himalayas 43/300, jobicy 3/50).
# The rest read "USA", "Europe", "Time zone: CET (+/- 3 hours)". Every feed states
# that in a structured field, so no LLM call is needed to read it - and rule 2
# without that field is worthless.
#
# ORDER MATTERS, and not in the obvious direction. There are four tiers, and a term
# that positively names somewhere foreign is checked BEFORE a term that says the job
# is open:
#
#   1. exclude_locations  never overridable, from config
#   2. HOME_TERMS + HOME_REGION_TERMS   somewhere that CONTAINS the Philippines
#   3. FOREIGN_TERMS      somewhere that does not
#   4. OPEN_TERMS         no geography named at all
#
# A listing that states eligibility in its OWN field (candidate_location) is read
# strictly instead: tiers 1-3 as normal, then anything unrecognised is treated as a
# restriction rather than as missing data. The feed was explicit, so an unfamiliar
# country name is a closed door, not a gap in the lists below.
#
# Tier 3 sits above tier 4 because "Anywhere (working US business hours)" contains
# "anywhere" and would otherwise be rescued by it. Tier 2 sits above tier 3 because
# multi-region postings are common and one of the regions is often ours: Working
# Nomads' single largest bucket reads "Europe, North America, Latin America, APAC",
# which a PH candidate can take and a plain block on "america" would bin. That is
# the whole reason "apac"/"asia" live in tier 2 while "worldwide"/"anywhere" — which
# say nothing about where — stay in tier 4.

# A place the candidate already lives in. Being generous here is deliberate and
# safe: a wrong home match costs one scoring call, while a wrong drop loses the job
# in silence. Names that exist abroad more often than here ("Laguna", "Clark",
# "Naga") are left out - rule 4 keeps them anyway.
HOME_TERMS = [
    "philippines", "philippine", "filipino", "filipinos", "pinoy", "ph", "ncr",
    "manila", "makati", "taguig", "bgc", "bonifacio", "quezon city", "pasig",
    "ortigas", "mandaluyong", "san juan", "alabang", "muntinlupa", "paranaque",
    "parañaque", "pasay", "las pinas", "las piñas", "caloocan", "marikina",
    "valenzuela", "malabon", "navotas", "pateros", "antipolo", "rizal", "taytay",
    "cainta", "cavite", "bacoor", "dasmarinas", "imus", "bulacan", "pampanga",
    "angeles city", "subic", "batangas", "baguio", "tagaytay", "cebu", "mactan",
    "mandaue", "lapu-lapu", "davao", "iloilo", "bacolod", "dumaguete",
    "cagayan de oro", "zamboanga", "general santos", "eastwood", "ermita",
    "malate", "ugong",
]

# Tier 2 as well: a region the Philippines sits inside. Listed apart from HOME_TERMS
# only because these rescue a posting rather than describe where the candidate is,
# but they are checked at the same moment and for the same reason.
HOME_REGION_TERMS = [
    "apac", "asia", "asia pacific", "asia-pacific", "southeast asia",
    "south-east asia", "south east asia", "oceania", "australasia",
]

# Somewhere that is positively NOT the Philippines: countries, regions, and the
# timezone bands a PH candidate cannot cover. Deliberately no foreign cities. A city
# name in a location field is usually the employer's office, while a country or
# region name in that field is usually the restriction - guessing wrong on a city
# would drop a worldwide-remote job for having a San Francisco head office.
FOREIGN_TERMS = [
    "united states", "usa", "u.s.", "u.s.a", "us", "america", "americas",
    "north america", "south america", "latin america", "latam", "canada",
    "mexico", "brazil", "argentina", "colombia", "chile", "peru",
    "united kingdom", "uk", "britain", "england", "scotland", "wales", "ireland",
    "europe", "european", "eu", "eea", "emea", "germany", "deutschland", "france",
    "spain", "portugal", "italy", "netherlands", "belgium", "poland", "czechia",
    "austria", "switzerland", "sweden", "norway", "denmark", "finland", "greece",
    "romania", "bulgaria", "hungary", "ukraine", "turkey", "russia", "israel",
    "uae", "dubai", "abu dhabi", "saudi arabia", "qatar", "kuwait", "bahrain",
    "egypt", "nigeria", "kenya", "ghana", "south africa", "africa",
    "india", "pakistan", "bangladesh", "sri lanka", "nepal",
    "china", "hong kong", "taiwan", "japan", "korea", "singapore", "malaysia",
    "indonesia", "thailand", "vietnam", "cambodia", "myanmar", "laos",
    "australia", "new zealand", "anz",
    # Timezone restrictions, which arrive as "Time zone: CET (+/- 3 hours)". GMT and
    # UTC are left out on purpose - they are written as offsets and PH is UTC+8, so
    # "UTC+8 to UTC+10" is a match rather than a restriction. CST is out too: it is
    # China Standard Time as often as it is Central.
    "cet", "cest", "est", "edt", "pst", "pdt", "mst", "mdt", "brt", "aest",
]

# Tier 4: says the job is open, but names no geography to check it against. Only
# consulted once tier 3 has found nothing foreign. These carry no weight under the
# default settings, where rule 4 already keeps anything unmatched - they are what
# survives drop_when_unknown, for a config that would rather see only listings
# that state they are open.
OPEN_TERMS = [
    "worldwide", "world wide", "world-wide", "global", "globally", "anywhere",
    "any location", "any country", "international", "remote-first", "fully remote",
]

HOME_COUNTRY_CODE = "PH"

# Manila is UTC+8. A feed that restricts by clock (himalayas' timezoneRestrictions,
# a job that says "UTC-5 to UTC-8") is stating the same thing a country does: whose
# working day you have to keep. Three hours either side still leaves a real overlap;
# beyond that it is a night shift being sold as a remote job.
PH_UTC_OFFSET = 8.0
UTC_OFFSET_TOLERANCE = 3.0
_UTC_OFFSET_RE = re.compile(r"utc\s*([+-]\d{1,2}(?:\.\d+)?)", re.IGNORECASE)


def _timezone_workable(text: str) -> bool | None:
    """True/False if the text names UTC offsets, None if it names none at all."""
    offsets = [float(m) for m in _UTC_OFFSET_RE.findall(text or "")]
    if not offsets:
        return None
    return any(abs(o - PH_UTC_OFFSET) <= UTC_OFFSET_TOLERANCE for o in offsets)


def work_arrangement(job: dict) -> str:
    """'remote', 'hybrid', 'onsite' or '' - from structured fields only.

    Hybrid is tested before remote because Indeed writes "Hybrid remote in Makati"
    and SEEK writes "Hybrid", and reading either as remote would let a job that
    needs three days in a foreign office pass rule 2.
    """
    stated = " ".join(
        str(job.get(k) or "") for k in ("work_arrangement", "location", "job_type")
    ).lower()
    if "hybrid" in stated:
        return "hybrid"
    if "remote" in stated or "work from home" in stated or "wfh" in stated:
        return "remote"
    if job.get("remote"):
        return "remote"
    if "on-site" in stated or "onsite" in stated or "on site" in stated or "in office" in stated:
        return "onsite"
    return ""


# Phrases in a posting that mean the work is remote. Whole-word matched, so "wfh"
# does not fire inside another token and "remote" does not match "remotely-managed
# warehouse" - the latter is why this is a phrase list rather than a substring scan.
REMOTE_PHRASES = [
    "remote", "remotely", "work from home", "wfh", "work at home",
    "home based", "home-based", "fully remote", "100% remote", "telecommute",
    "distributed team", "anywhere",
]


# Formats the sources actually use for a posting date, in the order they are tried.
_DATE_FORMATS = ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d %b %Y")


def posting_age_days(job: dict) -> float | None:
    """How old the listing is, or None when it does not say.

    Unknown is NOT treated as old — the same rule the geographic and salary filters
    follow. Most email sources never state a date.
    """
    import datetime as _dt

    raw = str(job.get("posted") or job.get("timestamp") or "").strip()
    if not raw:
        return None
    cleaned = raw.replace("Z", "+0000")
    for fmt in _DATE_FORMATS:
        try:
            when = _dt.datetime.strptime(cleaned[: len(_dt.datetime.now().strftime(fmt)) + 5], fmt)
        except ValueError:
            continue
        if when.tzinfo:
            when = when.astimezone(_dt.timezone.utc).replace(tzinfo=None)
        return (_dt.datetime.utcnow() - when).total_seconds() / 86400
    try:
        return (_dt.datetime.now() - _dt.datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)).total_seconds() / 86400
    except Exception:
        return None


def is_remote(job: dict) -> bool:
    """Whether this job is remote, from the best evidence available.

    Three sources in order of trust, which is the order they should be read in:

    1. `work_arrangement` - the board's own structured field. Jobstreet's API says
       "Remote" outright, and ignoring it was a real bug: a listing whose page reads
       "Manila City, Metro Manila (Remote)" and "100% Work From Home" was stored as
       remote=No, because the API's `locations[].label` drops the "(Remote)" suffix
       and puts it in `workArrangements` instead.
    2. the location string - covers sources that write "Philippines / remote" or
       "Work from Home" straight into it.
    3. the posting text - the last resort, and it has to be, because a description
       can mention remote work while describing something else. Only the opening of
       the posting is read, where a listing states its own arrangement.

    Hybrid is checked first everywhere: "Hybrid remote in Makati" is not remote.
    """
    arrangement = str(job.get("work_arrangement") or "").lower()
    if "hybrid" in arrangement:
        return False
    if arrangement:
        return "remote" in arrangement or "home" in arrangement

    location = str(job.get("location") or "").lower()
    if "hybrid" in location:
        return False
    if _matched(location, REMOTE_PHRASES):
        return True

    if job.get("remote"):
        return True

    text = str(job.get("description_summary") or job.get("description") or "")[:600].lower()
    return bool(text) and not ("hybrid" in text) and bool(_matched(text, REMOTE_PHRASES))


def geography_check(job: dict, settings: dict) -> tuple[bool, str]:
    """Return (keep, reason) for the four rules above."""
    settings = settings or {}
    if settings.get("enabled") is False:
        return True, ""

    code = str(job.get("country_code") or "").strip().upper()
    # candidate_location is the eligibility field the remote feeds each expose under
    # their own name - remotive's candidate_required_location, himalayas'
    # locationRestrictions, jobicy's jobGeo. Adapters normalise onto this one.
    place = " ".join(
        str(job.get(k) or "") for k in ("location", "candidate_location", "country_code")
    ).strip()

    hard = _matched(place, settings.get("exclude_locations"))
    if hard:
        return False, f"location matches {hard!r}"

    home = list(settings.get("home_terms") or HOME_TERMS)
    home += list(settings.get("home_region_terms") or HOME_REGION_TERMS)
    if code == HOME_COUNTRY_CODE or _matched(place, home):
        return True, ""

    foreign_terms = settings.get("foreign_terms") or FOREIGN_TERMS
    open_terms = settings.get("open_terms") or OPEN_TERMS

    # A feed that fills in an eligibility field is being explicit, and is read
    # strictly: anything it names that is not ours is a restriction, including a
    # country this module has never heard of. That matters — 800 himalayas jobs
    # sampled on 2026-09-01 named Costa Rica, Serbia, Morocco, Kazakhstan and the
    # Cayman Islands, none of which any hand-written FOREIGN_TERMS would carry, and
    # all of which are as closed to a PH candidate as "United States" is. Free-text
    # `location` keeps the forgiving path below, because there "Ugong" is a barangay
    # rather than a restriction.
    stated = str(job.get("candidate_location") or "").strip()
    if stated:
        # Everything the listing says, not just the field that triggered strict mode.
        # An adapter that puts the country in `location` and the clock in
        # `candidate_location` would otherwise have an Australia-only job rescued by
        # its UTC+8 overlap, which is exactly backwards.
        blocked = _matched(place, foreign_terms)
        if blocked:
            return False, f"eligibility limited to {blocked!r}"
        workable = _timezone_workable(place)
        if workable is False:
            return False, f"timezone {stated!r} does not overlap UTC+{PH_UTC_OFFSET:.0f}"
        if workable is True or _matched(place, open_terms):
            return True, ""
        return False, f"eligibility limited to {stated!r}"

    foreign = _matched(place, foreign_terms)
    if code and code != HOME_COUNTRY_CODE:
        foreign = foreign or code
    if foreign:
        arrangement = work_arrangement(job)
        if arrangement == "remote":
            return False, f"remote but restricted to {foreign!r}"
        return False, f"{arrangement or 'on-site'} in {foreign!r}"

    if _matched(place, open_terms):
        return True, ""

    # Rule 4. An unrecognised place name is far more likely to be a PH barangay the
    # home list does not carry ("Ugong", "Fort Bonifacio") than a foreign office, and
    # scoring judges it either way. drop_when_unknown flips that, the same way
    # drop_when_salary_unknown does for pay: strict, and it costs every listing whose
    # location we simply do not recognise.
    if place and settings.get("drop_when_unknown"):
        return False, f"location {place!r} not recognised"
    return True, ""


def _matched(text: str, patterns) -> str | None:
    """The first pattern that appears in text as a whole word, if any.

    Word boundaries carry every one of these lists. Short BPO names like "vxi",
    "wns" or "tcs" would otherwise fire inside unrelated words; "intern" would
    reject "International"; and "ph" would match the "ph" in "Philadelphia".
    """
    text = (text or "").lower()
    if not text:
        return None
    for pat in patterns or []:
        pat = (pat or "").strip().lower()
        if pat and re.search(rf"(?<!\w){re.escape(pat)}(?!\w)", text):
            return pat
    return None


# ── Salary hidden in the posting text ────────────────────────────────────────
#
# Some feeds have no salary FIELD at all — Working Nomads is the clear case — so
# under `drop_when_salary_unknown` every one of their jobs is dropped unpriced
# even when the posting states pay plainly in its first line.
#
# Reading it out of prose is genuinely dangerous, though, and the danger is not
# the arithmetic. Measured over 45 live Working Nomads descriptions, the first
# money figure in the text was NOT the salary in most of them:
#
#   "we have already paid out over $11M to our engineers"  — a company statistic
#   "reps can sell anywhere from $500 to $1,000 every day" — commission per deal
#   "you'll earn up to $100 for each video you sell"       — a per-piece rate
#   "Compensation: $16-$24 per hour"                       — an actual salary
#   "Agentic Python Engineer | REMOTE | Full-time | $70-120K USD" — also real
#
# So this is deliberately strict, and returns nothing when unsure. Two of the
# three rules are about the words around the number, not the number itself.

# Pay stated per unit of time. A period marker is REQUIRED — it is what separates
# "$24 per hour" from "$24" sitting in a sentence about a product price.
_PERIOD_MARKER = (
    r"(?:per|an|a|/|each)\s*(?:hour|hr|day|week|month|mo|year|annum)\b"
    r"|\b(?:hourly|daily|weekly|monthly|yearly|annually|annual|per\s*annum)\b"
    r"|/\s*(?:hr|hour|day|wk|week|mo|month|yr|year)\b"
)

# A money figure, its optional k/M suffix, and a following period marker within a
# short window — short so it cannot reach across into an unrelated sentence.
_TEXT_SALARY_RE = re.compile(
    r"(?:[\$€£₱]|\b(?:usd|eur|gbp|php|cad|aud|sgd)\b)\s*"
    r"\d[\d,.\s]*\s*[kKmM]?"
    r"(?:\s*(?:-|–|—|to)\s*(?:[\$€£₱])?\s*\d[\d,.\s]*\s*[kKmM]?)?"
    r"(?:\s*(?:usd|eur|gbp|php|cad|aud|sgd))?"
    rf"(?:[^.;\n]{{0,18}}?(?:{_PERIOD_MARKER}))",
    re.IGNORECASE,
)

# The same shape without a period marker, accepted ONLY with a k/M suffix and a
# currency, which is the "$70-120K USD" convention job ads use for annual pay.
# The k may sit on either end of a range — ads write "$70-120K" as often as
# "$70K-$120K" — so it is optional here and a k is required after matching.
_TEXT_SALARY_K_RE = re.compile(
    r"(?:[\$€£₱]|\b(?:usd|eur|gbp|cad|aud|sgd)\b)\s*"
    r"\d[\d,.]*\s*[kK]?"
    r"(?:\s*(?:-|–|—|to)\s*(?:[\$€£₱])?\s*\d[\d,.]*\s*[kK]?)?"
    r"(?:\s*(?:usd|eur|gbp|cad|aud|sgd))?",
    re.IGNORECASE,
)

# Money that is explicitly NOT this job's wage. Checked in a window around the
# match, because the giveaway is always a neighbouring word.
#
# Matched on WORD BOUNDARIES, not as substrings. As substrings these are actively
# wrong and cost a real job: "in sales" fires inside "LinkedIn Sales Navigator",
# which threw away a genuine "$70-120K USD" on the first live listing that had one.
# "fee" would fire inside "coffee", "cost" inside "costume", "price" inside
# "priced". This is the same trap exclude_titles and exclude_companies already
# dodge, met again in a new place.
_NOT_SALARY_NEAR = (
    "paid out", "payout", "raised", "funding", "funded", "valuation", "valued at",
    "revenue", "arr", "in sales", "series a", "series b", "series c", "seed round",
    "market cap", "worth over", "backed by", "budget of", "saved", "savings",
    "for each", "per sale", "per deal", "per video", "per article", "per lead",
    "per client", "per project", "commission", "bonus of", "referral", "prize",
    "cost", "price", "pricing", "fee", "subscription", "plan starts",
    # Perks quoted in money. An "annual learning budget of $1,000" carries a real
    # period marker and a real currency, and reads as a salary of ₱4,833/month
    # without these — seen on live jobicy listings.
    "budget", "stipend", "allowance", "credit", "credits", "reimburse",
    "reimbursement", "learning", "wellness", "equipment", "home office",
    "professional development", "perk", "perks", "discount",
)


def salary_from_text(text: str, window: int = 120) -> str:
    """The clearest salary statement in a posting, or "" when there isn't one.

    Returns the matched STRING rather than a number, so `normalize_salary_php`
    stays the single place that knows about ranges, currencies and periods.
    """
    if not text:
        return ""
    for pattern in (_TEXT_SALARY_RE, _TEXT_SALARY_K_RE):
        for match in pattern.finditer(text):
            start = max(0, match.start() - window)
            around = text[start : match.end() + window].lower()
            if _matched(around, _NOT_SALARY_NEAR):
                continue
            found = " ".join(match.group(0).split())
            # That pattern also matches a bare number; only a figure carrying a k
            # means "thousands per year" by the convention it exists to catch.
            if pattern is _TEXT_SALARY_K_RE and "k" not in found.lower():
                continue
            # A last gate: it has to survive the normaliser, which applies the
            # sanity ceiling. Anything it cannot read is not worth guessing at.
            if normalize_salary_php(found, default_currency="USD"):
                return found
    return ""



def check(job: dict, settings: dict) -> tuple[bool, str]:
    """Return (keep, reason). reason is only meaningful when keep is False."""
    if not settings:
        return True, ""

    blocked = _matched(job.get("company"), settings.get("exclude_companies"))
    if blocked:
        return False, f"company matches {blocked!r}"

    blocked = _matched(job.get("title"), settings.get("exclude_titles"))
    if blocked:
        return False, f"title matches {blocked!r}"

    keep, why = geography_check(job, settings.get("geography") or {})
    if not keep:
        return False, why

    if settings.get("exclude_degree_required") and job.get("degree_required") == "required":
        return False, "listing requires a degree"

    floor = settings.get("min_salary_php")
    value = None
    if floor or settings.get("max_salary_php"):
        value = normalize_salary_php(
            job.get("salary") or "",
            job_type=job.get("job_type") or "",
            hours_per_month=settings.get("hours_per_month"),
            usd_to_php=settings.get("usd_to_php"),
            currency_rates=settings.get("currency_rates"),
            default_currency=currency_for(job),
            text=" ".join(
                [
                    job.get("title") or "",
                    job.get("description_summary") or job.get("description") or "",
                ]
            ),
        )
    if floor:
        if value is None:
            if settings.get("drop_when_salary_unknown"):
                return False, "no salary listed"
        elif value < float(floor):
            return False, f"salary ~{value:,.0f} below {float(floor):,.0f}"

    # A ceiling as well as a floor. Two different things land above it and both
    # are worth not reading:
    #   * a role well past the candidate's level - a US staff/principal listing at
    #     $230k is a real job and a real waste of an afternoon to apply for.
    #   * bad source data. jobicy published a "Customer Service Support Agent" as
    #     salaryMin 25000 USD *monthly* - $300k/year for a CS agent, almost
    #     certainly ₱25,000/month mislabelled as dollars. Our reading of it is
    #     faithful; the feed is wrong, and nothing downstream can tell.
    # Age. Boards differ wildly in whether a filled role ever comes down:
    # onlinejobs.ph leaves postings open indefinitely, and one live run surfaced a
    # 199-day-old listing as an 86/100 match and a 97-day-old one at 100/100. Those
    # are almost certainly filled, and applying to them is wasted effort in the one
    # place this pipeline cannot help - your own time.
    max_age = settings.get("max_age_days")
    if max_age:
        age = posting_age_days(job)
        if age is not None and age > float(max_age):
            return False, f"posted {age:.0f} days ago, older than {int(max_age)}"

    ceiling = settings.get("max_salary_php")
    if ceiling and value is not None and value > float(ceiling):
        return False, f"salary ~{value:,.0f} above {float(ceiling):,.0f}"

    return True, ""
