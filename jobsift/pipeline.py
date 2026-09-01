"""Orchestration: turn a batch of inbox emails into scored, stored, notified jobs."""

from __future__ import annotations

import logging
from datetime import datetime

from .classify import classify
from .enrich import enrich_job
from .extract import extract_jobs
from .filters import check as passes_filters
from .filters import is_remote
from .utils import job_key, strip_tracking_params
from .score import score_job
from .sources import collect as collect_scraped

logger = logging.getLogger(__name__)


# Defined in utils so `store` can reuse it for the key migration. Kept under this
# name because both dry-run scripts import it from here.
_job_key = job_key


def _build_record(job: dict, score: dict, source: str, email_date: str) -> dict:
    remote = is_remote(job) or "upwork.com" in (job.get("url") or "")
    now = datetime.now().isoformat(timespec="seconds")
    return {
        "timestamp": email_date or now,
        "source": source or "N/A",
        "job_title": job.get("title") or "N/A",
        "company": job.get("company") or "N/A",
        "location": job.get("location") or "N/A",
        "remote": "Yes" if remote else "No",
        # The board's own word for it, kept alongside the derived flag so a row can
        # be read back without re-deriving anything. Empty on sources that do not say.
        "work_arrangement": job.get("work_arrangement") or "",
        "salary": job.get("salary") or "N/A",
        "skills_required": ", ".join(job.get("skills_required") or []) or "N/A",
        "job_type": job.get("job_type") or "N/A",
        "experience_level": job.get("experience_level") or "N/A",
        "duration": job.get("duration") or "N/A",
        "url": strip_tracking_params((job.get("url") or "").strip()) or "N/A",
        "description_summary": job.get("description_summary") or job.get("description") or "N/A",
        "score": score["total"],
        # How much the score was allowed to see. Stored on the row so a job can
        # be re-read later without having to guess whether its number meant
        # anything.
        "evidence": score.get("evidence", "unknown"),
        "evidence_chars": score.get("evidence_chars", 0),
        "degree_required": score.get("degree_required", "unknown"),
        "priority_bonus": score.get("priority_bonus", 0),
        "priority_hits": ", ".join(score.get("priority_hits") or []),
        "skill_match": f"{score['skills_score']}/30",
        "experience_fit": f"{score['experience_score']}/30",
        "interest_fit": f"{score['salary_score']}/40",
        "matching_skills": ", ".join(score.get("matching_skills") or []) or "N/A",
        "missing_skills": ", ".join(score.get("missing_skills") or []) or "N/A",
        "reasoning": score.get("reasoning") or "N/A",
        "status": "new",
        "reported_at": datetime.now().isoformat(timespec="seconds"),
    }


def run_once(config, llm, store, gmail, resume, sheet=None, telegram_send=None) -> int:
    """Process the inbox once. Returns the number of new jobs handled."""
    first_run = store.is_fresh_install()
    lookback = config.first_run_lookback_days if first_run else config.lookback_days
    if first_run:
        logger.info("Fresh install — backfilling %d day(s) of inbox history", lookback)
    messages = gmail.fetch_recent(lookback_days=lookback)
    logger.info("Fetched %d inbox message(s) (lookback %dd)", len(messages), lookback)
    handled = 0

    for msg in messages:
        if store.is_email_processed(msg["uid"]):
            continue

        is_job, source = classify(
            msg["from"], msg["subject"], config.known_senders, config.job_subject_keywords
        )
        if not is_job:
            store.mark_email_processed(msg["uid"])
            continue

        logger.info("Job email from %s (%s): %s", msg["from"], source, msg["subject"][:80])
        jobs = extract_jobs(llm, config.models["extract"], msg["text"], source)
        logger.info("  extracted %d job(s)", len(jobs))

        for job in jobs:
            key = _job_key(job)
            if key == "::" or store.is_job_seen(key):
                continue
            store.mark_job_seen(key)

            keep, why = passes_filters(job, config.filters)
            if not keep:
                logger.info("  filtered: %s @ %s — %s", job.get("title"), job.get("company"), why)
                continue

            job = enrich_job(llm, config.models["enrich"], job, config.skip_link_domains,
                             allow_hosts=(config.filters.get("fetch") or {}).get("allow_hosts"))
            score = score_job(
            llm, config.models["score"], job, resume, config.salary_baseline_php,
                config.priority_keywords, config.priority_points,
            )
            keep, why = passes_filters({**job, "degree_required": score.get("degree_required")}, config.filters)
            if not keep:
                logger.info("  filtered after scoring: %s — %s", job.get("title"), why)
                continue

            record = _build_record(job, score, source, msg["date"])

            store.save_job(key, record)
            handled += 1

            if sheet is not None:
                try:
                    sheet.append(record)
                except Exception:
                    logger.exception("Sheet append failed for %s", record.get("job_title"))

            if telegram_send is not None and record["score"] >= config.score_threshold:
                telegram_send(record)

            logger.info("  saved: %s @ %s — %s/100", record["job_title"], record["company"], record["score"])

        store.mark_email_processed(msg["uid"])

    # Optional non-email sources (opt-in, off by default). They emit the same job
    # dicts the extractor does, so they reuse the identical tail of the pipeline.
    for job in collect_scraped(config, store):
        key = _job_key(job)
        if key == "::" or store.is_job_seen(key):
            continue
        store.mark_job_seen(key)

        keep, why = passes_filters(job, config.filters)
        if not keep:
            logger.info("  filtered: %s @ %s — %s", job.get("title"), job.get("company"), why)
            continue

        source = job.get("source") or "scraped"
        job = enrich_job(llm, config.models["enrich"], job, config.skip_link_domains,
                         allow_hosts=(config.filters.get("fetch") or {}).get("allow_hosts"))
        score = score_job(
            llm, config.models["score"], job, resume, config.salary_baseline_php,
                config.priority_keywords, config.priority_points,
            )
        keep, why = passes_filters({**job, "degree_required": score.get("degree_required")}, config.filters)
        if not keep:
            logger.info("  filtered after scoring: %s — %s", job.get("title"), why)
            continue

        record = _build_record(job, score, source, job.get("posted") or "")

        store.save_job(key, record)
        handled += 1

        if sheet is not None:
            try:
                sheet.append(record)
            except Exception:
                logger.exception("Sheet append failed for %s", record.get("job_title"))

        if telegram_send is not None and record["score"] >= config.score_threshold:
            telegram_send(record)

        logger.info("  saved: %s @ %s — %s/100", record["job_title"], record["company"], record["score"])

    store.cleanup()
    return handled
