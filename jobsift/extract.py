"""Stage 1 — pull individual job listings out of an alert email (LLM)."""

from __future__ import annotations

import logging

from .utils import mask_urls, unmask_url

logger = logging.getLogger(__name__)

# Alert emails are mostly tracking URLs, so this is applied *after* masking —
# roughly double the useful content of the old 12k raw-character limit.
MAX_INPUT_CHARS = 24000

SYSTEM = """You are a job listing extractor. You receive email text from job alert emails and extract individual job listings into structured JSON.

Rules:
- ONLY extract actual job listings. Ignore email headers, navigation links, footers, and summary lines.
- Metadata tags like "Easily apply" and "Responsive employer" are not part of job details.

For each job, extract:
- title: The job title
- company: Company name
- location: City/location
- salary: Salary if listed, otherwise empty string
- description: The short description snippet
- posted: How long ago it was posted, otherwise empty string
- url: The URL token for that job, copied exactly as it appears in the email
  (e.g. "[URL7]"). Never invent a token and never write out a full URL.

Return raw JSON only — no markdown, no prose — with this exact structure:
{"jobs": [{"title": "", "company": "", "location": "", "salary": "", "description": "", "posted": "", "url": ""}]}

If no jobs are found, return: {"jobs": []}"""


def extract_jobs(llm, model: str, email_text: str, source: str) -> list[dict]:
    """Extract a list of job dicts from one alert email."""
    if not email_text.strip():
        return []

    masked, urls = mask_urls(email_text)
    if len(masked) > MAX_INPUT_CHARS:
        logger.warning(
            "Email is %d chars after URL masking — truncating to %d, later jobs will be missed",
            len(masked),
            MAX_INPUT_CHARS,
        )

    data = llm.complete_json(
        model,
        SYSTEM,
        f"Extract all job listings from this email:\n\n{masked[:MAX_INPUT_CHARS]}",
        max_tokens=8000,
    )

    jobs = data.get("jobs", []) if isinstance(data, dict) else []
    for job in jobs:
        if isinstance(job, dict):
            job["source"] = source
            job["url"] = unmask_url(job.get("url", ""), urls)
    return [j for j in jobs if isinstance(j, dict) and (j.get("title") or "").strip()]
