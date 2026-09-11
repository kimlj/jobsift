"""Does --vet store the employer check where every reader will find it?

No network and no service account. The database is a throwaway SQLite file and
the worksheets are fakes answering the two gspread calls the column writer makes
- a FORMULA read of the url column and a batch update - which is all this
feature touches. What it can get wrong:

  * the verdict landing on one copy of a posting that reached us twice, so an
    AVOID is invisible on the other row,
  * a link-less row widened to "every row whose url is empty",
  * an id or url that matches nothing reported as done,
  * a second check piling onto the first instead of replacing it,
  * the research's conclusion leaking into employer_name, which only ever holds
    what the posting itself said,
  * a delisted job's verdict never reaching the sheet, because its row has
    moved to the Closed tab - and a delisted job is exactly the kind that gets
    vetted after the fact.

    python dryrun_vet.py
"""

import json
import os
import tempfile

from jobsift.export import COLUMNS
from jobsift.sheets import HEADERS, SheetWriter, _cell
from jobsift.store import Store

failures = 0


def check(label, ok, detail=""):
    global failures
    if not ok:
        failures += 1
    print(f"  {'ok  ' if ok else 'FAIL'}  {label:<50} {detail}")


def job(url, title, source):
    return {"url": url, "job_title": title, "source": source, "company": "N/A",
            "employer_name": "", "score": 90}


store = Store(os.path.join(tempfile.mkdtemp(), "jobs.db"))
store.save_job("a", job("https://oj/1", "Web Automation Engineer", "onlinejobs_ph"))  # id 1
store.save_job("b", job("https://oj/1", "Web Automation Engineer", "email"))          # 2: same posting
store.save_job("c", job("https://oj/2", "Something Else", "onlinejobs_ph"))          # 3
store.save_job("d", job("", "No Link", "indeed"))                                   # 4
store.save_job("e", job("", "Also No Link", "indeed"))                              # 5


def stored(row_id):
    return json.loads(store.conn.execute(
        "SELECT data FROM jobs WHERE id = ?", (row_id,)).fetchone()[0])


print("the database\n" + "-" * 80)
done = store.mark_vetted("1", "safe", "Founded 2024; $5M raised", "Mogul (usemogul.com)",
                         "2026-09-11")
check("an id reaches both copies of the posting", len(done) == 2, f"updated={len(done)}")
check("the second copy carries it", stored(2).get("vetting") == "safe", stored(2).get("vetting"))
check("an unrelated job is untouched", "vetting" not in stored(3))
check("employer_name stays what the posting said",
      stored(1).get("employer_name") == "", repr(stored(1).get("employer_name")))
check("the research's answer is in vetted_as",
      stored(1).get("vetted_as") == "Mogul (usemogul.com)", stored(1).get("vetted_as"))

again = store.mark_vetted("https://oj/1", "caution", "New facts", "", "2026-09-12")
check("a url works too, and replaces", len(again) == 2
      and (stored(1)["vetting"], stored(1)["vetting_why"], stored(1)["vetted_on"])
      == ("caution", "New facts", "2026-09-12"), stored(1)["vetting"])
check("a check without --as clears the old answer",
      stored(1)["vetted_as"] == "", repr(stored(1)["vetted_as"]))

check("an id that matches nothing says so", store.mark_vetted("999", "safe", "", "", "x") == [])
check("a url that matches nothing says so",
      store.mark_vetted("https://nowhere", "safe", "", "", "x") == [])
lone = store.mark_vetted("4", "avoid", "Asked for a training fee", "", "2026-09-11")
check("a link-less row can still be vetted", len(lone) == 1 and stored(4)["vetting"] == "avoid",
      f"updated={len(lone)}")
check("without widening to every link-less row", "vetting" not in stored(5))

print("\nthe sheet cell\n" + "-" * 80)
full = _cell("vetting", {"vetting": "safe", "vetted_as": "Mogul (usemogul.com)",
                         "vetting_why": "Founded 2024; $5M raised"})
check("verdict, who, why", full == "SAFE - Mogul (usemogul.com): Founded 2024; $5M raised", full)
check("no --as reads cleanly",
      _cell("vetting", {"vetting": "caution", "vetting_why": "Unknown employer"})
      == "CAUTION: Unknown employer")
