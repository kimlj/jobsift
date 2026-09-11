"""SQLite storage: track processed emails, dedup jobs, log jobs.

Dedup is on a NORMALISED title::company (see utils.job_key), so one employer
writing itself "WeSupport, Inc." on one board and "WeSupport Incorporated" on
another is still one employer. `_migrate` carries keys written in an older
format forward, because changing that format is otherwise a silent un-dedup.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time

from .utils import job_key


class Store:
    def __init__(self, path: str):
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self._init()

    def _init(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS processed_emails (
                uid TEXT PRIMARY KEY,
                processed_at INTEGER
            );
            CREATE TABLE IF NOT EXISTS source_runs (
                source TEXT PRIMARY KEY,
                last_run INTEGER
            );
            CREATE TABLE IF NOT EXISTS seen_jobs (
                key TEXT PRIMARY KEY,
                first_seen INTEGER
            );
            CREATE TABLE IF NOT EXISTS applied_jobs (
                url TEXT PRIMARY KEY,
                title TEXT,
                company TEXT,
                board TEXT,
                receipt INTEGER,
                evidence TEXT,
                detected_at INTEGER
            );
            CREATE TABLE IF NOT EXISTS sheet_stages (
                url TEXT PRIMARY KEY,
                stage TEXT,
                tab TEXT,
                first_seen INTEGER,
                changed_at INTEGER
            );
            CREATE TABLE IF NOT EXISTS stage_changes (
                url TEXT,
                stage TEXT,
                tab TEXT,
                changed_at INTEGER
            );
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT,
                source TEXT,
                title TEXT,
                company TEXT,
                score INTEGER,
                url TEXT,
                data TEXT,
                created_at INTEGER
            );
            """
        )
        self.conn.commit()
        self._migrate()

    # Bumped when the dedup key format changes. 1 = normalised title::company,
    # where "WeSupport, Inc." and "WeSupport Incorporated" are one employer.
    SCHEMA_VERSION = 1

    def _migrate(self) -> None:
        """Bring dedup keys written by an older version up to the current format.

        Without this, changing the key format silently un-dedups history: a job
        saved last week reappears in tomorrow's alert email under a key nothing
        recognises, and is saved and alerted a second time.

        Only jobs we actually SAVED can be migrated — `seen_jobs` also holds keys
        for jobs that were filtered out, and those rows carry no title or company
        to recompute from. They are left alone: the cost is that such a job may be
        re-examined once, which is free because the hard filters run before enrich
        and scoring, and `cleanup()` expires them after 30 days regardless.
        """
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        if version >= self.SCHEMA_VERSION:
            return

        rows = self.conn.execute(
            "SELECT id, key, title, company, url, created_at FROM jobs"
        ).fetchall()
        for job_id, old, title, company, url, created_at in rows:
            new = job_key({"title": title, "company": company, "url": url})
            if new == old:
                continue
            # first_seen carries the original timestamp so cleanup's 30-day window
            # measures the job's real age rather than the moment we migrated.
            self.conn.execute(
                "INSERT OR IGNORE INTO seen_jobs (key, first_seen) VALUES (?, ?)",
                (new, int(created_at or time.time())),
            )
            self.conn.execute("UPDATE jobs SET key = ? WHERE id = ?", (new, job_id))

        self.conn.execute(f"PRAGMA user_version = {self.SCHEMA_VERSION}")
        self.conn.commit()

    # -- processed emails --
    def is_fresh_install(self) -> bool:
        """True when no email has ever been processed (first run on a new install)."""
        cur = self.conn.execute("SELECT 1 FROM processed_emails LIMIT 1")
        return cur.fetchone() is None

    def is_email_processed(self, uid: str) -> bool:
        cur = self.conn.execute("SELECT 1 FROM processed_emails WHERE uid = ?", (uid,))
        return cur.fetchone() is not None

    def mark_email_processed(self, uid: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO processed_emails (uid, processed_at) VALUES (?, ?)",
            (uid, int(time.time())),
        )
        self.conn.commit()

    # -- dedup jobs --
    def is_job_seen(self, key: str) -> bool:
        cur = self.conn.execute("SELECT 1 FROM seen_jobs WHERE key = ?", (key,))
        return cur.fetchone() is not None

    def mark_job_seen(self, key: str) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO seen_jobs (key, first_seen) VALUES (?, ?)",
            (key, int(time.time())),
        )
        self.conn.commit()

    # -- non-email sources: when did each last run --
    def seconds_since_scrape(self, source: str) -> float:
        """How long since this source was last read. A huge number if never."""
        row = self.conn.execute(
            "SELECT last_run FROM source_runs WHERE source = ?", (source,)
        ).fetchone()
        return float("inf") if not row else time.time() - float(row[0] or 0)

    def mark_scraped(self, source: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO source_runs (source, last_run) VALUES (?, ?)",
            (source, int(time.time())),
        )
        self.conn.commit()

    # -- job log --
    def save_job(self, key: str, record: dict) -> None:
        self.conn.execute(
            "INSERT INTO jobs (key, source, title, company, score, url, data, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                key,
                record.get("source", ""),
                record.get("job_title", ""),
                record.get("company", ""),
                int(record.get("score", 0) or 0),
                record.get("url", ""),
                json.dumps(record),
                int(time.time()),
            ),
        )
        self.conn.commit()

    # -- applications the boards confirmed --
    def mark_applied(self, url: str, confirmation) -> bool:
        """Record one confirmed application. True if this is the first time.

        The subject line is kept as `evidence` because this is the one column
        the program fills in on the user's behalf, and "which email said so" is
        the only way to argue with it later.
        """
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO applied_jobs "
            "(url, title, company, board, receipt, evidence, detected_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (url, confirmation.title, confirmation.company, confirmation.board,
             1 if confirmation.receipt else 0, confirmation.subject, int(time.time())),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def applied_urls(self) -> set[str]:
        """Every url a board has confirmed. Survives the sheet being off."""
        return {row[0] for row in self.conn.execute("SELECT url FROM applied_jobs")}

    # -- the stage column, mirrored from the sheet --
    def record_stages(self, stages: dict[str, tuple[str, str]]) -> int:
        """Mirror the sheet's stage column. Returns how many rows changed stage.

        The sheet stays where stages are set: they are the user's, and nothing
        here writes one back. This copy exists because the database is what the
        rest of the program reads on its own, and it once held the 6 applications
        a board had confirmed while the sheet said 24.

        Kept apart from applied_jobs on purpose. A receipt is a board's evidence
        and a stage is the user's word, and "which one said so" is the only way
        to argue with either later.

        Only changes are logged to stage_changes, so the first Applied row for a
        url is when the application was first seen - for rows already in the
        sheet before the first sync, that is the sync, not the day it was sent.
        A tab move with the stage unchanged (a delisted row filed into Closed)
        updates the tab and logs nothing. A url that disappears from the sheet
        keeps its last stage: deleting a row is not withdrawing an application.
        """
        now = int(time.time())
        changed = 0
        for url, (stage, tab) in stages.items():
            row = self.conn.execute(
                "SELECT stage, tab FROM sheet_stages WHERE url = ?", (url,)).fetchone()
            if row is None:
                self.conn.execute(
                    "INSERT INTO sheet_stages (url, stage, tab, first_seen, changed_at) "
                    "VALUES (?, ?, ?, ?, ?)", (url, stage, tab, now, now))
            elif row[0] != stage:
                self.conn.execute(
                    "UPDATE sheet_stages SET stage = ?, tab = ?, changed_at = ? WHERE url = ?",
                    (stage, tab, now, url))
            else:
                if row[1] != tab:
                    self.conn.execute("UPDATE sheet_stages SET tab = ? WHERE url = ?", (tab, url))
                continue
            self.conn.execute(
                "INSERT INTO stage_changes (url, stage, tab, changed_at) VALUES (?, ?, ?, ?)",
                (url, stage, tab, now))
            changed += 1
        self.conn.commit()
        return changed

    def sent_urls(self) -> set[str]:
        """Every url applied to, by either word: a board's receipt or the sheet's stage.

        This is what "already applied" means anywhere the database is read on its
        own. applied_urls stays receipts only, because --scan-applied feeds it to
        mark_applied, and a stage the user set is not a receipt to advance on.
        """
        from .sheets import STAGE_SENT

        marks = ",".join("?" * len(STAGE_SENT))
        staged = {row[0] for row in self.conn.execute(
            f"SELECT url FROM sheet_stages WHERE stage IN ({marks})", tuple(STAGE_SENT))}
        return self.applied_urls() | staged

    # -- postings that have left their board --
    def mark_delisted(self, url: str, when: str) -> int:
        """Record that a posting is no longer in its board's search results.

        Writes `status` on the stored record rather than deleting the row: the
        job was really scored, it may already have been applied to, and a row
        that vanishes from the sheet is indistinguishable from one that was
        never there. Returns how many stored rows were updated, which is more
        than one when the same posting reached us from two sources.

        Only ever called with a definite answer. A check that could not tell
        leaves the row alone — see `sources.onlinejobs.still_listed`, which
        returns None for that case precisely so it cannot be mistaken for gone.
        """
        updated = 0
        for row_id, blob in self.conn.execute(
            "SELECT id, data FROM jobs WHERE url = ?", (url,)
        ).fetchall():
            try:
                record = json.loads(blob)
            except Exception:
                continue
            if record.get("status") == "delisted":
                continue
            record["status"] = "delisted"
            record["delisted_on"] = when
            self.conn.execute(
                "UPDATE jobs SET data = ? WHERE id = ?", (json.dumps(record), row_id)
            )
            updated += 1
        if updated:
            self.conn.commit()
        return updated

    def delisted_urls(self) -> set[str]:
        """Every url already known to have left its board. Skipped on re-checks."""
        found = set()
        for (blob,) in self.conn.execute("SELECT data FROM jobs"):
            try:
                record = json.loads(blob)
            except Exception:
                continue
            if record.get("status") == "delisted" and record.get("url"):
                found.add(record["url"])
        return found

    # -- the employer check --
    def mark_vetted(self, job: str, verdict: str, why: str, vetted_as: str,
                    when: str) -> list[dict]:
        """Record the employer check's verdict on one job. Returns the updated records.

        `job` is a row id or a url. An id is widened to its url, because the
        same posting can reach us from two sources and the verdict is about the
        employer, which both rows share - vetting one and not the other would
        leave an "avoid" invisible on whichever row got looked at next.

        Written onto the stored record, the way mark_delisted writes `status`,
        so the export and the sheet read it from the same place as every other
        field. A second call replaces the first: new facts are the only reason
        anyone vets a job twice. `vetted_as` is kept apart from `employer_name`
        on purpose - that one is only ever what the posting itself stated, and
        this is what research concluded, which can be a probable rather than a
        certain answer.
        """
        if str(job).isdigit():
            found = self.conn.execute("SELECT url FROM jobs WHERE id = ?", (int(job),)).fetchone()
            if not found:
                return []
            # Widened only when there is a url to widen to. An empty one would
            # match every link-less row in the table and vet them all at once.
            if found[0]:
                rows = self.conn.execute(
                    "SELECT id, data FROM jobs WHERE url = ?", (found[0],)).fetchall()
            else:
                rows = self.conn.execute(
                    "SELECT id, data FROM jobs WHERE id = ?", (int(job),)).fetchall()
        else:
            rows = self.conn.execute("SELECT id, data FROM jobs WHERE url = ?", (job,)).fetchall()
        updated = []
        for row_id, blob in rows:
            try:
                record = json.loads(blob)
            except Exception:
                continue
            record["vetting"] = verdict
            record["vetting_why"] = why
            record["vetted_as"] = vetted_as
            record["vetted_on"] = when
            self.conn.execute(
                "UPDATE jobs SET data = ? WHERE id = ?", (json.dumps(record), row_id)
            )
            updated.append(record)
        if updated:
            self.conn.commit()
        return updated

    def cleanup(self, days: int = 30) -> None:
        """Forget dedup keys and processed-email uids older than `days`."""
        cutoff = int(time.time()) - days * 86400
        self.conn.execute("DELETE FROM seen_jobs WHERE first_seen < ?", (cutoff,))
        self.conn.execute("DELETE FROM processed_emails WHERE processed_at < ?", (cutoff,))
        self.conn.commit()
