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
from .sources import finish as finish_scraped

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
        # The employer as the POSTING names itself, kept beside the board's own
        # field rather than folded into it. `company` is load-bearing: job_key
        # falls back to the URL slug when it is empty, which is what keeps two
        # same-titled onlinejobs postings apart, so writing a name into it would
        # re-key every stored row and re-alert the lot. This column is the one
        # that answers "who is this" for the 142 rows whose board never said.
        "employer_name": score.get("employer_name") or "N/A",
        # Stable numeric id from the posting page, where the board exposes one.
        # Deterministic and free, and it groups an employer's postings together
        # even when nobody writes a name in the text.
        "employer_id": job.get("employer_id") or "",
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
        # "held" means pay and priority had carried this past the threshold on a
        # fit the scorer had already called too low. Recorded rather than silent,
        # so a capped total reads as a decision instead of a near-miss.
        "fit_gated": "held" if score.get("fit_gated") else "",
        "priority_hits": ", ".join(score.get("priority_hits") or []),
        "skill_match": f"{score['skills_score']}/30",
        "experience_fit": f"{score['experience_score']}/30",
        "interest_fit": f"{score['salary_score']}/40",
        "matching_skills": ", ".join(score.get("matching_skills") or []) or "N/A",
        "missing_skills": ", ".join(score.get("missing_skills") or []) or "N/A",
        "reasoning": score.get("reasoning") or "N/A",
        # Not a column anywhere; see enrich. Present only for --draft.
        "full_text": job.get("full_text") or "",
        "status": "new",
        "reported_at": datetime.now().isoformat(timespec="seconds"),
    }


def _record_applications(config, store, sheet, messages) -> set[str]:
    """Tick jobs the boards have confirmed an application for. Returns the uids
    of the receipts, so the caller can file them without classifying them.

    Runs over the messages already fetched, before classify() sees them. A
    receipt is not a job alert, but it comes from a board's own domain, so
    classify takes it for one and pays the extract model to find no jobs in it.
    That is what happened to every Indeed receipt until 2026-09-11, while this
    docstring already said they were dropped. Costs one regex pass, no LLM call.
    """
    from .applied import detect, match_to_jobs
    from .export import rows as stored_rows

    found = [(m["uid"], c) for m in messages if (c := detect(m))]
    if not found:
        return set()
    confirmations = [c for _, c in found]

    records = stored_rows(config.database_path, settings=config.filters)
    matched, unmatched = match_to_jobs(confirmations, records)
    fresh = [url for url, c in matched.items() if store.mark_applied(url, c)]

    for c in unmatched:
        logger.info("  application with no stored job: %s%s",
                    c.title, f" @ {c.company}" if c.company else "")
    if fresh:
        logger.info("Marked %d job(s) applied from inbox confirmations", len(fresh))
        if sheet is not None:
            try:
                sheet.mark_applied(store.applied_urls())
            except Exception:
                logger.exception("Could not tick applied in the sheet")
    return {uid for uid, _ in found}


def run_once(config, llm, store, gmail, resume, sheet=None, telegram_send=None) -> int:
    """Process the inbox once. Returns the number of new jobs handled."""
    first_run = store.is_fresh_install()
    lookback = config.first_run_lookback_days if first_run else config.lookback_days
    if first_run:
        logger.info("Fresh install — backfilling %d day(s) of inbox history", lookback)
    # Only mail not yet processed is downloaded. Every pass used to pull the whole
    # lookback window in full, 288 times a day, and then skip most of it.
    messages = gmail.fetch_recent(
        lookback_days=lookback, wanted=lambda uid: not store.is_email_processed(uid))
    logger.info("Fetched %d new inbox message(s) (lookback %dd)", len(messages), lookback)
    receipts = _record_applications(config, store, sheet, messages)
    handled = 0

    for msg in messages:
        if store.is_email_processed(msg["uid"]):
            continue
        if msg["uid"] in receipts:
            store.mark_email_processed(msg["uid"])
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
                config.min_fit_ratio, config.score_threshold,
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
                config.min_fit_ratio, config.score_threshold,
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

    # Only now has every delivered worker batch been handled (worker.take_inbox).
    finish_scraped(config)
    store.cleanup()
    return handled
