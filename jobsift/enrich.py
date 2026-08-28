"""Stage 2 — follow the job link and read structured details off the page (LLM)."""

from __future__ import annotations

import logging

import httpx

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


def enrich_job(llm, model: str, job: dict, skip_link_domains: list[str]) -> dict:
    url = (job.get("url") or "").strip()
    if not url or not url.startswith("http") or any(d in url for d in skip_link_domains):
        return _normalize_no_link(job)

    resolved = url
    try:
        resp = httpx.get(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
            timeout=15,
            follow_redirects=True,
        )
        # Boards mail 300-character tracking links that redirect to a short
        # canonical page. Keep the destination even when the page itself refuses
        # us: it is the link a human actually wants, and it is far shorter.
        resolved = strip_tracking_params(str(resp.url) or url)
        job = {**job, "url": resolved}
        resp.raise_for_status()
        page_text = html_to_text(resp.text, limit=5000)
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
    }
