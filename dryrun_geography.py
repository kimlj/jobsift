"""Dry run of the geographic rule — writes nothing, costs nothing, no LLM.

Three passes, in increasing order of how much they can surprise you:

  1. a table of hand-written cases, each with the rule it is there to pin down;
  2. every job already in the database, which must come out unchanged — the rule
     was written after those rows and is not allowed to disagree with them;
  3. the live public remote feeds, unauthenticated GETs, so the eligibility share
     can be re-measured rather than remembered. The numbers move as boards post.

    .venv\\Scripts\\python.exe dryrun_geography.py [--offline]

--offline skips pass 3 and the four network calls with it.
"""

from __future__ import annotations

import collections
import json
import sqlite3
import sys
import time
import urllib.request

from jobsift.filters import geography_check

CASES = [
    # (job, expect_keep, what it pins down)
    ({"location": "Makati City, Metro Manila"}, True, "rule 1, PH on-site"),
    ({"location": "Metro Manila (Hybrid)"}, True, "rule 1, PH hybrid is still fine"),
    ({"location": "Work from Home"}, True, "PH board, names no place"),
    ({"location": "N/A"}, True, "rule 4"),
    ({"location": ""}, True, "rule 4, empty"),
    ({"location": "Ugong, Pasig City, Metro Manila"}, True, "rule 1"),
    ({"location": "Fort Bonifacio"}, True, "barangay, caught on 'bonifacio'"),
    ({"location": "Salcedo Village"}, True, "barangay in no list at all — rule 4 keeps it"),
    ({"location": "Cebu", "country_code": "PH"}, True, "SEEK PH-Main"),

    ({"location": "Anywhere"}, True, "rule 2, open"),
    ({"location": "Worldwide", "remote": True}, True, "rule 2, open"),
    ({"location": "Remote", "candidate_location": "APAC"}, True, "rule 2, our region"),
    ({"location": "Remote", "candidate_location": "Philippines"}, True, "rule 2, named"),
    ({"location": "Remote (US, Canada or Philippines)"}, True, "home beats foreign"),
    ({"location": "Europe, North America, Latin America, APAC"}, True,
     "the multi-region posting: 'apac' must beat 'america'"),
    ({"location": "APAC,Middle East"}, True, "rule 2"),

    ({"location": "Anywhere in the United States", "remote": True}, False,
     "foreign beats open, or 'anywhere' rescues a US-only job"),
    ({"location": "Anywhere (working US business hours)", "remote": True}, False,
     "same trap, as Working Nomads actually writes it"),
    ({"location": "Remote", "candidate_location": "USA Only"}, False, "remotive"),
    ({"location": "Europe", "remote": True}, False, "workingnomads is EMEA-heavy"),
    ({"location": "Remote", "candidate_location": "Time zone: CET (+/- 3 hours)"}, False,
     "himalayas timezoneRestrictions"),
    ({"location": "the EU, the US, Canada, the UK, Australia, Singapore", "remote": True},
     False, "multi-region, but none of them ours"),
    ({"location": "Berlin, Germany"}, False, "rule 3, foreign on-site"),
    ({"location": "Singapore", "work_arrangement": "Hybrid"}, False, "rule 3, hybrid abroad"),
    ({"location": "Hybrid remote in London, UK"}, False, "hybrid is read before remote"),
    ({"location": "Sydney", "country_code": "AU"}, False, "rule 3 via country code"),
    ({"location": "Remote", "country_code": "MY"}, False, "SEEK MY-Main, remote but not ours"),

    # An explicit eligibility field is read strictly — a country nobody thought to
    # list is still a closed door. These five are real himalayas values.
    ({"location": "Remote", "candidate_location": "Costa Rica"}, False,
     "unlisted country, and the feed was explicit about it"),
    ({"location": "Remote", "candidate_location": "Bosnia and Herzegovina"}, False,
     "same, and no FOREIGN_TERMS list would ever carry it"),
    ({"location": "Remote", "candidate_location": "Cayman Islands"}, False, "same"),
    ({"location": "Remote", "candidate_location": "UTC-10, UTC-8, UTC-5, UTC+14"}, False,
     "restricted by clock: no overlap with UTC+8"),
    ({"location": "Remote", "candidate_location": "UTC+7, UTC+8, UTC+9"}, True,
     "restricted by clock, and we are inside it"),
    ({"location": "Remote", "candidate_location": "LATAM, Europe, USA, Canada, APAC"}, True,
     "strict, but 'apac' is still ours"),
]

UA = {"User-Agent": "jobsift/0.1 (+personal job filter)"}

