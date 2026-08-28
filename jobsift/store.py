"""SQLite storage: track processed emails, dedup jobs (title::company), log jobs."""

from __future__ import annotations

import json
import os
import sqlite3
import time


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
            CREATE TABLE IF NOT EXISTS seen_jobs (
                key TEXT PRIMARY KEY,
                first_seen INTEGER
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

    # -- processed emails --
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

    def cleanup(self, days: int = 30) -> None:
        """Forget dedup keys and processed-email uids older than `days`."""
        cutoff = int(time.time()) - days * 86400
        self.conn.execute("DELETE FROM seen_jobs WHERE first_seen < ?", (cutoff,))
        self.conn.execute("DELETE FROM processed_emails WHERE processed_at < ?", (cutoff,))
        self.conn.commit()
