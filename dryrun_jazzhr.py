"""Dry run for the JazzHR board reader (jobsift/sources/jazzhr.py).

Offline by default: parses trimmed copies of the real Bamboo Works pages as they
were on 6 Oct 2026 and checks what the pipeline would receive. Exits 1 on any
failed check.

    python dryrun_jazzhr.py           # offline checks
    python dryrun_jazzhr.py --live    # also read the live board, print what it returns
"""

from __future__ import annotations

import sys

from jobsift.sources.jazzhr import parse_board, parse_detail
from jobsift.utils import job_key

# The real list markup, trimmed to four roles: one role posted twice under two
# ids, one that the title gate should drop, and one distinct role.
LIST_PAGE = """
<html><head>
<meta property="og:title" content="Bamboo Works - Career Page" />
<script type="application/ld+json">
{"@type": "Organization", "name": "Bamboo Works", "url": "https:\\/\\/bambooworks.io\\/"}
</script></head><body><ul class="list-group">
<li class="list-group-item">
  <h3 class='list-group-item-heading'>
    <a href="https://bambooworks.applytojob.com/apply/0DuRaq8wz5/AI-Automation-Specialist-Claude-Accounting-Workflows">
      AI Automation Specialist (Claude &amp; Accounting Workflows)          </a>
  </h3>
  <ul class='list-inline list-group-item-text'><li><i class='fa fa-map-marker'></i>Remote</li></ul>
</li>
<li class="list-group-item">
  <h3 class='list-group-item-heading'>
    <a href="https://bambooworks.applytojob.com/apply/sPFTNkxlF5/AI-Automation-Specialist-Claude-Accounting-Workflows">
      AI Automation Specialist (Claude &amp; Accounting Workflows)          </a>
  </h3>
  <ul class='list-inline list-group-item-text'><li><i class='fa fa-map-marker'></i>Remote</li></ul>
</li>
<li class="list-group-item">
  <h3 class='list-group-item-heading'>
    <a href="https://bambooworks.applytojob.com/apply/0HBH3JVcur/Bookkeeper-CPA-US-Or-AU-Accounting">
      Bookkeeper (CPA, US or AU Accounting)          </a>
  </h3>
  <ul class='list-inline list-group-item-text'><li><i class='fa fa-map-marker'></i>Remote</li></ul>
</li>
<li class="list-group-item">
  <h3 class='list-group-item-heading'>
    <a href="http://bambooworks.applytojob.com/apply/G5BDCChUrd/AI-Automation-Engineer-Claude-Code-AI-Agents">
      AI Automation Engineer (Claude Code &amp; AI Agents)          </a>
  </h3>
  <ul class='list-inline list-group-item-text'><li><i class='fa fa-map-marker'></i>Remote</li></ul>
</li>
</ul></body></html>
"""

DETAIL_PAGE = """
<html><body>
<div class='job-header'><div class='container'>
  <h2>AI Automation Engineer (Claude Code &amp; AI Agents)</h2>
  <div class="job-attributes-container">
    <div title="Location"><i class='fa fa-map-marker'></i>Remote</div>
    <div id='resumator-job-employment' title="Type"><i class='fa fa-clock-o'></i>Part Time                </div>
  </div>
</div></div>
<div class='col col-xs-7 description' id="job-description">
  <h3><strong>About the Client</strong></h3><p>We're helping a fast-growing marketing and operations company.</p>
  <p><strong>Commitment:</strong> Part-Time (20 hours/week)</p>
  <p><strong>Monthly Rate:</strong> USD $900/month (can be higher, depending on level of experience)</p>
  <ul><li>Strong experience with Claude Code and AI-assisted development</li></ul>
</div>
</body></html>
"""

failures = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global failures
    print(f"  {'ok  ' if ok else 'FAIL'} {name}{'  ' + detail if detail and not ok else ''}")
    failures += 0 if ok else 1


print("Board page")
settings = {"include_keywords": ["ai", "automation"], "exclude_keywords": []}
jobs = parse_board(LIST_PAGE, "bambooworks", settings)
titles = [j["title"] for j in jobs]
check("two roles kept", len(jobs) == 2, str(titles))
check("the repost under a second id collapsed", titles.count(
    "AI Automation Specialist (Claude & Accounting Workflows)") == 1, str(titles))
check("the title gate drops the bookkeeper", not any("Bookkeeper" in t for t in titles), str(titles))
check("company read from the page", all(j["company"] == "Bamboo Works" for j in jobs),
      str({j["company"] for j in jobs}))
check("location and remote read", all(j["location"] == "Remote" and j["remote"] for j in jobs))
check("urls are https", all(j["url"].startswith("https://") for j in jobs), str([j["url"] for j in jobs]))
check("source names the board", all(j["source"] == "jazzhr:bambooworks" for j in jobs))
# Bamboo Works roles also arrive through Working Nomads. The key must match the one
# that source produced, or every role would alert twice.
check("dedup key matches the Working Nomads copy",
      job_key(jobs[1]) == "ai automation engineer claude code ai agents::bamboo works",
      job_key(jobs[1]))
check("no gate keeps every role", len(parse_board(LIST_PAGE, "bambooworks", {})) == 3)

print("Detail page")
detail = parse_detail(DETAIL_PAGE)
check("description read", "marketing and operations company" in detail["description"],
      detail["description"][:80])
check("salary found", "900" in detail["salary"], repr(detail["salary"]))
check("employment type read", detail["job_type"] == "Part Time", repr(detail["job_type"]))
check("marked as the full posting", detail.get("full_posting") is True)
check("an empty page is not a full posting", "full_posting" not in parse_detail("<html></html>"))

print("Company fallbacks")
og_only = '<meta property="og:title" content="Acme Corp - Career Page" />' + LIST_PAGE.split("</head>", 1)[1]
check("og:title when there is no JSON-LD",
      parse_board(og_only, "acme", {})[0]["company"] == "Acme Corp")
check("the board name when there is neither",
      parse_board("<ul>" + LIST_PAGE.split("<ul class=\"list-group\">", 1)[1], "acme", {})[0]["company"] == "acme")

if "--live" in sys.argv:
    from jobsift.sources.jazzhr import fetch_jobs

    print("Live board")
    live = fetch_jobs({"boards": ["bambooworks"], "include_keywords": ["ai", "automation", "claude"]})
    for job in live:
        print(f"  {job['title']}  |  {job['job_type'] or '-'}  |  {job['salary'] or '-'}  |  "
              f"{len(job['description'])} chars")
    check("live board returned roles", bool(live))
    check("every live role has its posting", all(j.get("full_posting") for j in live))

print(f"\n{'All checks passed.' if not failures else f'{failures} check(s) FAILED.'}")
sys.exit(1 if failures else 0)
