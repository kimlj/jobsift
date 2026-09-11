"""Does the sheet's stage column reach the database, and only when it changes?

No network and no sheet: fake worksheets stand in, shaped like what gspread
returns - a header cell first, trailing empty cells trimmed, the url as a
HYPERLINK formula. The case that matters most is the last one: a job applied to
in the sheet but never confirmed by a board must still count as applied when the
database is read on its own.

    python dryrun_stages.py
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from types import SimpleNamespace

import gspread

from jobsift.sheets import HEADERS, SheetWriter
from jobsift.store import Store

STAGE_COL = HEADERS.index("stage") + 1
URL_COL = HEADERS.index("url") + 1


class FakeTab:
    """One worksheet. rows are (stage, url)."""

    def __init__(self, title, rows):
        self.title = title
        self.rows = rows

    def col_values(self, col, value_render_option=None):
        if col == STAGE_COL:
            values = ["stage"] + [stage for stage, _ in self.rows]
        elif col == URL_COL:
            values = ["url"] + [f'=HYPERLINK("{url}","open")' if url else ""
                                for _, url in self.rows]
        else:
            values = []
        while values and values[-1] == "":   # the API trims trailing empties
            values.pop()
        return values


class FakeSpreadsheet:
    """Lookups only. There is no add_worksheet: a read that tried to create a
    tab would crash here, which is the point."""

    def __init__(self, *tabs):
        self.tabs = {t.title: t for t in tabs}

    def worksheet(self, title):
        if title in self.tabs:
            return self.tabs[title]
        raise gspread.WorksheetNotFound(title)


def writer(shortlist, below, closed=None):
    w = SheetWriter.__new__(SheetWriter)     # skip __init__: no network, no formatting
    w.ws, w.ws_below, w.closed_ws, w.closed_title = shortlist, below, None, "Closed"
    w.spreadsheet = FakeSpreadsheet(*[t for t in (closed,) if t is not None])
    return w


failures = 0


def check(name, got, want):
    global failures
    ok = got == want
    failures += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}"
          + ("" if ok else f"\n        got  {got!r}\n        want {want!r}"))


A, B, C, D, E, F, G = (f"https://example.test/job/{c}" for c in "ABCDEFG")
R = "https://example.test/job/receipt-only"

print("stages(): reading the sheet")
short = FakeTab("Shortlist", [("Applied", A), ("To apply", B), ("TRUE", C), ("Ignore", D), ("", E)])
below = FakeTab("Below the bar", [("Interviewing", F), ("Applied", A)])     # A on both tabs
closed = FakeTab("Closed", [("Rejected", G)])
w = writer(short, below, closed)
st = w.stages()
check("an Applied row reads as Applied, with its tab", st[A], ("Applied", "Shortlist"))
check("a pre-migration TRUE tick reads as Applied", st[C][0], "Applied")
check("a blank stage - trimmed off the end of the column - reads as New", st[E][0], "New")
check("the Closed tab is read too", st[G], ("Rejected", "Closed"))
check("every url exactly once", sorted(st), sorted({A, B, C, D, E, F, G}))
check("applied_urls is unchanged: sent stages and old ticks, job tabs only",
      w.applied_urls(), {A, C, F})
check("with no Closed tab, nothing is created and nothing raises",
      sorted(writer(short, below).stages()), sorted({A, B, C, D, E, F}))

print("\nrecord_stages(): the database copy")
tmp = tempfile.mkdtemp()
try:
    store = Store(os.path.join(tmp, "jobs.db"))
    check("the first sync records every row", store.record_stages(st), 7)
    check("the same sheet again changes nothing", store.record_stages(st), 0)

    moved = dict(st)
    moved[A] = ("Interviewing", "Shortlist")
    check("one stage moved is one change", store.record_stages(moved), 1)
    history = [row[0] for row in store.conn.execute(
        "SELECT stage FROM stage_changes WHERE url = ? ORDER BY rowid", (A,))]
    check("the change is logged after the first sighting", history, ["Applied", "Interviewing"])

    refiled = dict(moved)
    refiled[F] = ("Interviewing", "Closed")
    check("a tab move with the same stage is not a stage change", store.record_stages(refiled), 0)
    check("...but the tab is kept current", store.conn.execute(
        "SELECT tab FROM sheet_stages WHERE url = ?", (F,)).fetchone()[0], "Closed")

    store.record_stages({u: v for u, v in refiled.items() if u != B})
    check("a row deleted from the sheet keeps its last stage", store.conn.execute(
        "SELECT stage FROM sheet_stages WHERE url = ?", (B,)).fetchone()[0], "To apply")

    store.mark_applied(R, SimpleNamespace(
        title="Receipt only", company="X", board="indeed", receipt=True,
        subject="Indeed Application: Receipt only"))
    check("sent_urls = receipts + sent stages; New, To apply and Ignore left out",
          store.sent_urls(), {A, C, F, G, R})
    check("applied_urls still means board receipts only", store.applied_urls(), {R})
    store.conn.close()
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print(f"\n{'all passed' if not failures else f'{failures} FAILED'}")
sys.exit(1 if failures else 0)
