"""onlinejobs.ph — public job search reader.

Why this exists: onlinejobs.ph is a profile-first marketplace. Employers browse
worker profiles and message people directly, so it emits no job-alert emails at
all — the email path cannot see it. This reads its public search listing instead.

Politeness, deliberately conservative:
  * honours the site's robots.txt Crawl-delay of 5s between requests
  * reads only the public, logged-out search listing (never an authed page)
  * reads list pages, then ONE detail page per job that passes the title filter,
    each spaced by the same Crawl-delay. Keep "onlinejobs.ph" in skip_link_domains
    so enrich does not then fire a second, undelayed request for the same page.
  * identifies itself honestly in the User-Agent

Listing cards carry title, employment type, posted timestamp, salary, skills and
a ~300-character description. That was once assumed to be enough to score
against a resume. It is not: measured against every other source, no listing in
the database had ever cleared the 400 characters that separates a posting from a
teaser, so every score was a guess dressed as a number. The detail page carries
the real thing — 4,955 characters on the one measured — which is why each kept
job is now followed. Either way no LLM extract call is needed; the adapter emits
job dicts directly.
"""

from __future__ import annotations

import logging
import re
import time
from urllib.parse import quote_plus

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_EMPLOYER_ID_RE = re.compile(r"employerId\s*=\s*(\d+)")

BASE = "https://www.onlinejobs.ph"
SEARCH = BASE + "/jobseekers/jobsearch"
PAGE_SIZE = 30  # the site paginates by row offset, 30 per page

USER_AGENT = "jobsift/0.1 (personal job-alert tool; +https://github.com/kimlj/jobsift)"
DEFAULT_DELAY = 5.0  # robots.txt Crawl-delay
DEFAULT_MAX_PAGES = 3


def _matches(job: dict, include: list[str], exclude: list[str]) -> bool:
    """Keyword gate applied BEFORE scoring.

    An email alert is filtered on the board's side, so only relevant jobs ever
    arrive. `?jobkeyword=` gets most of the way there for this board, but its
    search is generous — asking for "python" returns a Shopify admin assistant —
    so this is the second gate. Doing it before the LLM stage is the whole
    point: a string compare is free, and a scoring call and a detail fetch are
    not.

    **Title only, deliberately.** This used to match the card's skill tags too,
    and they are employer-chosen marketing labels rather than a stack: a
    "Website Designer" paying $8/hour carries the tag "Web Design & Page
    Layout", whose bare "Web" passes an include list meant to catch web
    developers. One page of listings returned two jobs and both were design
    gigs, admitted on tags alone - neither title contains a whole-word "web"
    ("Website" is a different word), so the title gate alone would have dropped
    both correctly.
    """
    haystack = (job.get("title") or "").lower()

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


def _fetch_description(client, url: str) -> str:
    """Read one job's full posting text from its detail page.

    The listing card carries roughly 300 characters - enough to rank a job
    against a resume only in the sense that a title is. The detail page carries
    the posting: one measured at 4,955 characters, with the stack, the hours,
    the company and, where the employer states one, the degree requirement.

    That last one is the reason this exists. `exclude_degree_required` has been
    switched on the whole time and has never once fired, because every source
    fed it a snippet and the scorer honestly answered "unknown" for all 149
    jobs. The sentence it needs is on this page and nowhere else.

    Returns ("", "") on any failure, so the caller keeps the card's short
    description rather than losing the job.
    """
    try:
        resp = client.get(url)
    except Exception as exc:
        logger.warning("onlinejobs.ph: detail fetch failed for %s (%s)", url, exc)
        return "", ""
    if resp.status_code != 200:
        logger.warning("onlinejobs.ph: detail %s returned %s", url, resp.status_code)
        return "", ""
    node = BeautifulSoup(resp.text, "html.parser").select_one("#job-description")
    # The page has no structured employer NAME - no JSON-LD, no meta, nothing in
    # the markup - but it does assign the employer's id in an inline script, and
    # that is a stable identity the prose cannot give: two postings by the same
    # employer share it whether or not either one writes the company down.
    match = _EMPLOYER_ID_RE.search(resp.text)
    return _text(node), (match.group(1) if match else "")


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


