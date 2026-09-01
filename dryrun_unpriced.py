"""Every job dropped for stating no salary — with its link and any money in its text.

The point is to be checkable. `salary_from_text` is deliberately strict and returns
nothing when unsure, which means it can be wrong in two directions, and only one of
them is visible from the outside:

  * it rejected something that WAS the pay          -> a real job lost, silently
  * the posting genuinely never states pay          -> nothing to find

This prints both so the difference can be judged by a human rather than asserted.
For each dropped job it shows the link, and every money-shaped string in the posting
with the words around it, marked with why it was not accepted.

    .venv\\Scripts\\python.exe dryrun_unpriced.py [--source jobicy] [--limit 25]
    .venv\\Scripts\\python.exe dryrun_unpriced.py --out unpriced.txt
    .venv\\Scripts\\python.exe dryrun_unpriced.py --csv unpriced.csv

--csv writes one row per dropped job for Excel: sort by `money_found` to put the
ones actually worth checking at the top, and the rest are postings that name no
money at all. Written with a UTF-8 BOM for the same reason as jobsift/export.py.

Nothing is written to the database and no LLM is called.
"""

from __future__ import annotations

import csv
import io
import re
import sys

from jobsift.config import load_config
from jobsift.filters import (
    _NOT_SALARY_NEAR,
    _TEXT_SALARY_K_RE,
    _TEXT_SALARY_RE,
    _matched,
    check,
    normalize_salary_php,
    salary_from_text,
)
from jobsift.sources.jobstreet import fetch_jobs as jobstreet
from jobsift.sources.remote_feeds import fetch_jobs as feeds

# Deliberately looser than the real extractor: ANY currency figure. What the real
# one skipped is exactly what needs eyeballing.
ANY_MONEY = re.compile(r"(?:[\$€£₱]|\b(?:USD|EUR|GBP|PHP|CAD|AUD|SGD)\b)\s?\d[\d,.\s]*\s*[kKmM]?", re.I)


def _opt(flag, default):
    return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv else default


want = str(_opt("--source", "")).lower()
limit = int(_opt("--limit", 20))
out_path = _opt("--out", "")
csv_path = _opt("--csv", "")

config = load_config("config.yaml")
jobs = feeds(config.scrape_sources["remote_feeds"]) + jobstreet(
    {**config.scrape_sources["jobstreet_api"], "max_pages": 1}
)

lines: list[str] = []


def emit(text=""):
    lines.append(text)
    if not out_path:
        print(text)


dropped = [
    j for j in jobs
    if check(j, config.filters)[1] == "no salary listed"
    and (not want or want in j["source"].lower())
]

emit(f"{len(jobs)} fetched; {len(dropped)} dropped for stating no salary"
     + (f" (source filter: {want})" if want else ""))
emit("Check a few of these links by hand. If a posting states pay and we missed it,")
emit("the money line below will show it and say why it was skipped.")
emit("=" * 78)

with_money = 0
csv_rows: list[dict] = []
for job in dropped[:limit]:
    text = job.get("description") or ""
    hits = list(ANY_MONEY.finditer(text))
    emit()
    emit(f"[{job['source']}] {job['title']}")
    emit(f"   {job.get('company') or '(no company)'}")
    emit(f"   {job.get('url') or '(no link)'}")
    emit(f"   salary field: {job.get('salary') or '(empty)'}    posting: {len(text)} chars")
    row = {
        "source": job["source"],
        "title": job["title"],
        "company": job.get("company") or "",
        "url": job.get("url") or "",
        "salary_field": job.get("salary") or "",
        "posting_chars": len(text),
        "money_count": len(hits),
        "money_found": "",
        "why_skipped": "",
        "context": "",
    }
    if not hits:
        emit("   money in posting: NONE — no currency figure anywhere in the text")
        csv_rows.append(row)
        continue
    with_money += 1
    found_all, why_all, ctx_all = [], [], []
    for m in hits[:4]:
        start = max(0, m.start() - 75)
        around = " ".join(text[start:m.end() + 75].split())
        found = " ".join(m.group(0).split())
        # Ask the real extractor about this sentence rather than re-deriving its
        # rules here. An earlier version of this script reimplemented them, got one
        # clause wrong, and printed "WOULD HAVE BEEN ACCEPTED" for a figure the
        # extractor had correctly rejected — a diagnostic that disagrees with the
        # thing it is diagnosing is worse than no diagnostic.
        sentence = text[max(0, m.start() - 150):m.end() + 150]
        accepted = salary_from_text(sentence)
        blocked = _matched(sentence.lower(), _NOT_SALARY_NEAR)
        if accepted:
            why = f"*** ACCEPTED as {accepted!r} — investigate if that is wrong ***"
        elif blocked:
            why = f"skipped: {blocked!r} nearby marks it as not-a-wage"
        else:
            why = "skipped: no period stated ('per hour' / 'per year' / a k suffix)"
        emit(f"   money: {found!r}  -> {why}")
        emit(f"      ...{around}...")
        found_all.append(found)
        why_all.append(why)
        ctx_all.append(around)

    row["money_found"] = " | ".join(found_all)
    row["why_skipped"] = " | ".join(why_all)
    row["context"] = ctx_all[0] if ctx_all else ""
    csv_rows.append(row)

emit()
emit("=" * 78)
emit(f"of the {min(limit, len(dropped))} shown, {with_money} contain any currency figure at all")
emit(f"and {min(limit, len(dropped)) - with_money} contain no money anywhere in the posting.")

if csv_path:
    names = ["source", "title", "company", "url", "salary_field", "posting_chars",
             "money_count", "money_found", "why_skipped", "context"]
    # Sorted so the rows worth a human look — the ones that DO name money — come
    # first, and the long tail of postings that mention no money at all is below.
    csv_rows.sort(key=lambda r: (-r["money_count"], r["source"], r["title"]))
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=names)
        writer.writeheader()
        for r in csv_rows:
            writer.writerow({k: " ".join(str(r.get(k, "")).split()) for k in names})
    print(f"wrote {len(csv_rows)} row(s) to {csv_path}")

if out_path:
    io.open(out_path, "w", encoding="utf-8", newline="\n").write("\n".join(lines))
    print(f"wrote {len(lines)} lines to {out_path}")
