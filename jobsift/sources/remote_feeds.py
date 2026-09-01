"""Remote job boards that publish a public JSON feed.

Four of them, behind one adapter, because they are the same shape of thing: an
unauthenticated GET returning JSON with the **full posting text inline**. That last
part is what makes them worth having. Every other source in this project hands over
a teaser and hides the posting behind a click we then have to follow, get 403'd on,
or pay an LLM to read. Here the description arrives with the listing — 4,000 to
10,600 characters of it — so `enrich` is skipped entirely and these are the only
non-email jobs that reach `evidence: full`.

The four, measured live on 2026-09-01:

  remotive       19 jobs      the WHOLE feed; see the note below
  workingnomads  45 jobs      a FIXED 45; no salary field at all
  himalayas      20 per page, ~105,000 total, cursor-paged
  jobicy         up to 100 per call, and the only one with a usable geo filter

**Eligibility is the whole ballgame here.** These boards are worldwide, so "remote"
says nothing about whether a Manila-based candidate may take the job — measured, only
6-47% of their listings are open to one. Each feed states the restriction in its own
differently-named field, and this adapter's real job is to put that field in
`candidate_location`, where `filters.geography_check` reads it strictly. An adapter
that got that mapping wrong would look like it was working while quietly flooding the
inbox with US-only listings.

  remotive       candidate_required_location
  workingnomads  location            (yes, "location" — it is the restriction)
  himalayas      locationRestrictions + timezoneRestrictions
  jobicy         jobGeo

**A note on remotive.** Its API ignores `limit`, `search` and `category` — every call
returns the same 19 rows, and the envelope confirms it: `total-job-count: 19`. It
also ships a legal notice asking that its jobs not be republished to third-party job
sites and that Remotive be credited as the source. Sending them to your own phone is
not republishing, but that is why the source label stays `remotive` and the link is
always theirs.
"""

from __future__ import annotations

import logging
import time

import httpx

from ..filters import _matched, salary_from_text
from ..utils import html_to_text

logger = logging.getLogger(__name__)

USER_AGENT = "jobsift/0.1 (personal job-alert tool; +https://github.com/kimlj/jobsift)"
DEFAULT_DELAY = 1.0
DEFAULT_MAX_PAGES = 5          # himalayas only; the others are single-call feeds
DESCRIPTION_LIMIT = 8000       # what the scorer can use; the rest is boilerplate
# jobicy documents a cap of 100 and quietly serves 200; 500 and 1000 also return
# 200, so this is the real ceiling. See the config note on why it is worth using.
MAX_JOBICY_COUNT = 200

# The salary period words each feed uses, mapped to the words normalize_salary_php
# already understands. "annual" and "yearly" both mean the same thing and neither is
# spelled the way the parser's "per annum" pattern expects.
_PERIOD_WORDS = {
    "annual": "per year", "annually": "per year", "yearly": "per year",
    "year": "per year", "monthly": "per month", "month": "per month",
    "weekly": "per week", "week": "per week", "daily": "per day", "day": "per day",
    "hourly": "per hour", "hour": "per hour",
}


def _salary_string(minimum, maximum, currency: str, period: str) -> str:
    """Rebuild a salary string from structured fields.

    Deliberately re-uses `normalize_salary_php` rather than doing the arithmetic
    here: that function is the one place that knows about ranges reading their low
    end, hourly-to-monthly conversion and the currency table, and a second
    implementation would be a second thing to get wrong. Structured numbers also
    sidestep the separator problem entirely — there is no "€40.000" to misread when
    the feed hands over 40000 and "EUR".
    """
    low = minimum or maximum
    if not low:
        return ""
    high = maximum if (maximum and maximum != minimum) else None
    amount = f"{low} - {high}" if high else f"{low}"
    period_words = _PERIOD_WORDS.get(str(period or "").strip().lower(), "")
    return " ".join(p for p in (amount, str(currency or "").strip().upper(), period_words) if p)


def _get(client: httpx.Client, url: str):
    resp = client.get(url)
    resp.raise_for_status()
    return resp.json()


def _fetch_remotive(client: httpx.Client, settings: dict) -> list[dict]:
    payload = _get(client, "https://remotive.com/api/remote-jobs")
    jobs = []
    for raw in payload.get("jobs") or []:
        restriction = str(raw.get("candidate_required_location") or "").strip()
        description = html_to_text(raw.get("description") or "", limit=DESCRIPTION_LIMIT)
        jobs.append({
            "title": raw.get("title") or "",
            "company": raw.get("company_name") or "",
            "location": restriction,
            "candidate_location": restriction,
            # The field exists here but is blank more often than not; fall back
            # to the posting text the same way Working Nomads has to.
            "salary": str(raw.get("salary") or "").strip() or salary_from_text(description),
            "description": description,
            "posted": raw.get("publication_date") or "",
            "url": raw.get("url") or "",
            "job_type": raw.get("job_type") or "",
            "skills_required": [t for t in (raw.get("tags") or []) if t],
            "remote": True,
            # Description came inline; enrich has nothing to add.
            "full_posting": True,
            "source": "remotive",
        })
    return jobs


