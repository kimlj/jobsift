"""Stage 2 — follow the job link and read structured details off the page (LLM)."""

from __future__ import annotations

import logging

from .safefetch import UnsafeURL, safe_get
from .utils import html_to_text, strip_tracking_params

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
)

SYSTEM = """You extract structured details from a single job posting page. When the page
doesn't explicitly list technologies, infer likely required skills from the description.

Return raw JSON only — no markdown, no prose — with this structure (use empty string/array when unknown):
{"title": "", "company": "", "location": "", "salary": "", "skills_required": [],
 "job_type": "", "experience_level": "", "responsibilities": [], "requirements": [],
 "benefits": [], "description_summary": "", "duration": "", "remote": false}"""


def _normalize_no_link(job: dict) -> dict:
    """Fill defaults for a job we didn't (or couldn't) enrich from a page."""
    location = (job.get("location") or "").lower()
    return {
        **job,
        "skills_required": job.get("skills_required") or [],
        "experience_level": job.get("experience_level") or "unknown",
        "responsibilities": job.get("responsibilities") or [],
        "requirements": job.get("requirements") or [],
        "benefits": job.get("benefits") or [],
        "description_summary": job.get("description_summary") or job.get("description") or "",
        "remote": "remote" in location or "upwork.com" in (job.get("url") or ""),
        "enriched_from_page": False,
    }


def enrich_job(
    llm, model: str, job: dict, skip_link_domains: list[str],
    allow_hosts: list[str] | None = None,
) -> dict:
    # A source that already handed over the full posting has nothing to gain here
    # and something to lose. The remote JSON feeds carry 4,000-10,600 characters of
    # description inline, so following their link would pay an HTTP request and an
    # LLM call to re-derive text we were already given — and would overwrite it with
    # whatever the page happened to render.
    #
    # This has to be the job's own claim rather than a domain in skip_link_domains,
    # because these links do not share a domain: himalayas' applicationLink points
    # at whatever ATS the employer uses, and one listing in a single live run linked
    # to a Google Form. No hostname list can cover that.
    if job.get("full_posting"):
        return _normalize_no_link(job)

    url = (job.get("url") or "").strip()

    if not url or not url.startswith("http") or any(d in url for d in skip_link_domains):
        return _normalize_no_link(job)

    resolved = url
    try:
        # safe_get, not httpx.get: this URL came out of an email body, so whoever
        # sent the mail chose it. See safefetch for what that means on a VPS.
        resp = safe_get(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
            timeout=15,
            allow_hosts=allow_hosts,
        )
        # Boards mail 300-character tracking links that redirect to a short
        # canonical page. Keep the destination even when the page itself refuses
        # us: it is the link a human actually wants, and it is far shorter.
        resolved = strip_tracking_params(str(resp.url) or url)
        job = {**job, "url": resolved}
        resp.raise_for_status()
        page_text = html_to_text(resp.text, limit=5000)
    except UnsafeURL as exc:
        # Not a network problem. Either a board changed its links, or something
        # aimed this program at an address it has no business fetching.
        logger.warning("enrich: refused to fetch %s — %s", resolved, exc)
        return _normalize_no_link(job)
    except Exception as exc:
        logger.info("enrich: could not fetch %s (%s)", resolved, exc)
        return _normalize_no_link(job)

    if not page_text.strip():
        return _normalize_no_link(job)

    details = llm.complete_json(
        model,
        SYSTEM,
        f"Extract details from this job page:\n\n{page_text}",
        max_tokens=1500,
    )
    if not details:
        return _normalize_no_link(job)

    return {
        **job,
        "title": details.get("title") or job.get("title"),
        "company": details.get("company") or job.get("company"),
        "location": details.get("location") or job.get("location"),
        "salary": details.get("salary") or job.get("salary"),
        "skills_required": details.get("skills_required") or [],
        "job_type": details.get("job_type") or job.get("job_type"),
        "experience_level": details.get("experience_level") or "unknown",
        "responsibilities": details.get("responsibilities") or [],
        "requirements": details.get("requirements") or [],
        "benefits": details.get("benefits") or [],
        "description_summary": details.get("description_summary") or job.get("description") or "",
        "duration": details.get("duration") or job.get("duration"),
        "remote": bool(details.get("remote")) or "remote" in (job.get("location") or "").lower(),
        "enriched_from_page": True,
        # The page was fetched, distilled into the fields above and then thrown
        # away, which left description_summary (a sentence or two) as the only
        # posting text anything downstream could see. Drafting an application off
        # that is drafting blind. Kept on the record, so it reaches the database
        # but not the sheet or the CSV, both of which name their columns.
        "full_text": page_text,
    }
