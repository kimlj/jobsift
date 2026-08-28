"""Optional non-email job sources.

The email path is the project's default and its argument: boards email you, so
nothing has to be scraped. Some boards offer no email alerts at all, and this
package is the opt-in escape hatch for those. Every adapter here returns the
same job dicts `extract.extract_jobs` produces, so everything downstream —
dedup, enrich, score, notify, store — is unchanged and source-agnostic.

Adapters are OFF by default and must be enabled per-source in config.yaml.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def collect(config) -> list[dict]:
    """Run every enabled scrape source. Never raises — a broken adapter must not
    take down the email pipeline, which is the part that always works."""
    jobs: list[dict] = []
    sources = getattr(config, "scrape_sources", {}) or {}

    for name, settings in sources.items():
        if not (settings or {}).get("enabled"):
            continue
        try:
            if name == "onlinejobs_ph":
                from .onlinejobs import fetch_jobs

                found = fetch_jobs(settings)
            else:
                logger.warning("Unknown scrape source %r in config — skipping", name)
                continue
            logger.info("source %s: %d job(s)", name, len(found))
            jobs.extend(found)
        except Exception:
            logger.exception("Scrape source %s failed — continuing without it", name)

    return jobs