check("an unvetted row is blank, not 'None'", _cell("vetting", {}) == "")
# Written USER_ENTERED, so a cell opening with = or + would run as a formula.
check("never starts like a formula", not full.startswith(("=", "+", "-", "@")))
at = HEADERS.index("vetting")
check("beside the employer columns", HEADERS[at - 1] == "employer_name", HEADERS[at - 1])
exported = [name for name, _ in COLUMNS]
check("all four fields reach the CSV",
      all(f in exported for f in ("vetting", "vetted_as", "vetting_why", "vetted_on")))


class FakeSheet:
    """A tab holding one row per url, header row first."""

    def __init__(self, title, urls):
        self.title = title
        self.rows = [list(HEADERS)]
        for url in urls:
            cells = [""] * len(HEADERS)
            cells[HEADERS.index("url")] = f'=HYPERLINK("{url}","open")'
            self.rows.append(cells)

    def col_values(self, number, value_render_option="FORMATTED_VALUE"):
        return [r[number - 1] for r in self.rows]

    def batch_update(self, updates, value_input_option="RAW"):
        for update in updates:
            letters = "".join(c for c in update["range"] if c.isalpha())
            row = int("".join(c for c in update["range"] if c.isdigit()))
            column = 0
            for ch in letters:
                column = column * 26 + ord(ch) - 64
            self.rows[row - 1][column - 1] = update["values"][0][0]

    def value(self, url, header):
        for r in self.rows[1:]:
            if f'"{url}"' in r[HEADERS.index("url")]:
                return r[HEADERS.index(header)]
        return None


class FakeSpreadsheet:
    def __init__(self, tabs):
        self.tabs = {ws.title: ws for ws in tabs}

    def worksheet(self, title):
        if title not in self.tabs:
            raise LookupError(title)
        return self.tabs[title]


def writer(shortlist, closed):
    """A SheetWriter with its constructor skipped: no credential, no spreadsheet."""
    w = object.__new__(SheetWriter)
    w.ws, w.ws_below, w.closed_ws, w.closed_title = shortlist, None, None, "Closed"
    w.spreadsheet = FakeSpreadsheet([closed] if closed else [])
    # _tab is the migrating open. Here it only has to hand back the tab, and to
    # fail loudly if it is ever asked for one that does not exist yet.
    w._tab = lambda title, *a, **k: w.spreadsheet.worksheet(title)
    return w


print("\nthe sheet row\n" + "-" * 80)
shortlist = FakeSheet("Shortlist", ["https://oj/2"])
closed = FakeSheet("Closed", ["https://oj/1"])
w = writer(shortlist, closed)
cell = _cell("vetting", stored(1))
n = w.set_vetting({"https://oj/1": cell})
check("a delisted job's row in Closed gets it", n == 1
      and closed.value("https://oj/1", "vetting") == cell, closed.value("https://oj/1", "vetting"))
check("another job's row is untouched", shortlist.value("https://oj/2", "vetting") == "")

bare = FakeSheet("Shortlist", ["https://oj/2"])
w2 = writer(bare, None)
try:
    n2 = w2.set_vetting({"https://oj/2": "SAFE"})
    check("no Closed tab: none made, the row still written",
          n2 == 1 and bare.value("https://oj/2", "vetting") == "SAFE", f"written={n2}")
except LookupError as err:
    check("no Closed tab: none made, the row still written", False, f"asked for {err}")

# The refactor under set_vetting is shared with draft_status, which must keep
# to the job tabs: a queued draft for a closed row is a request nobody made.
w.set_draft_status({"https://oj/2": "queued", "https://oj/1": "queued"})
check("draft_status still reaches the job tabs",
      shortlist.value("https://oj/2", "draft_status") == "queued")
check("and still leaves Closed alone", closed.value("https://oj/1", "draft_status") == "")

store.conn.close()
print("\n" + ("-" * 80) +
      f"\n{'all cases behaved' if not failures else f'{failures} FAILED'}")
raise SystemExit(1 if failures else 0)
