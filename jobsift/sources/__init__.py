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


# How often a non-email source is read, when it does not say otherwise. The inbox
# is polled every poll_interval_seconds because IMAP is cheap and local; a job
# board is neither, and asking one 288 times a day to find the ~30 jobs it posted
# is both wasteful and rude to a service giving the data away.
#
# 20 minutes is a deliberate compromise, not a shrug: applying early is a real
# advantage, so the loop still notices a new posting within a third of an hour,
# while the request count falls from ~9,200 a day to a few hundred.
DEFAULT_SCRAPE_INTERVAL = 1200


def collect(config, store=None) -> list[dict]:
    """Run every enabled scrape source that is due. Never raises — a broken adapter
    must not take down the email pipeline, which is the part that always works.

    `store` carries the last-run time per source. Without one every source runs on
    every call, which is the old behaviour and what the dry-run scripts want.
    """
    jobs: list[dict] = []
    sources = getattr(config, "scrape_sources", {}) or {}
    default_interval = float(
        getattr(config, "scrape_interval_seconds", None) or DEFAULT_SCRAPE_INTERVAL
    )

    for name, settings in sources.items():
        if not (settings or {}).get("enabled"):
            continue

        interval = float((settings or {}).get("interval_seconds") or default_interval)
        if store is not None:
            waited = store.seconds_since_scrape(name)
            if waited < interval:
                logger.debug(
                    "source %s: not due for another %.0fs", name, interval - waited
                )
                continue
        try:
            # Lets an adapter stop paging once it reaches listings we already
            # have. Purely an efficiency signal — nothing is filtered by it.
            def _seen(job, _s=store):
                if _s is None:
                    return False
                from ..utils import job_key
                return _s.is_job_seen(job_key(job))

            if name == "onlinejobs_ph":
                from .onlinejobs import fetch_jobs

                found = fetch_jobs(settings)
            elif name == "jobstreet_api":
                from .jobstreet import fetch_jobs

                found = fetch_jobs(settings, is_seen=_seen)
            elif name == "remote_feeds":
                from .remote_feeds import fetch_jobs

                found = fetch_jobs(settings)
            else:
                logger.warning("Unknown scrape source %r in config — skipping", name)
                continue
            logger.info("source %s: %d job(s)", name, len(found))
            jobs.extend(found)
        except Exception:
            logger.exception("Scrape source %s failed — continuing without it", name)
        finally:
            # Marked even on failure, so a source that is down does not get
            # retried every 5 minutes for as long as it stays down.
            if store is not None:
                store.mark_scraped(name)

    return jobs