def _fetch_workingnomads(client: httpx.Client, settings: dict) -> list[dict]:
    """A bare GET, because nothing else is available.

    Probed 2026-09-01: `limit`, `count`, `page`, `offset`, `category`, `search`,
    `tag` and `keyword` are ALL accepted with a 200 and ALL ignored — every call
    returns the same fixed 45 newest listings. So there is no server-side keyword
    search to use, and the config's include/exclude gate is the only filter there
    can be for this feed.

    Worth being exact about what "beats the digest cap" means: the free email
    digest is capped at 5 jobs a day with the rest behind a paid account, and this
    returns 45 — 9x better, and not subject to that cap. It is NOT uncapped; 45 is
    a hard page size with no way to page past it.

    The feed also carries `category_name` (Development 28, Administration 12, ...).
    Filtering on it was measured against the title gate and the two agree on 26 of
    28 — the only differences being duplicate "No Experience Required" listings
    that are junk under either rule — so the structured field buys nothing here and
    is deliberately not used.
    """
    payload = _get(client, "https://www.workingnomads.com/api/exposed_jobs/")
    jobs = []
    for raw in payload or []:
        # This feed's "location" is the candidate restriction, not an office: its
        # values read "Anywhere", "CET (+/- 3 hours)", "Europe, North America, Latin
        # America, APAC". Mapping it to `location` alone would send it down the
        # forgiving path and let every geo-locked listing through.
        restriction = str(raw.get("location") or "").strip()
        description = html_to_text(raw.get("description") or "", limit=DESCRIPTION_LIMIT)
        jobs.append({
            "title": raw.get("title") or "",
            "company": raw.get("company_name") or "",
            "location": restriction,
            "candidate_location": restriction,
            # No salary FIELD exists on this feed — so under drop_when_salary_unknown
            # every Working Nomads job was dropped unpriced, silencing the source
            # entirely. The pay is often stated in the posting itself ("Compensation:
            # $16-$24 per hour", "Full-time | $70-120K USD"), so it is read from
            # there. salary_from_text is deliberately strict and returns "" when
            # unsure — see its notes on the company statistics and per-deal
            # commissions that a naive read of this same text picks up instead.
            "salary": salary_from_text(description),
            "description": description,
            "posted": raw.get("pub_date") or "",
            "url": raw.get("url") or "",
            "skills_required": [t for t in (raw.get("tags") or []) if t],
            "remote": True,
            # Description came inline; enrich has nothing to add.
            "full_posting": True,
            "source": "workingnomads",
        })
    return jobs


def _fetch_himalayas(client: httpx.Client, settings: dict) -> list[dict]:
    """Cursor-paged. `limit` is accepted and ignored — a page is always 20."""
    max_pages = max(1, int(settings.get("max_pages", DEFAULT_MAX_PAGES)))
    delay = float(settings.get("delay_seconds", DEFAULT_DELAY))
    jobs, cursor, seen = [], None, set()

    for page in range(max_pages):
        if page:
            time.sleep(delay)
        url = "https://himalayas.app/jobs/api" + (f"?cursor={cursor}" if cursor else "")
        payload = _get(client, url)
        rows = payload.get("jobs") or []
        if not rows:
            break
        for raw in rows:
            guid = raw.get("guid") or ""
            if guid in seen:
                continue
            seen.add(guid)
            description = html_to_text(raw.get("description") or "", limit=DESCRIPTION_LIMIT)
            places = [str(x) for x in (raw.get("locationRestrictions") or [])]
            zones = [f"UTC{float(x):+g}" for x in (raw.get("timezoneRestrictions") or [])]
            jobs.append({
                "title": raw.get("title") or "",
                "company": raw.get("companyName") or "",
                # An empty restrictions list is this feed saying "no restriction".
                "location": ", ".join(places) or "Worldwide",
                # Places AND timezones: either one can close the door, and the
                # filter reads whichever is present.
                "candidate_location": ", ".join(places + zones),
                "salary": _salary_string(
                    raw.get("minSalary"), raw.get("maxSalary"),
                    raw.get("currency"), raw.get("salaryPeriod"),
                ) or salary_from_text(description),
                "description": description,
                "posted": raw.get("pubDate") or "",
                # guid is the himalayas posting; applicationLink is usually the same but
                # can be the employer's own form, which is a worse thing to open on
                # a phone and a worse thing to keep as the job's identity.
                "url": guid or raw.get("applicationLink") or "",
                "job_type": raw.get("employmentType") or "",
                "experience_level": ", ".join(str(s) for s in (raw.get("seniority") or [])),
                "remote": True,
            # Description came inline; enrich has nothing to add.
            "full_posting": True,
                "source": "himalayas",
            })
        cursor = payload.get("nextCursor")
        if not cursor:
            break
    return jobs