def fetch_jobs(settings: dict, is_seen=None, seen_batch=None) -> list[dict]:
    """Read the public job search listing. Returns extract-shaped job dicts.

    `is_seen(job) -> bool` lets the caller say a listing is already stored. Here
    it skips the DETAIL fetch rather than the listing itself: filtering stays the
    pipeline's job, so passing nothing reads every kept job's page as before.

    `seen_batch(jobs) -> set of urls` is the same question asked once for every
    listing, for a caller whose answer is a network round trip away: the
    residential worker asking the core (worker.py). It may raise, and then no
    detail page is read.
    """
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

    # `?jobkeyword=` drives the site's own search. An earlier note here said no
    # server-side filter could be driven from a URL and that every page returns
    # the whole listing - that was wrong, and it cost real yield: an unfiltered
    # page one was ten Meta-Ads video editors, a payroll professional and a
    # virtual assistant, of which the title gate correctly kept one job. Asking
    # the site for "python" returns Senior Web Developer, Game AI Engineer and
    # AI Engineer on the same page.
    #
    # It is also the politer request: far fewer pages fetched for far more of
    # what we came for. With no keywords configured this falls back to reading
    # the unfiltered listing, which is the old behaviour.
    keywords = [k for k in (settings.get("search_keywords") or []) if str(k).strip()] or [None]

    jobs: list[dict] = []
    seen: set[str] = set()
    seen_before_filter = 0
    headers = {"User-Agent": USER_AGENT}
    first_request = True

    with httpx.Client(timeout=30, headers=headers, follow_redirects=True) as client:
        for keyword in keywords:
            for page in range(max_pages):
                offset = "" if page == 0 else f"/{page * PAGE_SIZE}"
                query = f"?jobkeyword={quote_plus(str(keyword))}" if keyword else ""
                url = f"{SEARCH}{offset}{query}"
                if not first_request:
                    time.sleep(delay)
                first_request = False

                # The site reliably serves a listing-less page on the first hit
                # of a session and the populated one on a repeat, so one paced
                # retry is required rather than optional. Never faster than
                # Crawl-delay.
                cards = []
                failed = False
                for attempt in range(2):
                    if attempt:
                        time.sleep(delay)
                    try:
                        resp = client.get(url)
                    except Exception as exc:
                        logger.warning("onlinejobs.ph: fetch failed for %s (%s)", url, exc)
                        failed = True
                        break
                    if resp.status_code != 200:
                        logger.warning("onlinejobs.ph: %s returned %s", url, resp.status_code)
                        failed = True
                        break
                    cards = BeautifulSoup(resp.text, "html.parser").select(".jobpost-cat-box")
                    if cards:
                        break

                # One bad keyword must not lose the jobs the others found, nor
                # skip the detail fetch for jobs already in hand.
                if failed or not cards:
                    break

                for card in cards:
                    parsed = _parse_card(card)
                    if not parsed:
                        continue
                    seen_before_filter += 1
                    # Keywords overlap heavily - "python" and "developer" return
                    # many of the same postings - so dedupe on the URL before
                    # anything downstream pays for the same job twice.
                    key = parsed.get("url") or parsed.get("title")
                    if key in seen:
                        continue
                    if _matches(parsed, include, exclude):
                        seen.add(key)
                        jobs.append(parsed)
                logger.info(
                    "onlinejobs.ph: %s page %d -> %d listing(s)",
                    keyword or "all", page + 1, len(cards),
                )

        # Detail pages, for jobs that survived the title gate only - the filter
        # runs first precisely so this costs one request per KEPT job rather
        # than one per listing seen.
        #
        # A listing already in seen_jobs is skipped here, because the pipeline
        # discards it the line after it arrives: reading its page buys a longer
        # description for a record that is about to be thrown away. Measured on
        # the first pass after this source came back, 106 of 112 pages were that
        # - 5 seconds of Crawl-delay each, nine tenths of the run, spent on jobs
        # already in hand. The board leaves postings up for months, so this is
        # the steady state and not a first-run artefact.
        #
        # The job dicts here never name an employer, so job_key falls back to the
        # URL slug on both sides of the comparison, which is why it matches.
        if settings.get("fetch_details", True) and jobs:
            pending = [j for j in jobs if j.get("url")]
            if seen_batch is not None or is_seen is not None:
                if seen_batch is not None:
                    known = seen_batch(pending)
                    fresh = [j for j in pending if j["url"] not in known]
                else:
                    fresh = [j for j in pending if not is_seen(j)]
                skipped = len(pending) - len(fresh)
                if skipped:
                    logger.info(
                        "onlinejobs.ph: skipping %d detail page(s) for listings already stored "
                        "(saves %.0fs)", skipped, skipped * delay,
                    )
                pending = fresh
            if pending:
                logger.info(
                    "onlinejobs.ph: reading %d detail page(s) at %.0fs apart", len(pending), delay
                )
            for job in pending:
                time.sleep(delay)
                full, employer = _fetch_description(client, job["url"])
                if full and len(full) > len(job.get("description") or ""):
                    job["description"] = full
                    # The whole posting is in hand, so enrich has nothing to fetch.
                    # Here that is what skip_link_domains already did; on a core
                    # fed by a residential worker the same fetch would be a 403.
                    job["full_posting"] = True
                if employer:
                    job["employer_id"] = employer

    logger.info(
        "onlinejobs.ph: %d listing(s) read, %d kept after keyword filter", seen_before_filter, len(jobs)
    )
    return jobs


