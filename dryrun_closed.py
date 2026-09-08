"""Does staging a row Closed file it into the Closed tab, and nothing else with it?

No network and no service account: the worksheets below are fakes that answer the
same handful of gspread calls the real ones do — column reads, a FORMULA read of
the whole grid, an update at a range, and a row delete. That is enough, because
everything this feature can get wrong is arithmetic over those four:

  * the wrong rows chosen (a stage that merely CONTAINS "Closed", a header row
    counted as data, a row on the tab below the threshold missed),
  * the right rows chosen and the wrong ones deleted (deleting ascending
    renumbers the sheet under you),
  * the row arriving stripped — the link lives in a HYPERLINK formula and the
    displayed value is only the word "open", so a display read moves a caption,
  * the row arriving as a lie — filed under `delisted` when nobody checked the
    board, or still calling itself `new`,
  * and the row coming BACK, because a backfill only knows what the shortlist
    holds and a closed row is still in the database.

    python dryrun_closed.py
"""

from jobsift.sheets import HEADERS, STAGES, SheetWriter

STAGE = HEADERS.index("stage")
DRAFT = HEADERS.index("draft")
TITLE = HEADERS.index("job_title")
URL = HEADERS.index("url")
STATUS = HEADERS.index("status")


def row(stage, title, url, draft="FALSE", status="new"):
    cells = [""] * len(HEADERS)
    cells[STAGE] = stage
    cells[DRAFT] = draft
    cells[TITLE] = f'=HYPERLINK("{url}","{title}")'
    cells[URL] = f'=HYPERLINK("{url}","open")'
    cells[STATUS] = status
    return cells


class FakeSheet:
    """One worksheet, backed by a list of rows with the header row at index 0."""

    def __init__(self, title, rows):
        self.title = title
        self.id = abs(hash(title)) % 10000
        self.rows = [list(HEADERS)] + [list(r) for r in rows]
        self._properties = {"gridProperties": {"rowCount": 1000}}
        self.deleted = []

    # --- the four calls the code under test makes -------------------------
    def col_values(self, number, value_render_option="FORMATTED_VALUE"):
        out = [r[number - 1] if number - 1 < len(r) else "" for r in self.rows]
        if value_render_option != "FORMULA":
            # What the sheet SHOWS. The whole point of the formula columns is
            # that this is not the address, so the fake must not hand one back.
            out = [_displayed(v) for v in out]
        while out and not str(out[-1]).strip():
            out.pop()
        return out

    def row_values(self, number):
        return list(self.rows[number - 1])

    def get_values(self, value_render_option="FORMATTED_VALUE"):
        return [list(r) for r in self.rows]

    def update(self, values, rng, value_input_option="RAW"):
        start = int("".join(c for c in rng if c.isdigit()))
        while len(self.rows) < start - 1 + len(values):
            self.rows.append([""] * len(HEADERS))
        for offset, value in enumerate(values):
            self.rows[start - 1 + offset] = list(value)

    def delete_rows(self, index):
        self.deleted.append(index)
        del self.rows[index - 1]

    def add_rows(self, n):
        self._properties["gridProperties"]["rowCount"] += n

    # --- reading the result ----------------------------------------------
    def jobs(self):
        return [r for r in self.rows[1:] if any(str(c).strip() for c in r)]


def _displayed(value):
    text = str(value)
    if text.startswith('=HYPERLINK('):
        return text.rsplit('"', 2)[-2]
    return text


def writer(shortlist, below, closed):
    """A SheetWriter with its tabs replaced and its constructor skipped, so no
    credential is read and no spreadsheet is opened."""
    w = object.__new__(SheetWriter)
    w.ws, w.ws_below, w.closed_ws = shortlist, below, closed
    w.closed_title = closed.title
    w.threshold = 60
    w.spreadsheet = None
    return w


failures = 0


