"""Job boards hosted on JazzHR (`<company>.applytojob.com`).

Some employers and recruiters only post on their own JazzHR board. Bamboo Works
is the first: a recruiter whose Claude and automation roles reach Working Nomads
late or not at all. A JazzHR board is server-rendered HTML with no RSS, and its
robots.txt disallows only /cb, so this reads the public pages a browser would:

- `/apply`, one page listing every open role with its title and location;
- one detail page per role, for the full description, read only for roles that
  pass the title gate and are not already stored.

The same role is often posted several times under different ids (four copies of
one Bamboo Works role on 6 Oct 2026, word for word identical), so a board's
listings are collapsed by title before any detail page is read.
"""

from __future__ import annotations

import html as html_lib
import json
import logging
import re
import time

import httpx
from bs4 import BeautifulSoup

from ..filters import _matched, salary_from_text
from ..utils import html_to_text, normalize_title

logger = logging.getLogger(__name__)

USER_AGENT = "jobsift/0.1 (personal job-alert tool; +https://github.com/kimlj/jobsift)"
DESCRIPTION_LIMIT = 12000
# robots.txt sets no Crawl-delay; a couple of seconds keeps a board's handful of
# detail pages from arriving as a burst.
DEFAULT_DELAY = 2.0

_JOB_HREF = re.compile(r"/apply/([A-Za-z0-9]{10})/")


def board_url(board: str) -> str:
    return f"https://{board}.applytojob.com/apply"


def _wanted(title: str, include: list, exclude: list) -> bool:
    if include and not _matched(title, include):
        return False
    return not (exclude and _matched(title, exclude))


def _company(soup: BeautifulSoup, board: str) -> str:
    """The board's own name for itself: its JSON-LD Organization, then og:title."""
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except ValueError:
            continue
        if isinstance(data, dict) and data.get("@type") == "Organization" and data.get("name"):
            return str(data["name"]).strip()
    og = soup.find("meta", property="og:title")
    if og and og.get("content"):
        # "<Company> - Career Page" on the list, "<Role> - <Company> - Career Page" on a job
        parts = [p.strip() for p in html_lib.unescape(og["content"]).split(" - ")]
        if len(parts) >= 2 and parts[-1].lower() == "career page":
            return parts[-2]
    return board


def parse_board(page: str, board: str, settings: dict | None = None) -> list[dict]:
    """The listings on a board's /apply page, title-gated and collapsed by title."""
    settings = settings or {}
    include = [str(v) for v in (settings.get("include_keywords") or []) if str(v).strip()]
    exclude = [str(v) for v in (settings.get("exclude_keywords") or []) if str(v).strip()]

    soup = BeautifulSoup(page, "html.parser")
    company = _company(soup, board)
    jobs: list[dict] = []
    titles: set[str] = set()
    for item in soup.select("li.list-group-item"):
        anchor = item.select_one(".list-group-item-heading a[href]")
        if not anchor or not _JOB_HREF.search(anchor["href"]):
            continue
        title = " ".join(anchor.get_text(" ", strip=True).split())
        if not title or not _wanted(title, include, exclude):
            continue
        norm = normalize_title(title)
        if norm in titles:
            continue
        titles.add(norm)
        marker = item.select_one(".fa-map-marker")
        location = " ".join(marker.parent.get_text(" ", strip=True).split()) if marker else ""
        jobs.append({
            "title": title,
            "company": company,
            "location": location,
            "candidate_location": location,
            "salary": "",
            "description": "",
            "posted": "",
            "url": anchor["href"].strip().replace("http://", "https://", 1),
            "job_type": "",
            "work_arrangement": "remote" if "remote" in location.lower() else "",
            "remote": "remote" in location.lower(),
            "source": f"jazzhr:{board}",
        })
    return jobs


def parse_detail(page: str) -> dict:
    """What a job page adds to its listing: description, salary, employment type."""
    soup = BeautifulSoup(page, "html.parser")
    body = soup.find(id="job-description")
    description = html_to_text(str(body), limit=DESCRIPTION_LIMIT) if body else ""
    employment = soup.find(id="resumator-job-employment")
    out = {
        "description": description,
        "salary": salary_from_text(description),
        "job_type": " ".join(employment.get_text(" ", strip=True).split()) if employment else "",
    }
    if description:
        # The whole posting is in hand, so enrich has nothing left to fetch.
        out["full_posting"] = True
    return out


def fetch_jobs(settings: dict, is_seen=None) -> list[dict]:
    """Read every configured board. A board that fails is logged and skipped."""
    boards = [str(b).strip() for b in (settings.get("boards") or []) if str(b).strip()]
    delay = float(settings.get("delay_seconds") or DEFAULT_DELAY)
    jobs: list[dict] = []
    with httpx.Client(
        headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=20
    ) as client:
        for board in boards:
            try:
                response = client.get(board_url(board))
                response.raise_for_status()
                listed = parse_board(response.text, board, settings)
                fresh = [j for j in listed if not (is_seen and is_seen(j))]
                logger.info(
                    "jazzhr %s: %d role(s) past the title gate, %d not yet stored",
                    board, len(listed), len(fresh),
                )
                for job in fresh:
                    time.sleep(delay)
                    try:
                        detail = client.get(job["url"])
                        detail.raise_for_status()
                        job.update(parse_detail(detail.text))
                    except httpx.HTTPError as exc:
                        # Kept with its title alone: the pipeline can still judge it,
                        # and enrich will try the page again.
                        logger.warning("jazzhr %s: %s unread (%s)", board, job["url"], exc)
                jobs.extend(listed)
            except httpx.HTTPError:
                logger.exception("jazzhr %s: board unreadable, skipping it this pass", board)
    return jobs