# The job id that ends every posting URL: ".../job/web-automation-engineer-1720855".
_JOB_ID_RE = re.compile(r"-(\d+)/?$")


def job_id(url: str) -> str:
    """The numeric id at the end of a posting URL, or "" if there isn't one."""
    match = _JOB_ID_RE.search((url or "").strip())
    return match.group(1) if match else ""


def still_listed(client, url: str, title: str, delay: float = DEFAULT_DELAY) -> bool | None:
    """Is this stored posting still in the board's search results?

    True = found, False = the search worked and the posting is not in it,
    None = could not tell, which is NOT the same as gone and must never be
    written to a row as if it were.

    **Why search rather than the posting's own page.** A closed job keeps
    serving its detail page, and to a logged-out reader that page is
    byte-identical to an open one: same `#job-description`, same "Please login
    or register as jobseeker to apply", same tag skeleton, HTTP 200 either way.
    The "This job has been closed" banner renders only for an authenticated
    session, and this adapter deliberately never authenticates. So the detail
    page cannot answer the question and the listing has to.

    **Measured, on one closed job and one open one.** Searching the board for
    the closed posting's own title ("web automation") returned 60 results and
    it was not among them, though its title is an exact match and it should
    have led. Searching for the open posting's title ("lead software engineer")
    returned 20 results and found it. Closed postings leave the index; open
    ones do not.

    **The empty page is the trap.** `fetch_jobs` documents that this site serves
    a listing-less page on the first hit of a session and the populated one on a
    repeat. A search that comes back with no cards at all is therefore
    indistinguishable from a search for a job that no longer exists, so it
    returns None. Only a search that returned OTHER postings, and not this one,
    is evidence of anything.

    **The full page is the other trap, and it fired on the first live run.** A
    search is read a page at a time, so "not on the page" only means "not in the
    board's results" when the page was SHORT enough to be the last one. Checking
    page one alone marked two live jobs gone: "Software Engineer" and "AI / Full
    Stack Developer" are generic enough to fill 30 of 30 slots, and both
    postings were simply further down. So absence counts only after a page comes
    back short, and a title that stays full for DEFAULT_MAX_PAGES returns None
    rather than a verdict. "Web Automation Engineer" returned 4 results, which
    is why the closed posting it was measured on gave a real answer.
    """
    wanted = job_id(url)
    if not wanted or not (title or "").strip():
        return None

    query = quote_plus(title.strip())
    for page in range(DEFAULT_MAX_PAGES):
        offset = "" if page == 0 else f"/{page * PAGE_SIZE}"
        target = f"{SEARCH}{offset}?jobkeyword={query}"

        cards: list = []
        for attempt in range(2):
            if page or attempt:
                time.sleep(delay)
            try:
                resp = client.get(target)
            except Exception as exc:
                logger.warning("onlinejobs.ph: listing check failed for %s (%s)", url, exc)
                return None
            if resp.status_code != 200:
                logger.warning(
                    "onlinejobs.ph: listing check %s returned %s", target, resp.status_code)
                return None
            soup = BeautifulSoup(resp.text, "html.parser")
            cards = soup.select(".jobpost-cat-box")
            if cards:
                break

        if not cards:
            # Page 1 empty after a paced retry is the session quirk, not an
            # answer. A later page empty means the result set simply ended,
            # and we have now seen all of it without finding the job.
            return None if page == 0 else False

        # Match on the id, not the slug: an employer can edit a title, which
        # rewrites the slug while the id stays put.
        found = {job_id(a["href"]) for a in soup.select("a[href*='/jobseekers/job/']")}
        if wanted in found:
            return True
        if len(cards) < PAGE_SIZE:
            # Short page, so this was the last one and we have seen every
            # result the board has for this title. The job is not among them.
            return False

    # Every page we were willing to read came back full and the job was in none
    # of them. There may be more; absence here is not evidence. This is the case
    # a generic title produces - "Software Engineer" alone fills page after page
    # - and returning False on it would retire live jobs off the shortlist.
    return None
