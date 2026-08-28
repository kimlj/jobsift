"""Dry run of the onlinejobs.ph path — mirrors pipeline.py, writes nothing.

Same order as the real run: fetch -> dedup -> pre-score filters -> score ->
post-score filters -> would-save / would-alert. The differences are deliberate
and total: no `store.save_job`, no `store.mark_job_seen`, no Sheet append, no
Telegram send. Nothing here can change what the real pipeline does next.

    .venv\\Scripts\\python.exe dryrun_onlinejobs.py [keyword ...] [--pages N] [--limit N]

Defaults to two keywords and one page so a look costs a couple of minutes and a
few cents rather than half an hour.
"""

from __future__ import annotations

import logging
import sys

from jobsift.config import load_config
from jobsift.filters import check as passes_filters
from jobsift.llm import build_llm
from jobsift.pipeline import _build_record, _job_key
from jobsift.score import score_job
from jobsift.sources.onlinejobs import fetch_jobs
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
words = [a for a in sys.argv[1:] if not a.startswith("--") and not a.isdigit()]

config = load_config("config.yaml", ".env")
settings = dict((config.scrape_sources or {}).get("onlinejobs_ph") or {})
if words:
    settings["search_keywords"] = words
settings["max_pages"] = pages
settings["fetch_details"] = True

print(f"\nDRY RUN — onlinejobs.ph  (nothing is saved, nothing is sent)")
print(f"keywords: {settings.get('search_keywords')}")
print(f"pages per keyword: {pages} | scoring at most {limit} jobs\n")

jobs = fetch_jobs({**settings, "fetch_details": False})

# Dedup exactly as the pipeline would, so the dry run does not flatter itself by
# counting jobs the real run would skip as already seen.
seen: set[str] = set()
fresh = []
for job in jobs:
    key = _job_key(job)
    if key == "::" or key in seen:
        continue
    seen.add(key)
    fresh.append(job)

print(f"{len(jobs)} kept by the title gate, {len(fresh)} distinct\n")

# Pre-score filters before paying for detail fetches.
survivors = []
for job in fresh:
    keep, why = passes_filters(job, config.filters)
    if not keep:
        print(f"  filtered  {str(job.get('title'))[:52]:52} — {why}")
        continue
    survivors.append(job)

print(f"\n{len(survivors)} past the hard filters; fetching details for the first {min(limit, len(survivors))}\n")

from jobsift.sources.onlinejobs import _fetch_description, USER_AGENT  # noqa: E402
import httpx, time  # noqa: E402

batch = survivors[:limit]
with httpx.Client(timeout=30, headers={"User-Agent": USER_AGENT}, follow_redirects=True) as client:
    for job in batch:
        if not job.get("url"):
            continue
        time.sleep(float(settings.get("delay_seconds", 5)))
        full = _fetch_description(client, job["url"])
        if full and len(full) > len(job.get("description") or ""):
            job["description"] = full

llm = build_llm(
    config.llm_provider,
    openai_api_key=config.openai_api_key,
    anthropic_api_key=config.anthropic_api_key,
)
resume = open(config.resume_path, encoding="utf-8").read()

would_save, would_alert, dropped = [], [], []
for job in batch:
    score = score_job(
        llm, config.models["score"], job, resume, config.salary_baseline_php,
        config.priority_keywords, config.priority_points,
    )
    keep, why = passes_filters({**job, "degree_required": score.get("degree_required")}, config.filters)
    record = _build_record(job, score, job.get("source") or "onlinejobs_ph", job.get("posted") or "")
    if not keep:
        dropped.append((record, why))
        continue
    would_save.append(record)
    if record["score"] >= config.score_threshold:
        would_alert.append(record)

print("\n" + "=" * 78)
print(f"WOULD SAVE {len(would_save)} · WOULD ALERT {len(would_alert)} (threshold {config.score_threshold})")
print("=" * 78)
for r in sorted(would_save, key=lambda r: -r["score"]):
    flag = "ALERT" if r["score"] >= config.score_threshold else "     "
    print(f"{flag} {r['score']:>3}/100  {r['evidence']:<8} {r['evidence_chars']:>5}ch  "
          f"{str(r['job_title'])[:44]:44} {str(r['salary'])[:22]}")
    print(f"         degree={r['degree_required']}  skills {r['skill_match']} exp {r['experience_fit']} pay {r['interest_fit']}")
    print(f"         match: {str(r['matching_skills'])[:100]}")

if dropped:
    print("\nDROPPED AFTER SCORING:")
    for r, why in dropped:
        print(f"  {r['score']:>3}/100  {str(r['job_title'])[:48]:48} — {why}")

print("\nNothing was written to the database and nothing was sent to Telegram.")
