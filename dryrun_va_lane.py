"""Offline checks for the VA lane (docs/va-lane.md). No network, no API key.

    .venv\\Scripts\\python.exe dryrun_va_lane.py

1. one_crawler lets only one process read onlinejobs.ph at a time, and waits
   rather than giving up.
2. config.va.example.yaml loads with the inbox off and no Gmail secrets set.
3. run_once with no inbox never touches one, and still reads the scrape sources.
4. The VA title gate keeps tech VA titles and drops the other kinds.

Exits non-zero when any case goes wrong.
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import yaml

from jobsift.sources import onlinejobs

failures: list[str] = []


def check(ok: bool, what: str) -> None:
    print(("ok   " if ok else "FAIL ") + what)
    if not ok:
        failures.append(what)


# 1. The lock. A file lock on Windows is per handle, so two threads opening the
# file separately contend exactly as two processes would.
with tempfile.TemporaryDirectory() as tmp:
    lock = Path(tmp) / "data" / "onlinejobs.lock"
    held = threading.Event()

    def holder():
        with onlinejobs.one_crawler(lock, poll=0.1):
            held.set()
            time.sleep(1.0)

    t = threading.Thread(target=holder)
    t.start()
    held.wait(5)
    start = time.monotonic()
    with onlinejobs.one_crawler(lock, poll=0.1):
        waited = time.monotonic() - start
    t.join()
    check(waited >= 0.8, f"a second reader waits for the first ({waited:.2f}s)")

    start = time.monotonic()
    with onlinejobs.one_crawler(lock, poll=0.1):
        pass
    check(time.monotonic() - start < 0.5, "the lock is free again once released")


# 2. The example config, with Gmail secrets absent.
from jobsift.config import load_config  # noqa: E402

with tempfile.TemporaryDirectory() as tmp:
    data = yaml.safe_load(Path("config.va.example.yaml").read_text(encoding="utf-8"))
    data["resume_path"] = str(Path(tmp) / "resume-va.txt")
    data["database_path"] = str(Path(tmp) / "va.db")
    Path(data["resume_path"]).write_text("profile", encoding="utf-8")
    cfg_path = Path(tmp) / "config.va.yaml"
    cfg_path.write_text(yaml.safe_dump(data), encoding="utf-8")
    env_path = Path(tmp) / ".env"
    env_path.write_text("DEEPSEEK_API_KEY=test\n", encoding="utf-8")
    saved = {k: os.environ.pop(k) for k in ("GMAIL_ADDRESS", "GMAIL_APP_PASSWORD", "DEEPSEEK_API_KEY")
             if k in os.environ}
    try:
        config = load_config(str(cfg_path), str(env_path))
        check(config.email_enabled is False, "config.va.example.yaml turns the inbox off")
        check(True, "it loads with no Gmail secrets")
        check("onlinejobs.ph" in config.skip_link_domains,
              "enrich never re-fetches an onlinejobs.ph page unpaced")
    except SystemExit as exc:
        check(False, f"config.va.example.yaml loads with no Gmail secrets ({exc})")
        config = None
    finally:
        os.environ.update(saved)


# 3. run_once with no inbox.
import jobsift.pipeline as pipeline  # noqa: E402

calls = {"collect": 0}


def fake_collect(config, store=None):
    calls["collect"] += 1
    return []


class FakeStore:
    def is_fresh_install(self):
        raise AssertionError("asked about the inbox backfill with the inbox off")

    def cleanup(self):
        pass


real_collect, real_finish = pipeline.collect_scraped, pipeline.finish_scraped
pipeline.collect_scraped = fake_collect
pipeline.finish_scraped = lambda config: None
try:
    handled = pipeline.run_once(SimpleNamespace(), None, FakeStore(), None, "profile")
    check(handled == 0 and calls["collect"] == 1,
          "run_once with no inbox skips it and still reads the scrape sources")
except Exception as exc:
    check(False, f"run_once with no inbox ({exc!r})")
finally:
    pipeline.collect_scraped, pipeline.finish_scraped = real_collect, real_finish


# 4. The title gate, on titles seen on the board on 2026-10-05.
source = yaml.safe_load(Path("config.va.example.yaml").read_text(encoding="utf-8"))
olj = source["scrape_sources"]["onlinejobs_ph"]
gate = lambda title: onlinejobs._matches({"title": title}, olj["include_keywords"],  # noqa: E731
                                         olj["exclude_keywords"])
for title in ["Virtual Assistant (GoHighLevel Expert) – Long-Term Role for Growing AI Agency",
              "Executive Virtual Assistant | GoHighLevel & Business Operations",
              "Operations Assistant & Researcher – Tourism Startup",
              "Zapier Automation Specialist",
              "Make.com and n8n Workflow Specialist",
              "Tech VA - CRM and Google Sheets"]:
    check(gate(title), f"keeps: {title}")
for title in ["Social Media Virtual Assistant",
              "Video Editor and VA",
              "Bookkeeping Admin Virtual Assistant",
              "Customer Service & Reception Virtual Assistant",
              "Earn easy money with data entry",
              "Full Stack Developer",
              "Graphic Designer",
              "Amazon VA for Private Label Management",
              "Appointment Setter",
              "E-commerce Virtual Assistant",
              "Sales Closer"]:
    check(not gate(title), f"drops: {title}")


print()
if failures:
    print(f"{len(failures)} failed")
    sys.exit(1)
print("all passed")
