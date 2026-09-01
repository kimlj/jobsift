"""Every job every source returns, with the verdict on each. One CSV, for Excel.

The other dry runs each answer one question. This one answers "what is actually
going on" — it fetches from every non-email source, runs the real hard filters, and
writes one row per job with a `verdict` column saying kept, or exactly why not.

Filter the `verdict` column in Excel:
    kept                 what would be scored and could reach Telegram
    no salary listed     dropped by drop_when_salary_unknown
    title matches ...    dropped by exclude_titles (jr/senior/...)
    company matches ...  dropped by exclude_companies
    salary ~X above/below   outside min_salary_php / max_salary_php
    eligibility / remote but ... / on-site in ...   dropped by the geographic rule

Nothing is written to the database, no LLM is called, and enabled/disabled in
config is ignored — this reads the sources whether or not they are switched on.

    .venv\\Scripts\\python.exe dryrun_sources.py
    .venv\\Scripts\\python.exe dryrun_sources.py --csv jobs-all.csv
    .venv\\Scripts\\python.exe dryrun_sources.py --kept-only --csv passed.csv
"""

from __future__ import annotations

import collections
import csv
import logging
import sys

from jobsift.config import load_config
from jobsift.filters import check, currency_for, normalize_salary_php
from jobsift.pipeline import _job_key
from jobsift.sources.jobstreet import fetch_jobs as jobstreet
from jobsift.sources.remote_feeds import fetch_jobs as feeds
from jobsift.utils import SNIPPET_CHARS, evidence_chars

logging.basicConfig(level=logging.INFO, format="%(message)s")
for noisy in ("httpx", "httpcore"):
    logging.getLogger(noisy).setLevel(logging.WARNING)


def _opt(flag, default=""):
    return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv else default


csv_path = _opt("--csv")
kept_only = "--kept-only" in sys.argv
config = load_config("config.yaml")

jobs = feeds(config.scrape_sources.get("remote_feeds") or {}) + jobstreet(
    config.scrape_sources.get("jobstreet_api") or {}
)

# Dedup exactly as the pipeline would, so the sheet matches what a run would do.
seen, unique = set(), []
for job in jobs:
    key = _job_key(job)
    if key == "::" or key in seen:
        continue
    seen.add(key)
    unique.append(job)

rows, verdicts = [], collections.Counter()
for job in unique:
    keep, why = check(job, config.filters)
    verdict = "kept" if keep else why
    verdicts[verdict.split(" ~")[0].split(" matches")[0]] += 1
    if kept_only and not keep:
        continue
    money = normalize_salary_php(
        job.get("salary") or "",
        job_type=job.get("job_type") or "",
        usd_to_php=config.filters.get("usd_to_php"),
        currency_rates=config.filters.get("currency_rates"),
        default_currency=currency_for(job),
    )
    chars = evidence_chars(job)
    rows.append({
        "verdict": verdict,
        "source": job.get("source", ""),
        "title": job.get("title", ""),
        "company": job.get("company", ""),
        "salary": job.get("salary", ""),
        "salary_php_monthly": round(money) if money else "",
        "location": job.get("location", ""),
        "eligible_for": job.get("candidate_location", ""),
        "work_arrangement": job.get("work_arrangement", ""),
        "job_type": job.get("job_type", ""),
        "posted": job.get("posted", ""),
        "evidence": "full" if chars >= SNIPPET_CHARS else "snippet",
        "evidence_chars": chars,
        "url": job.get("url", ""),
    })

print(f"\n{len(jobs)} fetched, {len(unique)} unique")
for verdict, n in verdicts.most_common():
    marker = "  <-- these would be scored" if verdict == "kept" else ""
    print(f"  {n:>4}  {verdict}{marker}")

by_source = collections.Counter(
    r["source"] for r in rows if r["verdict"] == "kept"
) if not kept_only else collections.Counter(r["source"] for r in rows)
if by_source:
    print("\nkept, by source: " + ", ".join(f"{k} {v}" for k, v in by_source.most_common()))

kept_rows = [r for r in rows if r["verdict"] == "kept"]
if kept_rows:
    print("\n── what passed ──")
    for r in sorted(kept_rows, key=lambda r: -(r["salary_php_monthly"] or 0)):
        money = f"{r['salary_php_monthly']:,}/mo" if r["salary_php_monthly"] else "unpriced"
        print(f"  {r['source']:<14} | {r['title'][:44]:<44} | {money:>14} | {r['evidence']}")
        print(f"                   {r['url']}")

if csv_path:
    names = list(rows[0]) if rows else ["verdict"]
    # Kept first, then the rejects grouped by reason — so the sheet opens on the
    # jobs worth reading rather than on the noise.
    rows.sort(key=lambda r: (r["verdict"] != "kept", r["verdict"], r["source"]))
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=names)
        writer.writeheader()
        for r in rows:
            writer.writerow({k: " ".join(str(r.get(k, "")).split()) for k in names})
    print(f"\nwrote {len(rows)} row(s) to {csv_path}")