def check(label, ok, detail=""):
    global failures
    if not ok:
        failures += 1
    print(f"  {'ok  ' if ok else 'FAIL'}  {label:<46} {detail}")


# The dropdown has to offer it before anyone can pick it.
print("the stage itself\n" + "-" * 78)
check("Closed is a stage", "Closed" in STAGES, STAGES)
check("it is last, after the funnel", STAGES[-1] == "Closed", STAGES[-1])

print("\nsweeping\n" + "-" * 78)
shortlist = FakeSheet("Shortlist", [
    row("New", "Backend Engineer", "https://a/1"),
    row("Closed", "Dead Lead", "https://a/2", draft="TRUE"),
    row("Applied", "Full Stack Dev", "https://a/3"),
    row("Closed", "Also Done", "https://a/4"),
    # Near misses. The dropdown is strict, but a paste is not, and neither of
    # these is somebody asking for the row to be filed.
    row("closed", "Lower Case", "https://a/5"),
    row("Not closed yet", "Contains The Word", "https://a/6"),
])
below = FakeSheet("Below", [row("Closed", "Low Score, Done", "https://b/1")])
closed = FakeSheet("Closed", [])
w = writer(shortlist, below, closed)

moved = w.sweep_closed()
check("moved both tabs' Closed rows", moved == 3, f"moved={moved}")
check("shortlist keeps the other four", len(shortlist.jobs()) == 4,
      [_displayed(r[TITLE]) for r in shortlist.jobs()])
check("the below tab is emptied", len(below.jobs()) == 0)
check("deleted bottom-up", shortlist.deleted == sorted(shortlist.deleted, reverse=True),
      shortlist.deleted)
check("the right rows are gone",
      [_displayed(r[TITLE]) for r in shortlist.jobs()]
      == ["Backend Engineer", "Full Stack Dev", "Lower Case", "Contains The Word"])

filed = closed.jobs()
check("three rows arrived", len(filed) == 3, [_displayed(r[TITLE]) for r in filed])
check("the link travelled, not the caption",
      all(r[URL].startswith('=HYPERLINK("http') for r in filed),
      [r[URL] for r in filed])
check("the draft tick survived", filed[0][DRAFT] == "TRUE", filed[0][DRAFT])
check("the stage still says Closed",
      all(r[STAGE] == "Closed" for r in filed), [r[STAGE] for r in filed])
check("status says closed, not new or delisted",
      all(r[STATUS] == "closed" for r in filed), [r[STATUS] for r in filed])

# A second pass over a swept sheet must be a no-op, not a second write: the
# daemon runs this every five minutes forever.
check("a second sweep does nothing", w.sweep_closed() == 0 and len(closed.jobs()) == 3)

print("\nthe crawler's half is unchanged\n" + "-" * 78)
shortlist2 = FakeSheet("Shortlist", [
    row("To apply", "Gone From The Board", "https://c/1"),
    row("New", "Still Up", "https://c/2"),
])
closed2 = FakeSheet("Closed", [])
w2 = writer(shortlist2, None, closed2)
gone = w2.move_to_closed(["https://c/1"])
check("a delisted posting still moves", gone == 1, f"moved={gone}")
check("and is still filed as delisted",
      closed2.jobs()[0][STATUS] == "delisted", closed2.jobs()[0][STATUS])
check("its stage is left alone",
      closed2.jobs()[0][STAGE] == "To apply", closed2.jobs()[0][STAGE])

print("\nand it does not come back\n" + "-" * 78)
# --backfill-sheet reads the whole database and drops what the sheet already
# holds. Before the Closed tab counted, this is the run that undid every close.
seen = w.existing_urls()
check("the closed urls count as already in the sheet",
      {"https://a/2", "https://a/4", "https://b/1"} <= seen, sorted(seen))
check("so does everything still on the shortlist",
      {"https://a/1", "https://a/3"} <= seen)

print("\n" + ("-" * 78) +
      f"\n{'all cases behaved' if not failures else f'{failures} FAILED'}")