def _fetch_jobicy(client: httpx.Client, settings: dict) -> list[dict]:
    """The only one of the four with a geo filter that means what we mean.

    `geo=philippines` returns Anywhere, APAC, Philippines and multi-region listings
    that include us — the geographic rule, applied on their server before anything
    crosses the wire. Checked both directions on 2026-09-01: nothing it returns would
    our own rule reject, and of the 7 eligible jobs in an unfiltered 100, all 7 are
    present, so it hides nothing we want.

    What it buys is volume, not quality: unfiltered we keep 7 of 100 and throw away
    93 after downloading them. It also means our geographic rule does no
    discriminating work on this feed — it is the backstop that would catch jobicy
    redefining `geo=philippines`, and every job here passing it is expected rather
    than reassuring.

    Note `geo=asia` is a 400; "philippines" is the value that works.
    """
    count = min(int(settings.get("count", 200)), MAX_JOBICY_COUNT)
    geo = str(settings.get("geo", "philippines")).strip()
    url = f"https://jobicy.com/api/v2/remote-jobs?count={count}"
    if geo:
        url += f"&geo={geo}"
    payload = _get(client, url)
    jobs = []
    for raw in payload.get("jobs") or []:
        restriction = str(raw.get("jobGeo") or "").strip()
        description = html_to_text(raw.get("jobDescription") or "", limit=DESCRIPTION_LIMIT)
        jobs.append({
            "title": raw.get("jobTitle") or "",
            "company": raw.get("companyName") or "",
            "location": restriction,
            "candidate_location": restriction,
            "salary": _salary_string(
                raw.get("salaryMin"), raw.get("salaryMax"),
                raw.get("salaryCurrency"), raw.get("salaryPeriod"),
            ) or salary_from_text(description),
            "description": description,
            "posted": raw.get("pubDate") or "",
            "url": raw.get("url") or "",
            "job_type": ", ".join(str(t) for t in (raw.get("jobType") or [])),
            "experience_level": raw.get("jobLevel") or "",
            "remote": True,
            # Description came inline; enrich has nothing to add.
            "full_posting": True,
            "source": "jobicy",
        })
    return jobs


FEEDS = {
    "remotive": _fetch_remotive,
    "workingnomads": _fetch_workingnomads,
    "himalayas": _fetch_himalayas,
    "jobicy": _fetch_jobicy,
}


def _wanted(title: str, include, exclude) -> bool:
    """Whole-word TITLE gate, applied before anything is scored.

    Every other source in this project filters by keyword — jobstreet through its
    own search, onlinejobs through include/exclude lists — and these feeds were the
    exception, taking whatever the board happened to publish. It showed: a jobicy
    page offered PreK Teacher, Early Childhood Teacher and Language Arts Teacher as
    passing jobs, because nothing had ever asked for engineering work. There is no
    server-side keyword search on any of the four, so the gate lives here.

    Title only, deliberately, for the reason the onlinejobs adapter documents: a
    description mentioning "Python" does not make a job a Python job.
    """
    if include and not _matched(title, include):
        return False
    return not (exclude and _matched(title, exclude))


def fetch_jobs(settings: dict) -> list[dict]:
    """Read every enabled feed. Returns extract-shaped job dicts.

    One feed failing never takes the others down with it — these are four unrelated
    services and any of them can be having a bad day.
    """
    include = [w for w in (settings.get("include_keywords") or []) if w]
    exclude = [w for w in (settings.get("exclude_keywords") or []) if w]
    wanted = settings.get("feeds") or list(FEEDS)
    delay = float(settings.get("delay_seconds", DEFAULT_DELAY))
    jobs: list[dict] = []

    with httpx.Client(timeout=45, headers={"User-Agent": USER_AGENT},
                      follow_redirects=True) as client:
        for index, name in enumerate(wanted):
            fetch = FEEDS.get(str(name).strip().lower())
            if not fetch:
                logger.warning("remote_feeds: unknown feed %r — expected one of %s",
                               name, sorted(FEEDS))
                continue
            if index:
                time.sleep(delay)
            try:
                found = fetch(client, dict(settings.get(name) or {}, **{
                    "max_pages": settings.get("max_pages", DEFAULT_MAX_PAGES),
                    "delay_seconds": delay,
                }))
            except Exception as exc:
                logger.warning("remote_feeds: %s failed (%s) — continuing", name, exc)
                continue
            kept = [j for j in found if _wanted(j.get("title") or "", include, exclude)]
            logger.info("remote_feeds: %s returned %d listing(s), %d past the title gate",
                        name, len(found), len(kept))
            jobs.extend(kept)

    return jobs
