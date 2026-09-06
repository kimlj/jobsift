"""Jobstreet PH — the SEEK v5 search API.

Why this exists: Jobstreet DOES email alerts, and those already flow through the
email path. What the emails cannot give us is the page — `ph.jobstreet.com/job/<id>`
sits behind Cloudflare and returns 403 to anything automated, browser User-Agent
included, so `enrich` degrades and every Jobstreet job is scored on whatever the
alert email happened to quote.

Jobstreet runs on SEEK infrastructure, and SEEK's search API is public. It hands
over, unauthenticated and already structured, the three fields this project spends
the most effort guessing at:

  * `salaryLabel`  "₱87,000 – ₱130,000 per month" — already pesos, already monthly.
                   Feeds min_salary_php with no currency inference at all, which is
                   the class of bug that silently binned the best onlinejobs.ph
                   listings. Watch for two real values on this field though: a USD
                   figure ("$500 – $600 per month", a PH board quoting dollars) and
                   an employer typing benefits into it ("Consecutive days off,
                   Remote"). normalize_salary_php reads the first correctly and
                   returns None for the second, which is "unknown", not "zero".
  * `workArrangements.displayText`  Remote / Hybrid / On-site — geography rule 3.
  * `locations[].countryCode`       "PH" — geography rules 1 and 2.

So this is not a scraper. It is one GET against a documented-shaped JSON endpoint,
no HTML parsing, no LLM extract call, and no page fetch.

**Search does not carry the posting.** What arrives is `teaser` — around 100
characters — plus bullet points when the advertiser bought them. That is well under
SNIPPET_CHARS, so these jobs score in snippet mode and Telegram marks them with the
"scored on an N-char alert snippet" warning. exclude_degree_required cannot fire on
a teaser, because the degree sentence is in a posting search never shows.

**The posting is reachable and is not ours to take.** The job page's own GraphQL
endpoint returns the whole advertisement to an anonymous request, and
`robots.txt` disallows both it and the search path this file uses. See the note
above `BASE_URL`. Reachable is not the same as allowed, and the difference is
written down in the one file a program is supposed to read.

**Which leaves this whole source on the wrong side of that line.** `Disallow:
/api/jobsearch/` covers the endpoint below. It is off by default in
`config.example.yaml` for that reason, and the honest way to read Jobstreet is
the email alerts: subscribe to their job alerts and the postings arrive in your
inbox, sent to you, where the extractor already handles them.

Measured 2026-09-01, keywords=developer: 4,230 total, of which the server itself
will narrow to 743 remote or 1,955 remote+hybrid before sending anything.
"""

from __future__ import annotations

import logging
import re
import time

import httpx

from ..filters import _matched

logger = logging.getLogger(__name__)

# ph.jobstreet.com is the PH storefront; siteKey selects the market inside SEEK.
# career-ops allowlists ID/SG/MY-Main and HK's jobsdb equivalent and does not carry
# PH-Main at all — it works, and it is the only one of them we want.
BASE_URL = "https://ph.jobstreet.com/api/jobsearch/v5/search"
JOB_URL = "https://ph.jobstreet.com/job/{id}"

# NOT here: a fetch of the full posting.
#
# It was added, worked, and was removed the same day. ph.jobstreet.com/robots.txt
# says:
#
#     Disallow: /graphql
#     Disallow: /api/jobsearch/
#
# The first is the endpoint the job page itself calls, which returns the whole
# advertisement - 4,302 characters against this file's 182-character teaser. It
# answers an anonymous request, so it was easy to mistake "reachable" for
# "allowed". The site had already said no in the one place a program is supposed
# to look, and nobody looked.
#
# Do not re-add it. The README's rule for this project is to use what a board
# publishes and to read robots.txt before enabling a source, and a posting worth
# having is not worth having on those terms. For one job you actually care about,
# open the page yourself and pass it in: `--draft <id> --posting FILE`.
DEFAULT_SITE_KEY = "PH-Main"

USER_AGENT = "jobsift/0.1 (personal job-alert tool; +https://github.com/kimlj/jobsift)"

