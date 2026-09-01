"""Dump every stored job to a spreadsheet.

Telegram is a glanceable feed and deliberately shows a fraction of what was
gathered — it exists to interrupt you about a handful of jobs. This is the other
view: every job, every field, one row each, for reading in Excel or LibreOffice
when you want to see what the pipeline actually did rather than what it decided
was worth pinging you about.

CSV rather than .xlsx on purpose — it opens directly in Excel, and the project
would otherwise gain a dependency (openpyxl) for one report.

**Written with a UTF-8 BOM**, which looks like a wart and is not. Excel on
Windows reads a BOM-less CSV as the local ANSI codepage, which turns every "₱"
into "â‚±" and mangles every "ñ" in a Filipino company name. Nothing warns you;
the file just looks broken. LibreOffice, pandas and Google Sheets all read the
BOM correctly, so it costs nothing elsewhere.
"""

from __future__ import annotations

import csv
import json
import logging
import sqlite3

logger = logging.getLogger(__name__)

# Ordered for reading left to right: what the job is, what we thought of it, how
# sure we are, and finally the raw material the judgement was made from.
COLUMNS = [
    ("verdict", "what TODAY's config says about this row — kept, or why not"),
    ("id", "row id, for `--draft <id>`"),
    ("score", "total out of 100"),
    ("job_title", ""),
    ("company", ""),
    ("source", "which board, and how it reached us"),
    ("salary", "as the listing wrote it"),
    ("salary_php_monthly", "normalised, what the salary filter actually compared"),
    ("location", ""),
    ("remote", "derived: structured field, then location, then posting text"),
    ("work_arrangement", "what the board itself said, where it says anything"),
    ("job_type", ""),
    ("experience_level", ""),
    ("evidence", "full | snippet — whether the score saw a posting or a teaser"),
    ("evidence_chars", "how much posting text was available"),
    ("degree_required", ""),
    ("skill_match", "out of 30"),
    ("experience_fit", "out of 30"),
    ("interest_fit", "salary component, out of 40"),
    ("priority_bonus", "points added for a priority keyword"),
    ("priority_hits", "which keywords matched"),
    ("matching_skills", ""),
    ("missing_skills", ""),
    ("reasoning", "why the model scored it this way"),
    ("description_summary", ""),
    ("skills_required", ""),
    ("duration", ""),
    ("url", ""),
    ("age_days", "how old the listing is — sort on this to work freshest-first"),
    ("timestamp", "when the listing was posted / the email arrived"),
    ("reported_at", "when we scored it"),
    ("status", ""),
]


def rows(db_path: str, min_score: int = 0, source: str = "", limit: int = 0,
         settings: dict | None = None) -> list[dict]:
    """Every stored job as a flat dict, highest score first.

    `settings` re-runs today's hard filters over rows that were stored under an
    older config, and records the answer in `verdict`. This matters more than it
    sounds: filters run at INGEST, so tightening one leaves everything already
    collected untouched. After `max_age_days` was added, the database still held a
    199-day-old listing scoring 86/100 — real when it was stored, dead now.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    sql = "SELECT id, score, source, title, company, url, data FROM jobs"
    where, params = [], []
    if min_score:
        where.append("score >= ?")
        params.append(int(min_score))
    if source:
        where.append("source LIKE ?")
        params.append(f"%{source}%")
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY score DESC, id DESC"
    if limit:
        sql += f" LIMIT {int(limit)}"

    from .filters import check, currency_for, normalize_salary_php, posting_age_days

    out = []
    for row in conn.execute(sql, params):
        job = json.loads(row["data"]) if row["data"] else {}
        # Columns the row carries authoritatively, whatever the JSON blob says.
        job.setdefault("job_title", row["title"])
        job.setdefault("company", row["company"])
        job["id"] = row["id"]
        job["score"] = row["score"]
        job["source"] = job.get("source") or row["source"]
        job["url"] = job.get("url") or row["url"]

        # The figure the salary filter compared, recomputed rather than stored —
        # so a row shows what today's parser makes of it, which is the number that
        # matters when asking why something was or was not dropped.
        value = normalize_salary_php(
            job.get("salary") or "",
            job_type=job.get("job_type") or "",
            default_currency=currency_for(job),
        )
        job["salary_php_monthly"] = round(value) if value else ""

        # Age as its own column rather than folded into the score. Score answers
        # "does this fit me"; age answers "is it still open" — two different
        # questions, and mixing them would make a stale perfect match outrank a
        # fresh good one for reasons the number could not explain.
        age = posting_age_days({**job, "posted": job.get("posted") or job.get("timestamp")})
        job["age_days"] = round(age) if age is not None else ""

        if settings:
            # A stored row names its title `job_title` and its date `timestamp`;
            # the filters expect `title` and `posted`. Mapping them here rather
            # than loosening the filters keeps one shape for live jobs.
            candidate = {
                **job,
                "title": job.get("job_title") or job.get("title") or "",
                "posted": job.get("posted") or job.get("timestamp") or "",
            }
            keep, why = check(candidate, settings)
            job["verdict"] = "kept" if keep else why
        else:
            job["verdict"] = ""
        out.append(job)
    conn.close()
    return out


def to_csv(records: list[dict], out_path: str) -> int:
    """Write the rows. Returns how many were written.

    Raises SystemExit with an explanation when the file is locked. Excel holds an
    exclusive lock on Windows, so re-exporting over a sheet you left open fails —
    and the stale file left behind is worse than the error, because it looks like
    the pipeline produced old data rather than none.
    """
    names = [name for name, _ in COLUMNS]
    try:
        return _write(records, names, out_path)
    except PermissionError:
        raise SystemExit(
            f"Cannot write {out_path} — it is open in Excel, which locks it.\n"
            f"Close the sheet and re-run, or export to another name: "
            f"--export jobs-2.csv"
        )


def _write(records: list[dict], names: list[str], out_path: str) -> int:
    # utf-8-sig writes the BOM Excel needs; see the module docstring.
    with open(out_path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(names)
        for record in records:
            writer.writerow([
                # Newlines inside a quoted CSV field are legal and Excel handles
                # them, but they turn one job into an unreadable tall row. The
                # reasoning and description fields are the ones that carry them.
                " ".join(str(record.get(name, "") or "").split())
                for name in names
            ])
    return len(records)


def summarise(records: list[dict]) -> str:
    """A few lines about what is in the file, for the terminal."""
    import collections

    if not records:
        return "no jobs stored yet"

    scores = [r.get("score") or 0 for r in records]
    by_source = collections.Counter(r.get("source") or "?" for r in records)
    evidence = collections.Counter(r.get("evidence") or "unknown" for r in records)
    priced = [r for r in records if r.get("salary_php_monthly")]

    lines = [
        f"{len(records)} job(s); score max {max(scores)}, median "
        f"{sorted(scores)[len(scores) // 2]}, min {min(scores)}",
        "  by source:   " + ", ".join(f"{k} {v}" for k, v in by_source.most_common()),
        "  by evidence: " + ", ".join(f"{k} {v}" for k, v in evidence.most_common()),
        f"  {len(priced)}/{len(records)} state a salary",
    ]
    top = records[0]
    lines.append(f"  highest: {top.get('score')}/100  {top.get('job_title')} @ {top.get('company')}")
    return "\n".join(lines)
