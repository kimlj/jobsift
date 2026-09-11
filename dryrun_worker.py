"""Dry run of the residential worker - offline: no SSH, no network, no model.

The worker splits jobsift across two machines, and every way that split can go
wrong is a way to lose a job, pay for it twice, or give a laptop key more reach
on a server than it should have. Each is built here, with the core's side called
in-process (LocalTransport) and onlinejobs.ph replaced by a stub that counts
which detail pages were asked for.

    .venv\\Scripts\\python.exe dryrun_worker.py
"""

from __future__ import annotations

import base64
import gzip
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

import jobsift.sources.onlinejobs as oj
from jobsift import career, worker
from jobsift.store import Store
from jobsift.utils import job_key

FAILED = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global FAILED
    FAILED += 0 if ok else 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  ({detail})" if detail and not ok else ""))


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
LISTING = "<html><body>" + "".join(CARD.format(title=t, slug=s, desc=d) for t, s, d in JOBS) + "</body></html>"
DETAIL = '<html><body><div id="job-description">%s</div></body></html>' % ("x" * 4000)


class Resp:
    def __init__(self, text):
        self.status_code = 200
        self.text = text


class StubClient:
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


class Flaky:
    """The core's side, able to fail the way a sleeping VPS or a dropped link does."""

    def __init__(self, inner, fail=()):
        self.inner, self.fail = inner, set(fail)

    def call(self, request, data=b""):
        if request.split()[0] in self.fail:
            raise worker.TransportError("connection refused")
        return self.inner.call(request, data)


SETTINGS = {"max_pages": 1, "delay_seconds": 5, "fetch_details": True,
            "search_keywords": ["python"], "include_keywords": [], "exclude_keywords": []}


def core_paths(root: Path) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    Store(str(root / "jobs.db"))
    return {"database": str(root / "jobs.db"), "inbox": str(root / "inbox"),
            "brief": str(root / "brief.md"), "career": str(root / "career.yaml"),
            "resume": str(root / "resume.txt"), "profile": str(root / "profile.yaml")}


def detail_pages_for(transport, outbox):
    StubClient.detail_urls = []
    summary = worker.run_pass(SETTINGS, transport, outbox)
    return summary, list(StubClient.detail_urls)


oj.httpx.Client = StubClient          # no network
oj.time.sleep = lambda *_: None       # no Crawl-delay wait in a test