# The endpoint answers an anonymous request with no User-Agent at all, so this is
# identification rather than disguise — the opposite of what the blocked page needs.
DEFAULT_DELAY = 2.0
DEFAULT_MAX_PAGES = 2
DEFAULT_PAGE_SIZE = 30      # 100 is accepted; 30 is a page of a search a human would run
MAX_PAGE_SIZE = 100

# The server-side filter. Passing several is allowed and additive — "2,3" returned
# 1,955 against 743 for remote alone — so this is how rule 3 gets enforced BEFORE
# anything crosses the wire, rather than downloading on-site jobs to discard them.
ARRANGEMENT_CODES = {"onsite": "1", "on-site": "1", "hybrid": "2", "remote": "3"}


def _clean(value) -> str:
    return str(value or "").strip()


def _location(job: dict) -> tuple[str, str]:
    """(label, countryCode) of the first location, which is the one SEEK ranks on."""
    for loc in job.get("locations") or []:
        label = _clean(loc.get("label"))
        if label or loc.get("countryCode"):
            return label, _clean(loc.get("countryCode")).upper()
    return "", ""


def _arrangement(job: dict) -> str:
    arrangements = job.get("workArrangements") or {}
    display = _clean(arrangements.get("displayText"))
    if display:
        return display
    for item in arrangements.get("data") or []:
        text = _clean((item.get("label") or {}).get("text"))
        if text:
            return text
    return ""


def _description(job: dict) -> str:
    """Everything the search response says about the WORK.

    Deliberately small, because that is all there is. bulletPoints are the three
    lines an advertiser pays to show on the card; classification is SEEK's own
    taxonomy ("Developers/Programmers") and is worth carrying because on a
    teaser-length job it is a real signal about the role rather than filler.
    """
    parts = [_clean(job.get("teaser"))]
    parts += [_clean(b) for b in (job.get("bulletPoints") or [])]
    for entry in job.get("classifications") or []:
        for key in ("subclassification", "classification"):
            described = _clean((entry.get(key) or {}).get("description"))
            if described:
                parts.append(described)
    return " ".join(p for p in parts if p)


def _to_job(raw: dict) -> dict | None:
    title = _clean(raw.get("title"))
    job_id = _clean(raw.get("id"))
    if not title or not job_id:
        return None

    label, country = _location(raw)
    # advertiser.description is the branded name the ad runs under; companyName is
    # the fallback for ads placed by an agency that did not brand them.
    company = _clean((raw.get("advertiser") or {}).get("description")) or _clean(
        raw.get("companyName")
    )

    return {
        "title": title,
        "company": company,
        "location": label,
        # The two fields the geographic filter reads. country_code is authoritative
        # and beats every keyword list; work_arrangement is what stops a Manila
        # hybrid role being read as remote.
        "country_code": country,
        "work_arrangement": _arrangement(raw),
        "salary": _clean(raw.get("salaryLabel")),
        "description": _description(raw),
        "posted": _clean(raw.get("listingDate")),
        # The canonical short link. It is 403 to us and perfectly fine in the
        # phone browser that actually opens it, which is the only thing it is for.
        "url": JOB_URL.format(id=job_id),
        "job_type": ", ".join(_clean(w) for w in (raw.get("workTypes") or []) if _clean(w)),
        # Named apart from the email path's "jobstreet" on purpose: the same board
        # reached two different ways, and keeping them distinguishable in the
        # database is the only way to answer whether this tier earned its keep.
        "source": "jobstreet_api",
    }


def _arrangement_param(settings: dict) -> str:
    """Map configured arrangement names to the API's numeric codes."""
    wanted = settings.get("work_arrangements") or []
    codes: list[str] = []
    for name in wanted:
        code = ARRANGEMENT_CODES.get(_clean(name).lower())
        if not code:
            logger.warning(
                "jobstreet: unknown work_arrangement %r — expected one of %s",
                name,
                sorted(set(ARRANGEMENT_CODES)),
            )
            continue
        if code not in codes:
            codes.append(code)
    return ",".join(codes)


