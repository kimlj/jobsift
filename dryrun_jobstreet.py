"""Dry run of the Jobstreet PH path — mirrors pipeline.py, writes nothing.

Same order as the real run: fetch -> dedup -> pre-score filters -> score ->
post-score filters -> would-save / would-alert. The differences are deliberate and
total: no `store.save_job`, no `store.mark_job_seen`, no Sheet append, no Telegram
send. Nothing here can change what the real pipeline does next.

    .venv\\Scripts\\python.exe dryrun_jobstreet.py [keyword ...] [--pages N] [--limit N]
    .venv\\Scripts\\python.exe dryrun_jobstreet.py --no-score

--no-score stops after the hard filters, so the whole thing is free: the API call
costs nothing and the LLM is never reached. Use it to check the geography and salary
rules against live listings before spending anything on scoring.

Defaults to two keywords and one page so a look costs a couple of minutes and a few
cents rather than half an hour.
"""

from __future__ import annotations

import collections
import logging
import sys

from jobsift.config import load_config
from jobsift.enrich import enrich_job
from jobsift.filters import check as passes_filters
from jobsift.filters import normalize_salary_php
from jobsift.llm import build_llm
from jobsift.pipeline import _build_record, _job_key
from jobsift.score import score_job
from jobsift.sources.jobstreet import fetch_jobs
from jobsift.utils import SNIPPET_CHARS, evidence_chars

logging.basicConfig(level=logging.INFO, format="%(message)s")
for noisy in ("httpx", "httpcore", "anthropic"):
    logging.getLogger(noisy).setLevel(logging.WARNING)


def _opt(name: str, default: int) -> int:
    if name in sys.argv:
        return int(sys.argv[sys.argv.index(name) + 1])
    return default


pages = _opt("--pages", 1)
limit = _opt("--limit", 12)
scoring = "--no-score" not in sys.argv
words = [a for a in sys.argv[1:] if not a.startswith("--") and not a.isdigit()]

config = load_config("config.yaml")
settings = dict(config.scrape_sources.get("jobstreet_api") or {})
settings["max_pages"] = pages
if words:
    settings["search_keywords"] = words

print(f"keywords: {settings.get('search_keywords')}")
print(f"arrangements: {settings.get('work_arrangements') or '(all)'}   pages: {pages}\n")

jobs = fetch_jobs(settings)

# Dedup exactly as the pipeline would, against the real database.
seen_keys, unique = set(), []
for job in jobs:
    key = _job_key(job)
    if key == "::" or key in seen_keys:
        continue
    seen_keys.add(key)
    unique.append(job)
print(f"\n{len(jobs)} fetched, {len(unique)} unique by title::company\n")

survivors, rejected = [], collections.Counter()
for job in unique:
    keep, why = passes_filters(job, config.filters)
    if keep:
        survivors.append(job)
    else:
        rejected[why.split(" ~")[0].split(" matches")[0]] += 1
        print(f"  filtered: {job['title'][:44]:<44} @ {job['company'][:22]:<22} — {why}")

print(f"\n{len(survivors)} past the hard filters, {len(unique) - len(survivors)} dropped")
for why, n in rejected.most_common():
    print(f"   {n:>3}  {why}")

# The pipeline enriches between the two filter passes, so this does too. For these
# jobs it is a no-op by design — ph.jobstreet.com is in skip_link_domains because the
# page is 403 — and running it here is what proves that, rather than assuming it.
# A skipped domain never reaches the LLM, so passing None for it is safe.
enriched = [enrich_job(None, "", job, config.skip_link_domains) for job in survivors]
fetched = sum(bool(j.get("enriched_from_page")) for j in enriched)
print(f"\nenrich: {fetched}/{len(enriched)} pages fetched"
      f" ({'skipped as configured' if not fetched else 'UNEXPECTED — check skip_link_domains'})")
survivors = enriched

print("\n── what survived, and what we know about it ──")
for job in survivors:
    money = normalize_salary_php(
        job.get("salary") or "",
        usd_to_php=config.filters.get("usd_to_php"),
        job_type=job.get("job_type") or "",
    )
    chars = evidence_chars(job)
    print(
        f"  {job['title'][:40]:<40} | {job['company'][:20]:<20} | "
        f"{(job.get('work_arrangement') or '?'):<8} | "
        f"{(f'{money:,.0f}/mo' if money else 'unpriced'):>12} | "
        f"{chars:>4} chars {'(snippet)' if chars < SNIPPET_CHARS else '(full)'}"
    )

if not scoring:
    print("\n--no-score: stopping before the LLM. Nothing was written and nothing cost anything.")
    sys.exit(0)

print(f"\n── scoring the first {min(limit, len(survivors))} ──")
llm = build_llm(
    config.llm_provider,
    openai_api_key=config.openai_api_key,
    anthropic_api_key=config.anthropic_api_key,
)
resume = open(config.resume_path, encoding="utf-8").read()

would_save = would_alert = 0
for job in survivors[:limit]:
    score = score_job(
        llm, config.models["score"], job, resume, config.salary_baseline_php,
        config.priority_keywords, config.priority_points,
    )
    keep, why = passes_filters({**job, "degree_required": score.get("degree_required")},
                               config.filters)
    if not keep:
        print(f"  dropped after scoring: {job['title'][:40]} — {why}")
        continue
    record = _build_record(job, score, job.get("source") or "jobstreet_api", job.get("posted") or "")
    would_save += 1
    alert = record["score"] >= config.score_threshold
    would_alert += alert
    print(
        f"  {record['score']:>3}/100 {'ALERT' if alert else '     '} "
        f"{record['job_title'][:40]:<40} | {record['evidence']}"
        f" ({record['evidence_chars']} chars)"
    )

print(f"\nwould save {would_save}, would alert {would_alert}. Nothing was written.")
