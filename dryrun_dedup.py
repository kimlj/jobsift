"""Dry run of the dedup key — writes nothing, costs nothing, no network, no LLM.

Jobs are deduped on a normalised title::company. The risk is asymmetric and worth
stating: a MISSED duplicate costs one extra line in Telegram, while a WRONG merge
silently loses a real job and nothing reports it. So this checks both directions —
the pairs that must collapse, and the pairs that must stay apart.

Pass 3 replays every job in the real database through the key and reports any group
the key would now merge, so a normalisation change can be eyeballed against real
employer names before it goes anywhere near the pipeline.

    .venv\\Scripts\\python.exe dryrun_dedup.py
"""

from __future__ import annotations

import collections
import json
import sqlite3

from jobsift.utils import job_key, normalize_company

# (job a, job b, should they be the same job?)
PAIRS = [
    # Must collapse — one employer, written two ways. Both of these are real, and
    # both arrived in a single Jobstreet run.
    ({"title": "Senior Data Engineer", "company": "WeSupport Incorporated"},
     {"title": "Senior Data Engineer", "company": "WeSupport, Inc."}, True),
    ({"title": "Business Intelligence Developer", "company": "NightOwl Consulting Philippines, Inc"},
     {"title": "Business Intelligence Developer", "company": "Nightowl Consulting"}, True),
    ({"title": "Full Stack Developer", "company": "IT Managers Inc."},
     {"title": "Full Stack Developer", "company": "IT Managers, Inc."}, True),
    ({"title": "Data Engineer", "company": "REALPAGE (PHILIPPINES) INC."},
     {"title": "Data Engineer", "company": "RealPage"}, True),
    ({"title": "Backend Engineer", "company": "Accenture in the Philippines"},
     {"title": "Backend Engineer", "company": "Accenture"}, True),
    ({"title": "DevOps Engineer", "company": "GECO Asia Pte. Ltd"},
     {"title": "DevOps  Engineer ", "company": "GECO Asia"}, True),

    # Must NOT collapse — different employers or different openings. The words that
    # look corporate but carry identity are the trap here.
    ({"title": "Software Engineer", "company": "ATOMIT Corp."},
     {"title": "Software Engineer", "company": "ATOMIT Business Solutions Corp"}, False),
    ({"title": "Software Engineer", "company": "AS White Global"},
     {"title": "Software Engineer", "company": "AS White"}, False),
    ({"title": "Data Engineer", "company": "Acme Solutions"},
     {"title": "Data Engineer", "company": "Acme Technologies"}, False),
    ({"title": "Senior AI Engineer - Hybrid", "company": "enablesGROUP"},
     {"title": "Senior AI Engineer", "company": "enablesGROUP"}, False),
    ({"title": "Backend Engineer", "company": "Acme"},
     {"title": "Frontend Engineer", "company": "Acme"}, False),
    # No employer named: the URL slug stands in, so two different postings sharing
    # a title must stay distinct.
    ({"title": "Web Developer", "company": "", "url": "https://www.onlinejobs.ph/jobseekers/job/alpha-1"},
     {"title": "Web Developer", "company": "", "url": "https://www.onlinejobs.ph/jobseekers/job/beta-2"}, False),
]

print("── pairs ──")
failed = 0
for a, b, same in PAIRS:
    ka, kb = job_key(a), job_key(b)
    ok = (ka == kb) == same
    failed += not ok
    verdict = "same" if ka == kb else "distinct"
    print(f"{'ok  ' if ok else 'FAIL'} {verdict:<8} "
          f"{a['company'] or a.get('url', '')[:28]:<38.38} | "
          f"{b['company'] or b.get('url', '')[:28]:<38.38} -> {ka if ka == kb else ''}")
print(f"\n{len(PAIRS) - failed}/{len(PAIRS)} as expected")

print("\n── the real database, replayed through the key ──")
try:
    rows = sqlite3.connect("data/jobs.db").execute("select data from jobs").fetchall()
except sqlite3.Error as exc:
    print(f"  no readable database ({exc})")
    raise SystemExit(1 if failed else 0)

groups = collections.defaultdict(list)
for (data,) in rows:
    j = json.loads(data)
    groups[job_key({"title": j.get("job_title"), "company": j.get("company"),
                    "url": j.get("url")})].append((j.get("job_title"), j.get("company")))

merged = {k: v for k, v in groups.items() if len(v) > 1}
print(f"  {len(rows)} stored jobs -> {len(groups)} distinct keys, {len(merged)} group(s) with more than one row")
for key, members in merged.items():
    print(f"\n  {key!r}")
    for title, company in members:
        print(f"     {title}  @  {company}")
if not merged:
    print("  (no collisions among stored jobs)")

print("\n── how employer names normalise, worst offenders in the database ──")
names = sorted({c for _, c in ((j.get("job_title"), json.loads(d).get("company"))
                               for d, j in ((d, json.loads(d)) for (d,) in rows)) if c})
shown = [(n, normalize_company(n)) for n in names if n.lower() != normalize_company(n)]
for before, after in shown[:12]:
    print(f"  {before!r:48} -> {after!r}")
print(f"  ... {len(shown)} of {len(names)} employer names normalise to something shorter")

# The database passes are for reading; only the pairs are a verdict.
raise SystemExit(1 if failed else 0)