def fetch_jobs(settings: dict, is_seen=None) -> list[dict]:
    """Search Jobstreet PH. Returns extract-shaped job dicts, deduped on job id.

    `is_seen(job) -> bool` lets the caller say a listing is already stored. It is
    used only to stop paging early (see the note below) — filtering is still the
    pipeline's job, so passing nothing simply reads every configured page.
    """
    site_key = _clean(settings.get("site_key")) or DEFAULT_SITE_KEY
    max_pages = max(1, int(settings.get("max_pages", DEFAULT_MAX_PAGES)))
    page_size = min(int(settings.get("page_size", DEFAULT_PAGE_SIZE)), MAX_PAGE_SIZE)
    delay = float(settings.get("delay_seconds", DEFAULT_DELAY))
    arrangements = _arrangement_param(settings)

    keywords = [k for k in (settings.get("search_keywords") or []) if _clean(k)]
    if not keywords:
        # No keyword means the whole board, which for PH is tens of thousands of
        # rows the title filter would then throw away one at a time.
        logger.warning("jobstreet: no search_keywords configured — nothing to search")
        return []

    # The second gate. SEEK's search is broad — "automation specialist" returns
    # Funnel Builder Virtual Assistant, "rpa developer" returns ABAP Developer
    # Trainer — so a title filter runs before anything is scored, the same way
    # onlinejobs_ph and remote_feeds do it. Empty lists keep everything.
    include = [w for w in (settings.get("include_keywords") or []) if w]
    exclude = [w for w in (settings.get("exclude_keywords") or []) if w]

    jobs: list[dict] = []
    seen: set[str] = set()
    gated = 0
    first_request = True

    with httpx.Client(timeout=30, headers={"User-Agent": USER_AGENT}) as client:
        for keyword in keywords:
            for page in range(1, max_pages + 1):
                if not first_request:
                    time.sleep(delay)
                first_request = False

                params = {
                    "siteKey": site_key,
                    "keywords": keyword,
                    "pageSize": page_size,
                    "page": page,
                    "sortmode": "ListedDate",   # freshest first, not most relevant
                }
                if arrangements:
                    params["workarrangement"] = arrangements

                try:
                    resp = client.get(BASE_URL, params=params)
                    resp.raise_for_status()
                    payload = resp.json()
                except Exception as exc:
                    logger.warning(
                        "jobstreet: search failed for %r page %d (%s)", keyword, page, exc
                    )
                    break

                rows = payload.get("data") or []
                page_new = 0
                if page == 1:
                    logger.info(
                        "jobstreet: %r matched %s listing(s); reading up to %d page(s)",
                        keyword,
                        payload.get("totalCount", "?"),
                        max_pages,
                    )
                if not rows:
                    break

                for raw in rows:
                    job_id = _clean(raw.get("id"))
                    if job_id in seen:
                        continue
                    seen.add(job_id)
                    job = _to_job(raw)
                    if not job:
                        continue
                    if is_seen is not None and is_seen(job):
                        continue
                    title = job["title"]
                    if (include and not _matched(title, include)) or (
                        exclude and _matched(title, exclude)
                    ):
                        gated += 1
                        continue
                    jobs.append(job)
                    page_new += 1

                if len(rows) < page_size:
                    break   # last page

                # Results are sorted newest-first, so page 2 is strictly OLDER than
                # page 1. If anything on page 1 was already known, everything below
                # it is known too and there is nothing to go and find. Only a page
                # that was entirely new can mean the newest listings overflowed it.
                #
                # This is what makes frequent polling affordable: a steady-state
                # sweep costs one request per keyword instead of max_pages, and the
                # deep read still happens on the first run and after a quiet spell.
                if is_seen and page_new < len(rows):
                    logger.debug(
                        "jobstreet: %r page %d had %d/%d new — stopping, the rest is older",
                        keyword, page, page_new, len(rows),
                    )
                    break

    logger.info("jobstreet: %d unique listing(s) across %d keyword(s); %d dropped by the title gate",
                len(jobs), len(keywords), gated)
    return jobs
