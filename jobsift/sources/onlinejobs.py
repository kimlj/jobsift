"""onlinejobs.ph — public job search reader.

Why this exists: onlinejobs.ph is a profile-first marketplace. Employers browse
worker profiles and message people directly, so it emits no job-alert emails at
all — the email path cannot see it. This reads its public search listing instead.

Politeness, deliberately conservative:
  * honours the site's robots.txt Crawl-delay of 5s between requests
  * reads only the public, logged-out search listing (never an authed page)
  * reads LIST pages only. Individual job pages are not followed, which is why
    the caller should keep "onlinejobs.ph" in skip_link_domains — otherwise
    enrich would fire one undelayed request per job.
  * identifies itself honestly in the User-Agent

Listing cards carry title, employment type, posted timestamp, salary, skills and
a truncated description. That is enough to score against a resume, so no LLM
extract call is needed here — the adapter emits job dicts directly.
"""

from __future__ import annotations

import logging
import re
import time

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

BASE = "https://www.onlinejobs.ph"
SEARCH = BASE + "/jobseekers/jobsearch"
PAGE_SIZE = 30  # the site paginates by row offset, 30 per page

USER_AGENT = "jobsift/0.1 (personal job-alert tool; +https://github.com/kimlj/jobsift)"
DEFAULT_DELAY = 5.0  # robots.txt Crawl-delay
DEFAULT_MAX_PAGES = 3


def _matches(job: dict, include: list[str], exclude: list[str]) -> bool:
    """Keyword gate applied BEFORE scoring.

    An email alert is filtered on the board's side, so only relevant jobs ever
    arrive. onlinejobs.ph exposes no server-side filter we can drive from a URL —
    every page returns the full listing — so the equivalent filtering has to
    happen here. Doing it before the LLM stage is the whole point: a string
    compare is free, a scoring call is not.
    """
    haystack = " ".join(
        [job.get("title") or "", " ".join(job.get("skills_required") or [])]
    ).lower()

    def hit(word: str) -> bool:
        # Whole-word match, so "web" does not fire on "Website" and "api" does
        # not fire on "capital". Multi-word phrases match as a phrase.
        return re.search(rf"(?<!\w){re.escape(word.lower().strip())}(?!\w)", haystack) is not None

    if any(hit(w) for w in exclude):
        return False
    if not include:
        return True
    return any(hit(w) for w in include)


def _text(node, default: str = "") -> str:
    return " ".join(node.get_text(" ", strip=True).split()) if node else default


def _parse_card(card) -> dict | None:
    heading = card.select_one("h4")
    if not heading:
        return None

    badge = heading.select_one("span.badge")
    job_type = _text(badge)
    if badge:
        badge.extract()  # so it does not end up glued to the title
    title = _text(heading)
    if not title:
        return None

    desc_node = card.select_one(".desc")
    url = ""
    if desc_node:
        link = desc_node.select_one("a[href]")
        if link:
            href = link["href"].strip()
            url = href if href.startswith("http") else BASE + href
            link.extract()  # drop the "See More" anchor text

    posted_node = card.select_one("p[data-temp]")
    posted = posted_node.get("data-temp", "").strip() if posted_node else ""

    # Salary sits in the dd next to the dollar icon; there is no other dd here.
    salary = ""
    for dd in card.select("dd"):
        value = _text(dd)
        if value:
            salary = value
            break

    skills = [s for s in (_text(a) for a in card.select(".job-tag a")) if s]

    return {
        "title": title,
        # The listing card never names the employer — onlinejobs.ph reveals it
        # only on the detail page. Left empty on purpose; _job_key falls back to
        # the URL slug so same-titled postings stay distinct.
        "company": "",
        "location": "Philippines / remote",
        "salary": salary,
        "description": _text(desc_node),
        "posted": posted,
        "url": url,
        "job_type": job_type,
        "skills_required": skills,
        "source": "onlinejobs_ph",
    }


def fetch_jobs(settings: dict) -> list[dict]:
    """Read the public job search listing. Returns extract-shaped job dicts."""
    max_pages = int(settings.get("max_pages", DEFAULT_MAX_PAGES))
    delay = float(settings.get("delay_seconds", DEFAULT_DELAY))
    if delay < DEFAULT_DELAY:
        logger.warning(
            "delay_seconds=%.1f is below the site's robots.txt Crawl-delay of %.0fs — raising it",
            delay,
            DEFAULT_DELAY,
        )
        delay = DEFAULT_DELAY

    include = [w for w in (settings.get("include_keywords") or []) if w]
    exclude = [w for w in (settings.get("exclude_keywords") or []) if w]

    jobs: list[dict] = []
    seen_before_filter = 0
    headers = {"User-Agent": USER_AGENT}

    with httpx.Client(timeout=30, headers=headers, follow_redirects=True) as client:
        for page in range(max_pages):
            url = SEARCH if page == 0 else f"{SEARCH}/{page * PAGE_SIZE}"
            if page:
                time.sleep(delay)

            # The site reliably serves a listing-less page on the first hit of a
            # session and the populated one on a repeat, so one paced retry is
            # required rather than optional. Still never faster than Crawl-delay.
            cards = []
            for attempt in range(2):
                if attempt:
                    time.sleep(delay)
                try:
                    resp = client.get(url)
                except Exception as exc:
                    logger.warning("onlinejobs.ph: fetch failed for %s (%s)", url, exc)
                    return jobs
                if resp.status_code != 200:
                    logger.warning("onlinejobs.ph: %s returned %s — stopping", url, resp.status_code)
                    return jobs
                cards = BeautifulSoup(resp.text, "html.parser").select(".jobpost-cat-box")
                if cards:
                    break

            if not cards:
                logger.info("onlinejobs.ph: no listings on %s after retry — stopping", url)
                break

            for card in cards:
                parsed = _parse_card(card)
                if not parsed:
                    continue
                seen_before_filter += 1
                if _matches(parsed, include, exclude):
                    jobs.append(parsed)
            logger.info("onlinejobs.ph: page %d -> %d listing(s)", page + 1, len(cards))

    logger.info(
        "onlinejobs.ph: %d listing(s) read, %d kept after keyword filter", seen_before_filter, len(jobs)
    )
    return jobs
