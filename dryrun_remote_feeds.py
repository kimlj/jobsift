"""Dry run of the remote-feeds path — mirrors pipeline.py, writes nothing.

Same order as the real run: fetch -> dedup -> pre-score filters -> enrich -> score ->
post-score filters -> would-save / would-alert. The differences are deliberate and
total: no `store.save_job`, no `store.mark_job_seen`, no Sheet append, no Telegram
send. Nothing here can change what the real pipeline does next.

    .venv\\Scripts\\python.exe dryrun_remote_feeds.py [--limit N] [--feeds a,b]
    .venv\\Scripts\\python.exe dryrun_remote_feeds.py --no-score

--no-score stops after the hard filters, so it costs four HTTP calls and nothing
else. That is the mode to run when checking whether the geographic rule is reading
each feed's eligibility field correctly, which is this source's one real risk.
"""

from __future__ import annotations

import collections
import logging
import sys

from jobsift.config import load_config
from jobsift.enrich import enrich_job
from jobsift.filters import check as passes_filters
from jobsift.filters import geography_check, normalize_salary_php
from jobsift.llm import build_llm
from jobsift.pipeline import _build_record, _job_key
from jobsift.score import score_job
from jobsift.sources.remote_feeds import fetch_jobs
from jobsift.utils import SNIPPET_CHARS, evidence_chars

logging.basicConfig(level=logging.INFO, format="%(message)s")
for noisy in ("httpx", "httpcore", "anthropic"):
    logging.getLogger(noisy).setLevel(logging.WARNING)


def _opt(name, default):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


limit = int(_opt("--limit", 8))
scoring = "--no-score" not in sys.argv

config = load_config("config.yaml")
settings = dict(config.scrape_sources.get("remote_feeds") or {})
if "--feeds" in sys.argv:
    settings["feeds"] = [f.strip() for f in str(_opt("--feeds", "")).split(",") if f.strip()]

jobs = fetch_jobs(settings)

by_feed = collections.Counter(j["source"] for j in jobs)
print(f"\n{len(jobs)} listing(s): " + ", ".join(f"{k} {v}" for k, v in by_feed.most_common()))

seen, unique = set(), []
for job in jobs:
    key = _job_key(job)
    if key == "::" or key in seen:
        continue
    seen.add(key)
    unique.append(job)
print(f"{len(unique)} unique by normalised title::company\n")

# Geography on its own first — it is the rule this source lives or dies by, and
# seeing it separately from the salary and title rules is the point of the exercise.
geo_kept, geo_why = [], collections.Counter()
for job in unique:
    keep, why = geography_check(job, config.filters.get("geography") or {})
    geo_kept.append(job) if keep else geo_why.__setitem__(why, geo_why[why] + 1)

print("── geography ──")
for feed in by_feed:
    total = sum(1 for j in unique if j["source"] == feed)
    kept = sum(1 for j in geo_kept if j["source"] == feed)
    share = f"{100 * kept / total:.0f}%" if total else "-"
    print(f"  {feed:<15} {kept:>3}/{total:<4} eligible ({share})")
print(f"  {'TOTAL':<15} {len(geo_kept):>3}/{len(unique):<4}")
for why, n in geo_why.most_common(6):
    print(f"     dropped {n:>4}  {why}")

survivors, rejected = [], collections.Counter()
for job in unique:
    keep, why = passes_filters(job, config.filters)
    if keep:
        survivors.append(job)
    else:
        rejected[why.split(" ~")[0].split(" matches")[0]] += 1

print(f"\n── all hard filters ──\n  {len(survivors)}/{len(unique)} survive")
for why, n in rejected.most_common(8):
    print(f"     {n:>4}  {why}")

# These feeds carry the posting inline, so enrich should never fetch anything. If it
# does, a URL is being followed for text we were already given.
enriched = [enrich_job(None, "", job, config.skip_link_domains) for job in survivors]
print(f"\n  enrich fetched {sum(bool(j.get('enriched_from_page')) for j in enriched)} page(s)"
      " — should be 0, the descriptions came inline")
survivors = enriched

full = sum(evidence_chars(j) >= SNIPPET_CHARS for j in survivors)
print(f"  evidence: {full}/{len(survivors)} reach 'full' (>= {SNIPPET_CHARS} chars)")

print("\n── what survived ──")
for job in survivors[:25]:
    money = normalize_salary_php(
        job.get("salary") or "", usd_to_php=config.filters.get("usd_to_php"),
        currency_rates=config.filters.get("currency_rates"), job_type=job.get("job_type") or "",
    )
    print(f"  {job['source']:<14} | {job['title'][:38]:<38} | "
          f"{(job.get('candidate_location') or '?')[:22]:<22} | "
          f"{(f'{money:,.0f}/mo' if money else 'unpriced'):>13} | "
          f"{evidence_chars(job):>5} chars")

if not scoring:
    print("\n--no-score: stopping before the LLM. Nothing written, nothing spent.")
    sys.exit(0)

print(f"\n── scoring the first {min(limit, len(survivors))} ──")
llm = build_llm(config.llm_provider, openai_api_key=config.openai_api_key,
                anthropic_api_key=config.anthropic_api_key)
resume = open(config.resume_path, encoding="utf-8").read()

would_save = would_alert = 0
for job in survivors[:limit]:
    score = score_job(llm, config.models["score"], job, resume, config.salary_baseline_php,
                      config.priority_keywords, config.priority_points)
    keep, why = passes_filters({**job, "degree_required": score.get("degree_required")},
                               config.filters)
    if not keep:
        print(f"  dropped after scoring: {job['title'][:38]} — {why}")
        continue
    record = _build_record(job, score, job["source"], job.get("posted") or "")
    would_save += 1
    alert = record["score"] >= config.score_threshold
    would_alert += alert
    print(f"  {record['score']:>3}/100 {'ALERT' if alert else '     '} "
          f"{record['job_title'][:38]:<38} | {record['source']:<14} | "
          f"{record['evidence']} ({record['evidence_chars']} chars)")

print(f"\nwould save {would_save}, would alert {would_alert}. Nothing was written.")