with tempfile.TemporaryDirectory() as tmp_name:
    tmp = Path(tmp_name)
    paths = core_paths(tmp / "core")
    first_url = "https://www.onlinejobs.ph/jobseekers/job/python-developer-111"
    Store(paths["database"]).mark_job_seen(
        job_key({"title": "Python Developer", "company": "", "url": first_url}))
    local = worker.LocalTransport(paths)
    outbox = tmp / "laptop" / "outbox"
    inbox = Path(paths["inbox"])

    print("── a pass ──")
    summary, fetched = detail_pages_for(local, outbox)
    check("detail pages are read only for listings the core does not have",
          len(fetched) == 2 and first_url not in fetched, str(fetched))
    check("only the new listings are delivered",
          summary["delivered"] == 2 and summary["known"] == 1, str(summary))
    batches = list(inbox.glob("*.jsonl"))
    check("they land in the core's inbox as one batch", len(batches) == 1, str(batches))
    _, delivered = worker.parse_batch(batches[0].read_text(encoding="utf-8"))
    check("each carries its full posting, so the core never fetches it again",
          all(j.get("full_posting") and len(j["description"]) >= 4000 for j in delivered))
    check("the outbox is empty once the core has them", not list(outbox.glob("*.jsonl")))

    print("── the next pass, before the core has run ──")
    summary, fetched = detail_pages_for(local, outbox)
    check("jobs waiting in the inbox count as known: no page read twice",
          not fetched and summary["delivered"] == 0, str(summary))

    print("── the core reads its inbox ──")
    taken = worker.take_inbox(paths["inbox"])
    check("the core gets the delivered jobs", len(taken) == 2, str(len(taken)))
    again = worker.take_inbox(paths["inbox"])
    check("a pass that died before finishing reads them again instead of losing them",
          len(again) == 2)
    worker.finish_inbox(paths["inbox"])
    check("a finished pass files them as done",
          not list((inbox / "processing").glob("*.jsonl"))
          and len(list((inbox / "done").glob("*.jsonl"))) == 1)
    leftover = inbox / "processing" / "olj-left-over.jsonl"
    leftover.write_text(worker.make_batch([]), encoding="utf-8")
    worker.finish_inbox(paths["inbox"])
    check("finishing files only what this pass read", leftover.exists())
    leftover.unlink()

    print("── when the core cannot be reached ──")
    paths2 = core_paths(tmp / "core2")
    outbox2 = tmp / "laptop2" / "outbox"
    local2 = worker.LocalTransport(paths2)
    summary, fetched = detail_pages_for(Flaky(local2, fail={"seen"}), outbox2)
    check("if it cannot be asked, no detail page is read and nothing is queued",
          not fetched and summary["error"] and not list(outbox2.glob("*.jsonl")), str(summary))
    summary, fetched = detail_pages_for(Flaky(local2, fail={"deliver"}), outbox2)
    check("if delivery fails, the batch waits in the outbox",
          summary["queued"] == 3 and len(list(outbox2.glob("*.jsonl"))) == 1, str(summary))
    summary, fetched = detail_pages_for(local2, outbox2)
    check("and goes first on the next pass that can reach the core",
          summary["delivered"] == 3 and not list(outbox2.glob("*.jsonl"))
          and len(list(Path(paths2["inbox"]).glob("*.jsonl"))) == 1 and not fetched, str(summary))

    print("── what the core will and will not do ──")
    batch = worker.make_batch([{"title": "X", "company": "", "url": "https://e.com/job/x-1"}]).encode()
    local2.call("deliver olj-test", batch)
    check("a batch delivered twice is kept once", "already" in local2.call("deliver olj-test", batch))

    def refused(request, data=b""):
        return worker.serve(request, data, paths2)[0] != 0

    check("a shell command is refused", refused("rm -rf /"))
    check("a path in a batch name is refused", refused("deliver ../../etc/cron.d/x", batch))
    check("a file outside the four is refused", refused("put config", b"x: 1"))
    check("a batch that is not a batch is refused, and nothing is written",
          refused("deliver olj-junk", b"not json")
          and not (Path(paths2["inbox"]) / "olj-junk.jsonl").exists())
    Path(paths2["career"]).write_text("rules: []\n", encoding="utf-8")
    check("a broken career.yaml is refused and the old one kept",
          refused("put career", b"rules: [\n")
          and Path(paths2["career"]).read_text(encoding="utf-8") == "rules: []\n")
    check("a brief that is not a brief is refused", refused("put brief", b"hello"))

    print("── pushing the files the core drafts from ──")
    laptop = tmp / "laptop3"
    laptop.mkdir()
    files = {"resume": laptop / "resume.txt", "career": laptop / "career.yaml",
             "brief": laptop / "brief.md", "profile": laptop / "profile.yaml"}
    files["resume"].write_text("Kim\n", encoding="utf-8")
    files["career"].write_text("projects: []\n", encoding="utf-8")
    files["brief"].write_text("# Career evidence brief\n\ncounted\n", encoding="utf-8")
    files["profile"].write_text("name: Kim\n", encoding="utf-8")
    as_str = {k: str(v) for k, v in files.items()}
    state = laptop / "pushed.json"
    first = worker.push_files(local2, as_str, state)
    check("the first push sends all four", sorted(first) == ["brief", "career", "profile", "resume"],
          str(first))
    check("an unchanged file is not sent again", worker.push_files(local2, as_str, state) == [])
    files["resume"].write_text("Kim, updated\n", encoding="utf-8")
    check("a changed one is",
          worker.push_files(local2, as_str, state) == ["resume"]
          and Path(paths2["resume"]).read_text(encoding="utf-8") == "Kim, updated\n")

    print("── a core that drafts from a pushed brief ──")
    done = career.refresh({"enabled": True, "out": str(tmp / "core2" / "evidence.yaml")},
                          paths2["career"], paths2["brief"])
    check("with no repos of its own it never rebuilds over the pushed brief",
          done["action"] == "none"
          and "counted" in Path(paths2["brief"]).read_text(encoding="utf-8"), str(done))

    print("── the laptop keeps a copy of the core's database ──")
    core_db = Store(paths2["database"])
    core_db.mark_job_seen("backup::probe")
    core_db.save_job("backup::probe", {"job_title": "Probe", "company": "Acme",
                                       "url": "https://e.com/job/probe"})
    core_db.conn.close()
    backups = tmp / "laptop4" / "backups"
    note = worker.take_backup(local2, backups)
    copies = sorted(backups.glob("jobs-*.db"))
    check("the first pass keeps a copy", len(copies) == 1 and "1 jobs" in note, note)
    if copies:
        restored = sqlite3.connect(copies[0].resolve().as_uri() + "?mode=ro", uri=True)
        found = restored.execute("SELECT COUNT(*) FROM seen_jobs WHERE key = 'backup::probe'").fetchone()[0]
        restored.close()
        check("it restores: the core's rows are in it", found == 1)
    check("a second pass the same day takes none", worker.take_backup(local2, backups) == "")
    for day in range(1, 5):
        worker.take_backup(local2, backups, keep=3, now=time.time() + day * 86400)
    check("only the newest three are kept", len(list(backups.glob("jobs-*.db"))) == 3)

    class Answers:
        """A core whose copy arrives wrong."""

        def __init__(self, text):
            self.text = text

        def call(self, request, data=b""):
            return self.text

    held = sorted(backups.glob("jobs-*.db"))
    for label, text in [
        ("a copy cut off in transit", local2.call("backup")[:400]),
        ("a copy that is not SQLite", base64.b64encode(gzip.compress(b"hello")).decode()),
        ("an answer that is not a copy at all", "refused: something\n"),
    ]:
        try:
            worker.take_backup(Answers(text), backups, now=time.time() + 30 * 86400)
            check(f"{label} is refused", False)
        except ValueError:
            check(f"{label} is refused, and nothing is filed",
                  sorted(backups.glob("jobs-*.db")) == held
                  and not list(backups.glob(".*.part")))
    check("backup takes no argument", refused("backup ../../etc/passwd"))

print()
print("all checks passed" if not FAILED else f"{FAILED} check(s) FAILED")
sys.exit(1 if FAILED else 0)
