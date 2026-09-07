"""Offline check: onlinejobs.ph must not re-read the detail page of a listing
we already have.

No network. `httpx.Client` is replaced with a stub serving one canned listing
page and counting which detail URLs are asked for, so the assertion is about
requests made rather than about a log line.

Why this exists: measured on the first pass after the source came back, 106 of
112 detail pages were for listings already in seen_jobs, each costing a 5s
Crawl-delay. The pipeline discards those the line after they arrive. The hook
that prevents it (`is_seen`) already existed and was wired to jobstreet only.

    .venv\\Scripts\\python.exe dryrun_seen_skip.py
"""

from __future__ import annotations

import sys

import jobsift.sources.onlinejobs as oj

CARD = """
<div class="jobpost-cat-box">
  <h4><span class="badge">Full Time</span>{title}</h4>
  <p data-temp="2026-09-06 11:00:00">posted</p>
  <div class="desc">{desc} <a href="/jobseekers/job/{slug}">See More</a></div>
  <dl><dd>$1,200/month</dd></dl>
  <div class="job-tag"><a>Python</a></div>
</div>
"""

JOBS = [
    ("Python Developer", "python-developer-111", "Builds things in Python."),
    ("Automation Engineer", "automation-engineer-222", "Wires up workflows."),
    ("AI Developer", "ai-developer-333", "Ships LLM features."),
]

LISTING = "<html><body>" + "".join(
    CARD.format(title=t, slug=s, desc=d) for t, s, d in JOBS
) + "</body></html>"

DETAIL = '<html><body><div id="job-description">%s</div></body></html>' % ("x" * 4000)


class Resp:
    def __init__(self, text):
        self.status_code = 200
        self.text = text


class StubClient:
    """Serves the listing on any search URL, a long detail body otherwise."""

    detail_urls: list[str] = []

    def __init__(self, *a, **kw):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url):
        if "/jobseekers/job/" in url:
            StubClient.detail_urls.append(url)
            return Resp(DETAIL)
        return Resp(LISTING)


def run(is_seen, label):
    StubClient.detail_urls = []
    settings = {
        "max_pages": 1,
        "delay_seconds": 5,
        "fetch_details": True,
        "search_keywords": ["python"],
        "include_keywords": [],
        "exclude_keywords": [],
    }
    jobs = oj.fetch_jobs(settings, is_seen=is_seen)
    print("  %-28s -> %d job(s) returned, %d detail page(s) fetched"
          % (label, len(jobs), len(StubClient.detail_urls)))
    return jobs, list(StubClient.detail_urls)


def main() -> int:
    oj.httpx.Client = StubClient          # no network
    oj.time.sleep = lambda *_: None       # no Crawl-delay wait in a test

    failures = []

    print("onlinejobs.ph detail-fetch skipping\n")

    jobs, fetched = run(None, "no is_seen (old behaviour)")
    if len(fetched) != 3:
        failures.append("without is_seen, expected 3 detail fetches, got %d" % len(fetched))

    seen_slugs = {"python-developer-111", "automation-engineer-222"}

    def is_seen(job):
        return any(s in (job.get("url") or "") for s in seen_slugs)

    jobs, fetched = run(is_seen, "two of three already seen")
    if len(fetched) != 1:
        failures.append("expected 1 detail fetch, got %d" % len(fetched))
    if fetched and "ai-developer-333" not in fetched[0]:
        failures.append("fetched the wrong job: %s" % fetched[0])

    # The contract in sources/__init__.py: is_seen is an efficiency signal and
    # must not filter. All three jobs still come back for the pipeline to judge.
    if len(jobs) != 3:
        failures.append("is_seen must not drop jobs; expected 3 returned, got %d" % len(jobs))

    jobs, fetched = run(lambda job: True, "all already seen")
    if len(fetched) != 0:
        failures.append("expected 0 detail fetches, got %d" % len(fetched))
    if len(jobs) != 3:
        failures.append("expected 3 returned even when all seen, got %d" % len(jobs))

    print()
    if failures:
        for f in failures:
            print("FAIL: %s" % f)
        return 1
    print("PASS: skips known listings, keeps fetching new ones, filters nothing.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