# Each feed states eligibility in its own field, and the lambda is the whole of what
# an adapter has to do about it: put that field in `candidate_location`, which the
# filter then reads strictly. Getting this mapping wrong is the difference between a
# 4% leak and none — Working Nomads' restriction lives in a field named "location",
# so copying it to `location` alone would send it down the forgiving path.
FEEDS = {
    "remotive": (
        "https://remotive.com/api/remote-jobs",
        lambda d: [
            {"title": j.get("title"), "remote": True,
             "location": j.get("candidate_required_location"),
             "candidate_location": j.get("candidate_required_location")}
            for j in d["jobs"]
        ],
    ),
    "workingnomads": (
        "https://www.workingnomads.com/api/exposed_jobs/",
        lambda d: [
            {"title": j.get("title"), "remote": True,
             "location": j.get("location"), "candidate_location": j.get("location")}
            for j in d
        ],
    ),
    # limit is capped at 20 whatever you ask for, offset is deprecated, and the feed
    # is newest-first over ~105k jobs — so a single page is the last few minutes of
    # postings, not a sample. Page it by cursor or do not quote a number from it.
    "himalayas": (
        "https://himalayas.app/jobs/api",
        lambda d: [
            {"title": j.get("title"), "remote": True,
             "location": ", ".join(str(x) for x in (j.get("locationRestrictions") or []))
                         or "Worldwide",
             # Restrictions and timezones both, because either one can close the door.
             "candidate_location": ", ".join(
                 [str(x) for x in (j.get("locationRestrictions") or [])]
                 + [f"UTC{x:+g}" for x in (j.get("timezoneRestrictions") or [])]
             )}
            for j in d["jobs"]
        ],
    ),
    "jobicy": (
        "https://jobicy.com/api/v2/remote-jobs?count=50",
        lambda d: [
            {"title": j.get("jobTitle"), "remote": True,
             "location": j.get("jobGeo"), "candidate_location": j.get("jobGeo")}
            for j in d["jobs"]
        ],
    ),
}
# Feeds that page by cursor: how many pages to pull for a measurement worth quoting.
CURSOR_PAGES = {"himalayas": 15}


def _get(url: str):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


print("── cases ──")
failed = 0
for job, expect, pins in CASES:
    keep, why = geography_check(job, {})
    ok = keep == expect
    failed += not ok
    extra = job.get("candidate_location") or job.get("country_code") or ""
    print(f"{'ok  ' if ok else 'FAIL'} {'keep' if keep else 'drop'}  "
          f"{job.get('location') or '(none)':<45.45} {extra:<32.32} {why or pins}")
print(f"\n{len(CASES) - failed}/{len(CASES)} as expected")

# The one setting that changes rule 4's answer, shown side by side because the
# difference is the whole argument for leaving it off.
print("\n── drop_when_unknown ──")
for loc in ("Makati", "Worldwide", "Salcedo Village", "N/A", ""):
    lax = geography_check({"location": loc}, {})[0]
    strict = geography_check({"location": loc}, {"drop_when_unknown": True})[0]
    print(f"  {loc or '(empty)':<20} default {'keep' if lax else 'drop'}"
          f"   drop_when_unknown {'keep' if strict else 'drop'}")

print("\n── jobs already stored ──")
try:
    rows = sqlite3.connect("data/jobs.db").execute("select data from jobs").fetchall()
except sqlite3.Error as exc:
    print(f"  no readable database ({exc})")
else:
    dropped = []
    for (data,) in rows:
        job = json.loads(data)
        keep, why = geography_check(job, {})
        if not keep:
            dropped.append((job.get("location"), job.get("job_title"), why))
    print(f"  {len(rows)} stored, {len(dropped)} would now be dropped on geography")
    for loc, title, why in dropped:
        print(f"    {loc!r} — {title} — {why}")

if "--offline" in sys.argv:
    sys.exit(1 if failed else 0)

print("\n── live feeds ──")
for name, (url, shape) in FEEDS.items():
    try:
        if name in CURSOR_PAGES:
            jobs, cursor, guids = [], None, set()
            for _ in range(CURSOR_PAGES[name]):
                page = _get(url + (f"?cursor={cursor}" if cursor else ""))
                for raw, job in zip(page.get("jobs") or [], shape(page)):
                    if raw["guid"] not in guids:
                        guids.add(raw["guid"])
                        jobs.append(job)
                cursor = page.get("nextCursor")
                if not cursor:
                    break
                time.sleep(0.4)
        else:
            jobs = shape(_get(url))
    except Exception as exc:                                  # noqa: BLE001
        print(f"\n{name}: unreachable — {exc}")
        continue
    kept, why = [], collections.Counter()
    for job in jobs:
        keep, reason = geography_check(job, {})
        kept.append(job) if keep else why.__setitem__(reason, why[reason] + 1)
    share = 100 * len(kept) / len(jobs) if jobs else 0
    print(f"\n{name}: {len(kept)}/{len(jobs)} eligible ({share:.0f}%)")
    for reason, n in why.most_common(5):
        print(f"   dropped {n:>4}  {reason}")
    for job in kept[:3]:
        print(f"   kept          {job['location']!r} — {(job['title'] or '')[:48]}")

# The feeds are a measurement, not a verdict; only the cases are.
sys.exit(1 if failed else 0)
