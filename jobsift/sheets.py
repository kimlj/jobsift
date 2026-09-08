"""Optional output — append every scored job to a Google Sheet for browsing."""

from __future__ import annotations

import logging

from .filters import currency_for, normalize_salary_php
from .utils import hyperlink

logger = logging.getLogger(__name__)

# Column order for the sheet (also the header row).
# The user's funnel position for a row. Ordered as the funnel runs, because the
# dropdown shows them in this order and reading it top to bottom should be the
# life of an application.
STAGES = ["New", "To apply", "Applied", "Interviewing", "Rejected", "Ignore",
          "Closed"]
STAGE_DEFAULT = "New"

# The one stage that is also an instruction. Every other value describes where a
# row stands; this one asks for the row to be filed, and the next pass moves it
# into the Closed tab. It sits last rather than beside the other two dead ends
# because it is not a funnel position at all - a job can be closed out of any of
# them, including Interviewing.
STAGE_CLOSED = "Closed"

# Background and text for each stage, as "#rrggbb". Chosen so the column can be
# read down at a glance rather than word by word: Interviewing is the only one
# that should catch the eye across a screenful, To apply is the one asking for
# an action, and the two dead ends are drained of colour on purpose - Rejected
# because it is over, Ignore because you already decided. New is nearly white so
# an untriaged row reads as background rather than as a state. Closed is drained
# too but in a different hue, because a row filed away and a row ignored in place
# are different answers and the Closed tab holds both.
STAGE_COLOURS = {
    "New":          ("#F8F9FA", "#80868B"),
    "To apply":     ("#FEF7E0", "#B06000"),
    "Applied":      ("#E8F0FE", "#1967D2"),
    "Interviewing": ("#CEEAD6", "#0D652D"),
    "Rejected":     ("#FCE8E6", "#C5221F"),
    "Ignore":       ("#E8EAED", "#9AA0A6"),
    "Closed":       ("#EFEBE9", "#8D6E63"),
}


def _rgb(value: str) -> dict:
    """"#rrggbb" to the 0-1 floats the Sheets API wants."""
    value = value.lstrip("#")
    return {"red": int(value[0:2], 16) / 255,
            "green": int(value[2:4], 16) / 255,
            "blue": int(value[4:6], 16) / 255}

# Stages the program may overwrite when a receipt arrives. Everything else is a
# decision the user made by hand and knows more about than we do: a receipt
# landing on a row marked Ignore means either they applied and changed their
# mind, or the match was wrong, and writing over it is the wrong answer to both.
# This is the dropdown form of the old rule that a tick was never cleared.
STAGE_ADVANCEABLE = {"", STAGE_DEFAULT, "To apply"}

# Stages that mean an application went in. Read by --skip-applied and by the
# receipt scanner, so a job already sent is not offered again.
STAGE_SENT = {"Applied", "Interviewing", "Rejected"}

# Renamed columns, old -> new. Checked BEFORE the unknown-column guard in
# _migrate_headers, which would otherwise see the old name, decide it cannot be
# placed, and leave the tab frozen on the previous version forever.
_RENAMED = {"applied": "stage"}

HEADERS = [
    # Same leading order as the CSV export: the columns you decide on, first.
    # Both outputs answer "is this worth opening" and they had drifted apart —
    # score sat at column N here while the export put it third.
    #
    # url is beside company because the sheet is scanned and clicked down. Sheets
    # auto-links a bare URL under USER_ENTERED, so unlike the CSV this needs no
    # HYPERLINK formula.
    # "stage" is yours, not the program's. Every new row is written as New and
    # the program only ever advances it to Applied, so it can carry a dropdown
    # and stay frozen at column A. It sits there because append_row writes from
    # column A outward: a column you added yourself would be overwritten by the
    # next row. It was a tickbox called `applied` until a tick could not tell
    # "not looked at" from "want this" from "no thanks".
    "stage",
    # Tick this and the next pass drafts an application for the row: cover letter,
    # employer answers, tailored resume, into the Drafts tab. Yours to set, like
    # `applied` - the program only ever reads it, and it costs one model call per
    # tick, which is why nothing ticks it for you.
    "draft",
    # Written BY the program, unlike the two boxes either side of it. A sheet
    # cannot call anything - the program polls it - so a tick sits there looking
    # ignored for up to one poll interval. This column is where the answer goes:
    # queued, drafting, or a link straight to the finished draft.
    "draft_status",
    "score",
    # How much of the posting the score was computed from, beside the score
    # itself, because the two are read together or not at all. 48 of the first
    # 83 rows to reach the shortlist were scored on 182 characters or fewer -
    # mostly on the title - and in the sheet an 89 from a title looked exactly
    # like an 89 from three thousand words.
    "scored_on",
    # source sits between the link and the employer because it is read as part
    # of the link: which board this came from decides how much to trust the row
    # beside it, and the scored_on column two places left says the same thing in
    # numbers. onlinejobs.ph rows carry the whole ad; indeed rows carry a title.
    # salary as the board wrote it, then the same figure normalised to pesos a
    # month. Both, because they answer different questions: the first is what the
    # employer said, the second is what the filter compared, and a row that looks
    # underpaid is usually one where those two disagree ("$25/hr", "50k PA").
    "job_title", "url", "source", "salary", "salary_php_monthly", "company",
    # Beside company, not instead of it. company is what the board handed over
    # and is empty on the sources that hide the employer; this is what the
    # posting text called itself, and on onlinejobs.ph rows it is the only one
    # of the two that ever says anything.
    "employer_name",
    "location", "remote",
    "timestamp", "job_type", "experience_level", "duration",
    "skills_required", "description_summary", "skill_match", "experience_fit",
    "interest_fit", "matching_skills", "missing_skills", "reasoning", "status",
    "reported_at",
]


# The drafts log. A separate tab rather than more columns on the job row: a cover
# letter is a page of prose, and a row carrying one cannot be scanned past. Keyed by
# url like everything else, and appended rather than replaced, so re-drafting a job
# leaves the earlier attempt to compare against.
DRAFT_HEADERS = [
    "drafted_at", "score", "job_title", "company", "url", "apply_via",
    "salary_answer", "gaps", "cover_letter", "tailored_resume", "questions",
]


def _a1(index: int) -> str:
    """Column index to its A1 letters. Past 25 columns "Z"+1 is not "[" but "AA",
    and HEADERS is already 27 long."""
    letters = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        letters = chr(ord("A") + rest) + letters
    return letters


def _cell(header: str, record: dict) -> str:
    """One cell. `applied` is the user's tickbox, so it goes out as FALSE rather
    than empty — an empty cell under tickbox validation reads as blank, a FALSE
    reads as unticked. `url` gets the narrow clickable form; see utils.hyperlink."""
    if header == "draft":
        return "FALSE"
    if header == "stage":
        return STAGE_DEFAULT
    if header == "scored_on":
        # Composed rather than stored: the record carries the two halves and this
        # is the one place they are read as a sentence.
        kind = str(record.get("evidence") or "unknown")
        count = int(record.get("evidence_chars") or 0)
        if kind == "full":
            return "full"
        # The exact count is in the CSV as evidence_chars. Here it was noise:
        # the column is scanned down a list to sort the trustworthy rows from
        # the rest, and 182 against 155 is not a distinction anyone acts on.
        return "snippet" if count else "title"
    if header == "job_title":
        # The title IS the link. Hovering a separate one-word cell, waiting for
        # the preview, and clicking that is three actions for the thing the row
        # exists to do.
        title = str(record.get("job_title") or "")
        linked = hyperlink(record.get("url", ""), title)
        # hyperlink hands back its input unchanged when it is not a real link, so
        # a row with no url keeps a plain title rather than printing "N/A".
        return linked if linked.startswith("=HYPERLINK(") else title
    if header == "salary_php_monthly":
        # Written as a bare number so the column sorts and filters as one.
        stored = record.get("salary_php_monthly")
        if stored not in (None, ""):
            return str(stored)
        # Recomputed, not stored, exactly as the CSV does it: the row then shows
        # what TODAY's parser makes of the salary, which is the number that
        # answers "why was this kept or dropped".
        value = normalize_salary_php(
            record.get("salary") or "",
            job_type=record.get("job_type") or "",
            default_currency=currency_for(record),
        )
        return str(round(value)) if value else ""
    if header == "url":
        return hyperlink(record.get("url", ""))
    return str(record.get(header, ""))


class SheetWriter:
    """Wraps one or two gspread worksheets. Created only when enabled is true.

    The sheet is split by score rather than filtered by it. Every job that got
    past the filters and was scored lands in one tab or the other, because a job
    the scorer put at 58 is the one worth arguing with — it is not noise, it is
    the boundary, and a sheet holding only what already convinced the scorer
    cannot show where the bar is wrong. The shortlist stays scannable; the other
    tab is there for a quiet week, and for checking the threshold itself.

    Both tabs carry the same headers, tick boxes and filter, so a row reads the
    same way whichever tab it is in.
    """

    def __init__(self, gs_config, threshold: int = 0):
        import gspread

        from google.oauth2.service_account import Credentials

        creds = Credentials.from_service_account_file(
            gs_config.service_account_file,
            scopes=["https://www.googleapis.com/auth/spreadsheets"],
        )
        client = gspread.authorize(creds)
        self.spreadsheet = client.open_by_key(gs_config.sheet_id)
        self.threshold = threshold

        self.ws = self._tab(gs_config.worksheet)
        # An empty worksheet_below turns the split off and sends everything to
        # the one tab, which is what every sheet made before the split had.
        below = getattr(gs_config, "worksheet_below", "")
        self.ws_below = self._tab(below) if below else None
        self.draft_ws = None
        # Name now, tab later. Created on the first delisted posting, so a sheet
        # whose jobs are all still open never grows an empty tab explaining that.
        self.closed_title = getattr(gs_config, "worksheet_closed", "")
        self.closed_ws = None

    def _tab(self, title: str, headers: list | None = None, tickbox: bool = True,
             clip: bool = False):
        """Fetch or create one tab, header it, and make it usable."""
        import gspread

        headers = headers or HEADERS
        try:
            ws = self.spreadsheet.worksheet(title)
        except gspread.WorksheetNotFound:
            ws = self.spreadsheet.add_worksheet(
                title=title, rows=1000, cols=len(headers)
            )

        # Compare row 1 to HEADERS rather than asking whether the sheet is
        # empty: a freshly created worksheet does not reliably read back as
        # empty, and a tab that skipped its header silently writes every later
        # row one column off nothing and looks blank at the top.
        current = ws.row_values(1)
        current = self._migrate_renames(ws, current)
        if current != headers:
            if not self._migrate_headers(ws, current, headers):
                return ws
            ws.update([headers], "A1", value_input_option="RAW")

        self._setup(ws, headers, tickbox, clip)
        return ws

    def _migrate_renames(self, ws, current: list) -> list:
        """Rename columns this version renamed, converting their values first.

        This has to run BEFORE `_migrate_headers`, which refuses a tab holding
        any column it does not know. An existing sheet still says `applied`, so
        without this the guard would fire on every run, log a warning, and leave
        the tab frozen on the previous version forever - the upgrade would never
        arrive for exactly the people who already have data.

        `applied` was a tick box: TRUE became Applied, and everything else
        becomes New rather than empty, because an unticked row never meant
        "rejected", only "no receipt has been seen for this". The boolean
        validation is cleared in the same batch, or the sheet refuses the words
        being written into a column still expecting a checkbox. `_setup` puts
        the dropdown on afterwards.

        Returns the header list as it now stands, so the caller carries on with
        what the sheet actually holds rather than what it held a moment ago.
        """
        renames = [(old, new) for old, new in _RENAMED.items() if old in current]
        if not renames:
            return current

        updated = list(current)
        for old, new in renames:
            index = updated.index(old)
            letter = _a1(index)
            # Bounded by the rows that hold a JOB, not by what the column
            # returns. Under the tick-box validation every cell in this column
            # exists as far as the API is concerned, so col_values comes back
            # 1000 long on a tab holding two rows - the same reason `_next_row`
            # reads job_title instead of asking for the end of the table. The
            # first run of this wrote "New" into 999 empty rows of the Closed
            # tab before that was caught.
            try:
                last = self._next_row(ws) - 1
                values = ws.col_values(index + 1)[1:last] if last > 1 else []
            except Exception as err:
                logger.warning("could not read %s on %s: %s", old, ws.title, err)
                return current

            self.spreadsheet.batch_update({"requests": [{"setDataValidation": {
                "range": {"sheetId": ws.id, "startRowIndex": 1, "endRowIndex": 5000,
                          "startColumnIndex": index, "endColumnIndex": index + 1}}}]})

            if old == "applied":
                converted = [
                    ["Applied" if str(v).strip().upper() in ("TRUE", "1", "YES")
                     else STAGE_DEFAULT]
                    for v in values
                ]
                if converted:
                    ws.update(converted, f"{letter}2:{letter}{len(converted) + 1}",
                              value_input_option="USER_ENTERED")
            ws.update([[new]], f"{letter}1", value_input_option="RAW")
            updated[index] = new
            logger.info("%s: %s -> %s (%d row(s) converted)",
                        ws.title, old, new, len(values))
        return updated

    def _migrate_headers(self, ws, current: list, headers: list) -> bool:
        """Bring an existing tab's columns to `headers`, data and all.

        Every column change shipped here has to reach a sheet somebody already
        has, or the upgrade quietly writes new labels over old values: rewriting
        row 1 is the one thing that must never be done on its own. So columns are
        added where they belong and moved to where they belong, and only then is
        the header row written - by which point writing it changes nothing.

        Returns False when the tab holds a column this version does not know. It
        is not necessarily wrong (somebody's own notes column), but it cannot be
        placed, so the row is left exactly as it is and the caller does nothing.
        """
        unknown = [h for h in current if h and h not in headers]
        if unknown:
            logger.warning(
                "%s has columns this version does not know (%s); leaving row 1 alone. "
                "Remove or rename them to let the sheet migrate.", ws.title, unknown)
            return False

        # `now` tracks what the sheet looks like as each request is applied, so
        # the whole migration can be planned in one batch instead of re-reading.
        now = list(current)
        requests = []

        for name in sorted([h for h in headers if h not in now], key=headers.index):
            at = headers.index(name)
            at = min(at, len(now))
            requests.append({"insertDimension": {
                "range": {"sheetId": ws.id, "dimension": "COLUMNS",
                          "startIndex": at, "endIndex": at + 1},
                "inheritFromBefore": False}})
            now.insert(at, name)

        for target, name in enumerate(headers):
            if target >= len(now) or now[target] == name:
                continue
            frm = now.index(name)
            # destinationIndex is read in the coordinates BEFORE the column is
            # lifted out, so moving right needs one more than the final position.
            requests.append({"moveDimension": {
                "source": {"sheetId": ws.id, "dimension": "COLUMNS",
                           "startIndex": frm, "endIndex": frm + 1},
                "destinationIndex": target if target < frm else target + 1}})
            now.insert(target, now.pop(frm))

        if requests:
            try:
                self.spreadsheet.batch_update({"requests": requests})
            except Exception as err:
                logger.warning("could not migrate %s: %s", ws.title, err)
                return False
            logger.info("%s: migrated columns to this version (%d change(s))",
                        ws.title, len(requests))
        return True

    def resync(self, records: dict) -> int:
        """Rewrite every row this version computes, from `records` keyed by url.

        A column can change meaning without changing name - scored_on lost its
        character counts, the monthly figure moved to a live exchange rate, the
        title became the link - and none of that reaches rows already written.
        A new user never notices, because their first write is already current;
        the person who has been running it for a month sees a sheet half in each
        version, which is worse than either.

        `stage`, `draft` and `draft_status` are skipped. The first two are the
        user's, and the third describes work in flight.
        """
        mine = {"stage", "draft", "draft_status"}
        writable = [(i, h) for i, h in enumerate(HEADERS) if h not in mine]
        total = 0
        for ws in self._tabs():
            updates = []
            for url, row in self._url_rows(ws).items():
                record = records.get(url)
                if not record:
                    continue
                for index, header in writable:
                    updates.append({"range": f"{_a1(index)}{row}",
                                    "values": [[_cell(header, record)]]})
            # Sheets rejects an enormous single batch; 5,000 cells is well inside
            # the limit and is about 200 rows at a time.
            for start in range(0, len(updates), 5000):
                ws.batch_update(updates[start:start + 5000],
                                value_input_option="USER_ENTERED")
            if updates:
                total += len(updates) // len(writable)
                logger.info("%s: resynced %d row(s)", ws.title, len(updates) // len(writable))
        return total

    def _target(self, record: dict):
        """Which tab a record belongs in. Below the bar only when there is a tab
        for it and the score reads as a number — an unscored or malformed row
        goes to the shortlist, where it will actually be noticed."""
        if self.ws_below is None:
            return self.ws
        try:
            score = int(record.get("score") or 0)
        except (TypeError, ValueError):
            return self.ws
        return self.ws if score >= self.threshold else self.ws_below

    def _existing_dropdown(self, ws, column: int) -> tuple:
        """(is there a rule on row 2 of this column, which values it offers).

        Asked so `_setup` can leave an existing dropdown alone. The chip style -
        the rounded pill, with a colour per value - can only be made from the
        Sheets UI under Insert > Dropdown. The v4 API reports one back as an
        ordinary ONE_OF_LIST, identical field for field to the rule this program
        writes, so it cannot be told apart afterwards and cannot be recreated.
        What it CAN do is not destroy it: without this check, every run would
        overwrite a hand-made chip dropdown with a plain one, and the loss would
        look like Sheets forgetting rather than like us doing it.

        The offered values come back with it because that protection has a cost:
        a stage added by a later version cannot reach a hand-made dropdown
        either, and the symptom - Sheets refusing the very word the program is
        asking for - reads as a bug rather than as one menu somebody has to open.
        So the list is compared and the difference is named in the log.

        Unreadable is treated as present with unknown values, because writing
        over something we could not see is the failure this exists to prevent.
        """
        letter = _a1(column)
        try:
            meta = self.spreadsheet.fetch_sheet_metadata(
                {"includeGridData": True, "ranges": [f"'{ws.title}'!{letter}2:{letter}2"]})
            rows = meta["sheets"][0]["data"][0].get("rowData", [])
            if not rows:
                return False, []
            values = rows[0].get("values", [])
            rule = values[0].get("dataValidation") if values else None
            if not rule:
                return False, []
            offered = [str(v.get("userEnteredValue", ""))
                       for v in rule.get("condition", {}).get("values", [])]
            return True, offered
        except Exception as err:
            logger.warning("could not read validation on %s: %s", ws.title, err)
            return True, None

    def _stage_colour_requests(self, ws, column: int) -> list:
        """Conditional formatting for the stage column, one rule per value.

        Deletes this tab's existing rules on that column before adding them
        back, because conditional formats do not overwrite: adding the same six
        every run would stack sixty by the tenth, all agreeing, all evaluated.
        That is the one part of `_setup` that is not naturally idempotent.

        Deletions are emitted in DESCENDING index order. The rules live in a
        list and each delete renumbers the ones after it, so removing 0 then 1
        removes the wrong second rule.

        Rules the user added themselves on other columns are left alone; only
        ones covering this column are replaced.
        """
        existing = []
        try:
            meta = self.spreadsheet.fetch_sheet_metadata()
            for sheet in meta.get("sheets", []):
                if sheet.get("properties", {}).get("sheetId") != ws.id:
                    continue
                for index, rule in enumerate(sheet.get("conditionalFormats", [])):
                    for span in rule.get("ranges", []):
                        if (span.get("startColumnIndex") == column
                                and span.get("endColumnIndex") == column + 1):
                            existing.append(index)
                            break
        except Exception as err:
            # Without the read we cannot tell new rules from old, and adding
            # blind is how the stack happens. Skipping colour is the safe miss.
            logger.warning("could not read conditional formats on %s: %s", ws.title, err)
            return []

        requests = [
            {"deleteConditionalFormatRule": {"sheetId": ws.id, "index": index}}
            for index in sorted(existing, reverse=True)
        ]
        span = {"sheetId": ws.id, "startRowIndex": 1, "endRowIndex": 5000,
                "startColumnIndex": column, "endColumnIndex": column + 1}
        for offset, stage in enumerate(STAGES):
            background, foreground = STAGE_COLOURS[stage]
            requests.append({"addConditionalFormatRule": {
                "index": offset,
                "rule": {
                    "ranges": [dict(span)],
                    "booleanRule": {
                        "condition": {"type": "TEXT_EQ",
                                      "values": [{"userEnteredValue": stage}]},
                        "format": {
                            "backgroundColor": _rgb(background),
                            "textFormat": {"foregroundColor": _rgb(foreground),
                                           "bold": stage == "Interviewing"},
                        },
                    },
                },
            }})
        return requests

    def _setup(self, ws, headers: list | None = None, tickbox: bool = True,
               clip: bool = False) -> None:
        """Make a tab usable without anyone opening a menu.

        A stage dropdown and its colours, a draft tick box, a frozen header row
        and first column, and a filter on row 1 so it can be sorted by score.
        All of it is idempotent — re-applying the same validation and freeze is
        a no-op, a basic filter that already exists is left alone, and the
        conditional formats are deleted before being re-added because those
        would otherwise stack.

        Validation runs to row 5000 rather than the current last row, so rows
        appended by later runs land inside it and get a tick box too.
        """
        headers = headers or HEADERS
        url_col = headers.index("url")
        requests = []
        if tickbox:
            column = headers.index("draft")
            requests.append({"setDataValidation": {
                "range": {"sheetId": ws.id, "startRowIndex": 1,
                          "endRowIndex": 5000,
                          "startColumnIndex": column,
                          "endColumnIndex": column + 1},
                "rule": {"condition": {"type": "BOOLEAN"}, "showCustomUi": True}}})
            # stage is a dropdown, not a tick, and strict=True does two things
            # at once. It is what makes Sheets draw the value as a rounded chip
            # rather than a bare cell with an arrow, which is the whole reason
            # the column is scannable. And it stops a typo: `mark_applied` and
            # `applied_urls` compare these strings exactly, so an "applied" that
            # should have been "Applied" would quietly drop a row out of
            # STAGE_SENT and offer a job back that was already sent. An earlier
            # version used strict=False to avoid arguing with someone typing
            # their own word into their own column; the closed set of seven is
            # worth more than that freedom.
            column = headers.index("stage")
            present, offered = self._existing_dropdown(ws, column)
            if present and offered:
                missing = [s for s in STAGES if s not in offered]
                if missing:
                    # Named, not written. A dropdown made by hand is the only
                    # kind that draws chips, and replacing it to add one value
                    # would trade the whole column's readability for it.
                    logger.warning(
                        "%s: the stage dropdown does not offer %s - add %s by hand "
                        "under Data > Data validation, or the sheet will refuse "
                        "the word", ws.title, ", ".join(missing),
                        "them" if len(missing) > 1 else "it")
            if not present:
                requests.append({"setDataValidation": {
                    "range": {"sheetId": ws.id, "startRowIndex": 1,
                              "endRowIndex": 5000,
                              "startColumnIndex": column,
                              "endColumnIndex": column + 1},
                    "rule": {"condition": {"type": "ONE_OF_LIST",
                                           "values": [{"userEnteredValue": v} for v in STAGES]},
                             "showCustomUi": True, "strict": True}}})
            requests += self._stage_colour_requests(ws, column)
        if "salary" in headers:
            # As-written salaries are text, but Sheets reads a bare "1500" as a
            # number and right-aligns it, so the column comes out ragged: "$17/hr"
            # left, "1500" right, for no reason a reader can see. Forced left, and
            # the raggedness it was signalling now lives in salary_php_monthly,
            # which is a number on purpose.
            written = headers.index("salary")
            requests.append({"repeatCell": {
                "range": {"sheetId": ws.id, "startRowIndex": 1,
                          "startColumnIndex": written, "endColumnIndex": written + 1},
                "cell": {"userEnteredFormat": {"horizontalAlignment": "LEFT"}},
                "fields": "userEnteredFormat.horizontalAlignment"}})

        if "salary_php_monthly" in headers:
            # The cell holds a plain number so the column sorts, filters and
            # compares as one; the peso sign and the separators are a display
            # format over it. Writing "P80,000" as text would give a column that
            # sorts 100,000 before 80,000 and cannot be summed.
            money = headers.index("salary_php_monthly")
            requests.append({"repeatCell": {
                "range": {"sheetId": ws.id, "startRowIndex": 1,
                          "startColumnIndex": money, "endColumnIndex": money + 1},
                "cell": {"userEnteredFormat": {
                    "numberFormat": {"type": "NUMBER", "pattern": '"₱"#,##0'}}},
                "fields": "userEnteredFormat.numberFormat"}})

        if clip:
            # A cover letter and a resume in one row make it about 950px tall, and
            # two of those fill the screen. Clipped, every draft is one line and
            # the cell still holds the whole text to click into and copy.
            requests.append({"repeatCell": {
                "range": {"sheetId": ws.id, "startRowIndex": 1},
                "cell": {"userEnteredFormat": {"wrapStrategy": "CLIP"}},
                "fields": "userEnteredFormat.wrapStrategy"}})
            requests.append({"updateDimensionProperties": {
                "range": {"sheetId": ws.id, "dimension": "ROWS",
                          "startIndex": 1, "endIndex": 5000},
                "properties": {"pixelSize": 21}, "fields": "pixelSize"}})
        requests += [
            {"updateSheetProperties": {
                "properties": {"sheetId": ws.id,
                               "gridProperties": {"frozenRowCount": 1,
                                                  # Through score: the two tick
                                                  # boxes, their status, and the
                                                  # number you scroll to compare
                                                  # everything else against.
                                                  "frozenColumnCount": 4 if tickbox else 0}},
                "fields": "gridProperties.frozenRowCount,gridProperties.frozenColumnCount"}},
            {"repeatCell": {
                "range": {"sheetId": ws.id, "startRowIndex": 0, "endRowIndex": 1},
                "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
                "fields": "userEnteredFormat.textFormat.bold"}},
            {"updateDimensionProperties": {
                "range": {"sheetId": ws.id, "dimension": "COLUMNS",
                          "startIndex": url_col, "endIndex": url_col + 1},
                "properties": {"pixelSize": 60}, "fields": "pixelSize"}},
        ]
        try:
            self.spreadsheet.batch_update({"requests": requests})
        except Exception as err:
            logger.warning("sheet setup skipped for %s: %s", ws.title, err)

        # A basic filter can only exist once per sheet, and asking for a second
        # is an error rather than a no-op, so this one is tried separately and
        # its failure ignored.
        try:
            self.spreadsheet.batch_update({"requests": [{"setBasicFilter": {
                "filter": {"range": {"sheetId": ws.id,
                                     "startColumnIndex": 0,
                                     "endColumnIndex": len(headers)}}}}]})
        except Exception:
            pass

    def _tabs(self) -> list:
        return [w for w in (self.ws, self.ws_below) if w is not None]

    def applied_urls(self) -> set[str]:
        """The urls of jobs whose stage says an application went in, both tabs.

        Interviewing and Rejected count as sent: both are states you can only
        reach by having applied, and a job already sent should not be offered
        back as though it were new.

        Both, because a job worth applying to may well be one the scorer put
        below the bar — that is the case the second tab exists for.

        Matched on url because it is the only field a person will not retype.
        The cell holds a HYPERLINK formula, so the url is read from the
        formula rather than the rendered value, which is just the word open.
        """
        import re

        applied_col = HEADERS.index("stage")
        url_col = HEADERS.index("url")
        out: set[str] = set()

        for ws in self._tabs():
            try:
                flags = ws.col_values(applied_col + 1)[1:]
                formulas = ws.col_values(
                    url_col + 1, value_render_option="FORMULA")[1:]
            except Exception as err:
                logger.warning("could not read stage column on %s: %s", ws.title, err)
                continue

            for flag, cell in zip(flags, formulas):
                value = str(flag).strip()
                # TRUE is still honoured: a sheet mid-migration, or one whose
                # rename could not run, still holds the old tick.
                if value not in STAGE_SENT and value.upper() not in ("TRUE", "1", "YES"):
                    continue
                match = re.search(r'HYPERLINK\("([^"]+)"', str(cell))
                url = match.group(1) if match else str(cell).strip()
                if url:
                    out.add(url)
        return out

    def _url_rows(self, ws) -> dict[str, int]:
        """url -> its 1-based row on this tab."""
        import re

        url_col = HEADERS.index("url")
        try:
            cells = ws.col_values(url_col + 1, value_render_option="FORMULA")
        except Exception as err:
            logger.warning("could not read url column on %s: %s", ws.title, err)
            return {}
        out: dict[str, int] = {}
        for i, cell in enumerate(cells[1:], start=2):
            match = re.search(r'HYPERLINK\("([^"]+)"', str(cell))
            url = match.group(1) if match else str(cell).strip()
            if url:
                out.setdefault(url, i)
        return out

    def mark_applied(self, urls) -> int:
        """Move rows whose url is in `urls` to the Applied stage.

        ADVANCES ONLY, and only out of STAGE_ADVANCEABLE. A row the user has
        moved on by hand is left where they put it: Ignore means they decided
        against it, and Interviewing or Rejected are further down the funnel
        than a receipt can see. Writing Applied over any of those would replace
        something they know with something we inferred.

        This is the dropdown form of the old rule. The tick was never cleared
        because a board confirms an application and never its absence; the stage
        is never rewound for the same reason.
        """
        wanted = set(urls)
        if not wanted:
            return 0
        stage_col = HEADERS.index("stage")
        letter = _a1(stage_col)
        moved = 0
        for ws in self._tabs():
            rows = [row for url, row in self._url_rows(ws).items() if url in wanted]
            if not rows:
                continue
            try:
                column = ws.col_values(stage_col + 1)
            except Exception as err:
                logger.warning("could not read stage on %s: %s", ws.title, err)
                continue
            updates = []
            for row in sorted(rows):
                # col_values is a 0-based list over a 1-based sheet, and comes
                # back short when the trailing cells are empty.
                now = column[row - 1].strip() if row - 1 <= len(column) - 1 else ""
                if now.upper() in ("TRUE", "1", "YES"):
                    continue  # a pre-migration tick: already applied
                if now not in STAGE_ADVANCEABLE:
                    logger.info("%s row %d left at %r", ws.title, row, now)
                    continue
                updates.append({"range": f"{letter}{row}", "values": [["Applied"]]})
            if not updates:
                continue
            try:
                ws.batch_update(updates, value_input_option="USER_ENTERED")
                moved += len(updates)
            except Exception as err:
                logger.warning("could not set stage on %s: %s", ws.title, err)
        return moved

    def _closed_tab(self):
        """The Closed tab, made on first use. None when the feature is off."""
        if not self.closed_title:
            return None
        if self.closed_ws is None:
            self.closed_ws = self._tab(self.closed_title)
        return self.closed_ws

    def move_to_closed(self, urls, status: str = "delisted") -> int:
        """Move rows out of the job tabs and into the Closed tab.

        Moved, never deleted. The row is a real job that was really scored, and
        your `applied` and `draft` ticks are in it — losing those would relitigate
        applications you have already sent. So the whole row travels, ticks and
        all, and the shortlist is left holding only jobs that can still be
        applied to.

        Two things this has to get right:

        * **Read as FORMULA.** The job title carries the link (see
          `docs/decisions.md`), so the displayed value is a caption and the
          address lives in a HYPERLINK the default render option throws away.
          Copying display values would move every row and silently unlink it.
        * **Delete from the bottom up.** Removing row 12 renumbers everything
          under it, so deleting ascending would take the wrong rows out from the
          second one onward. `hits` is sorted descending for exactly this.

        `status` is the word the moved row ends up carrying: "delisted" when the
        crawler found the posting gone from its board, "closed" when the user
        filed it by hand. The tab holds both and they are not the same event, so
        the column has to be able to tell them apart afterwards.

        Returns how many rows moved.
        """
        target = self._closed_tab()
        if target is None:
            return 0
        wanted = {u for u in (urls or ()) if u}
        if not wanted:
            return 0

        moved = 0
        for ws in (self.ws, self.ws_below):
            if ws is None or ws is target:
                continue
            rows_by_url = self._url_rows(ws)
            hits = sorted(
                ((rows_by_url[u], u) for u in wanted if u in rows_by_url), reverse=True
            )
            if not hits:
                continue
            try:
                values = ws.get_values(value_render_option="FORMULA")
            except Exception as err:
                logger.warning("could not read %s to move closed rows: %s", ws.title, err)
                continue

            status_col = HEADERS.index("status")
            payload = []
            for index, _ in reversed(hits):  # back to sheet order for the write
                if index - 1 < len(values):
                    row = list(values[index - 1])
                    row += [""] * (len(HEADERS) - len(row))
                    # The row is copied verbatim so the ticks survive, but its
                    # status was written at ingest and still says "new". A row
                    # filed under Closed that calls itself new is the sheet
                    # disagreeing with itself, so this one cell is restated -
                    # with WHY it closed, which is the caller's to say.
                    row[status_col] = status
                    payload.append(row)
            if not payload:
                continue

            self._write(target, payload)
            for index, _ in hits:
                try:
                    ws.delete_rows(index)
                except Exception as err:
                    logger.warning("moved row %d off %s but could not delete it: %s",
                                   index, ws.title, err)
            moved += len(payload)
            logger.info("moved %d closed posting(s) from %s to %s",
                        len(payload), ws.title, target.title)
        return moved

    def _staged_urls(self, stage: str) -> set[str]:
        """Urls whose stage cell holds exactly `stage`, across both job tabs.

        Exactly, and with no case folding: the dropdown is strict, so the value
        came out of the list or it came out of a paste, and a near miss is not a
        stage. The url is read from the HYPERLINK formula for the usual reason -
        the rendered value is only the word "open".
        """
        import re

        stage_col = HEADERS.index("stage")
        url_col = HEADERS.index("url")
        out: set[str] = set()
        for ws in self._tabs():
            try:
                stages = ws.col_values(stage_col + 1)[1:]
                formulas = ws.col_values(
                    url_col + 1, value_render_option="FORMULA")[1:]
            except Exception as err:
                logger.warning("could not read stage column on %s: %s", ws.title, err)
                continue
            for value, cell in zip(stages, formulas):
                if str(value).strip() != stage:
                    continue
                match = re.search(r'HYPERLINK\("([^"]+)"', str(cell))
                url = match.group(1) if match else str(cell).strip()
                if url:
                    out.add(url)
        return out

    def sweep_closed(self) -> int:
        """File every row the user staged Closed into the Closed tab.

        The other half of that tab. `move_to_closed` empties the shortlist of
        jobs that cannot be applied to any more; this empties it of jobs the user
        has finished with, which until now had nowhere to go - Rejected and
        Ignore are honest about a row but leave it on the shortlist forever.

        A sheet cannot call anything, so this is polled once a pass, exactly like
        the draft tick: set the stage, and within one interval the row is gone.
        Nothing is written back to the stage cell - it already says Closed, and it
        goes on saying Closed in the tab it lands in, which is the point of
        moving the row rather than copying it.

        Returns how many rows moved.
        """
        urls = self._staged_urls(STAGE_CLOSED)
        if not urls:
            return 0
        # Not "delisted". That word is the crawler's finding that a posting has
        # left its board, and this row's posting may well still be up - what
        # ended was the user's interest in it. Filing a decision under a finding
        # would leave the tab unable to say which of the two happened.
        return self.move_to_closed(urls, status="closed")

    def _ticked_urls(self, column: str) -> set[str]:
        """Urls whose `column` tickbox is TRUE, across both job tabs."""
        import re

        index = HEADERS.index(column)
        url_col = HEADERS.index("url")
        out: set[str] = set()
        for ws in self._tabs():
            try:
                flags = ws.col_values(index + 1)[1:]
                formulas = ws.col_values(url_col + 1, value_render_option="FORMULA")[1:]
            except Exception as err:
                logger.warning("could not read %s on %s: %s", column, ws.title, err)
                continue
            for flag, cell in zip(flags, formulas):
                if str(flag).strip().upper() not in ("TRUE", "1", "YES"):
                    continue
                match = re.search(r'HYPERLINK\("([^"]+)"', str(cell))
                url = match.group(1) if match else str(cell).strip()
                if url:
                    out.add(url)
        return out

    def draft_link(self) -> str:
        """A formula linking to the Drafts tab, or "" if there is not one yet."""
        if self.draft_ws is None:
            try:
                self.draft_ws = self.spreadsheet.worksheet("Drafts")
            except Exception:
                return ""
        url = f"{self.spreadsheet.url}/edit#gid={self.draft_ws.id}"
        return f'=HYPERLINK("{url}","see Drafts")'

    def set_draft_status(self, statuses: dict) -> int:
        """Write {url: text} into the draft_status column. Returns cells written.

        One batch per tab, because this is called three times per drafted row -
        queued, drafting, done - and a request each would be most of the run.
        """
        if not statuses:
            return 0
        column = HEADERS.index("draft_status")
        letter = _a1(column)
        written = 0
        for ws in self._tabs():
            updates = [
                {"range": f"{letter}{row}", "values": [[statuses[url]]]}
                for url, row in self._url_rows(ws).items()
                if url in statuses
            ]
            if not updates:
                continue
            try:
                ws.batch_update(updates, value_input_option="USER_ENTERED")
                written += len(updates)
            except Exception as err:
                logger.warning("could not write draft status on %s: %s", ws.title, err)
        return written

    def reconcile_draft_status(self) -> int:
        """Make draft_status describe the sheet as it is now. Returns cells fixed.

        Every status was written by whichever pass happened to be running, on the
        assumption that the write would land and that nothing would change after
        it. Both assumptions fail: a pass killed between "queued" and the draft
        leaves a queue that does not exist, and a tick removed after the fact
        leaves a request nobody made. Three rows were wrong this way, one of them
        for a draft that had already been written.

        So the column is derived rather than accumulated. Cheap - it reads what it
        was going to read anyway - and it makes every earlier wrong value
        self-correcting instead of permanent.
        """
        import re

        drafted = self.drafted_urls()
        link = self.draft_link()
        draft_col = HEADERS.index("draft")
        status_col = HEADERS.index("draft_status")
        url_col = HEADERS.index("url")
        letter = _a1(status_col)
        # Written by a pass in flight. Anything else in the cell is a message to
        # the reader (a failure, a request for the posting) and is left alone
        # unless the request behind it is gone.
        TRANSIENT = ("queued", "drafting...")
        fixed = 0

        for ws in self._tabs():
            try:
                rows = ws.get_all_values(value_render_option="FORMULA")
            except Exception as err:
                logger.warning("could not read %s to reconcile: %s", ws.title, err)
                continue

            updates = []
            for number, row in enumerate(rows[1:], start=2):
                row = list(row) + [""] * (len(HEADERS) - len(row))
                ticked = str(row[draft_col]).strip().upper() in ("TRUE", "1", "YES")
                status = str(row[status_col]).strip()
                match = re.search(r'HYPERLINK\("([^"]+)"', str(row[url_col]))
                url = match.group(1) if match else str(row[url_col]).strip()
                if not url:
                    continue

                if url in drafted:
                    # Done, whatever the cell currently claims.
                    wanted = link or "done"
                elif not ticked and (status in TRANSIENT or status.startswith("failed:")):
                    # The request was withdrawn. A status for it is noise.
                    wanted = ""
                else:
                    continue

                if status != wanted:
                    updates.append({"range": f"{letter}{number}", "values": [[wanted]]})

            if updates:
                try:
                    ws.batch_update(updates, value_input_option="USER_ENTERED")
                    fixed += len(updates)
                except Exception as err:
                    logger.warning("could not reconcile %s: %s", ws.title, err)
        return fixed

    def drafts_requested(self) -> set[str]:
        """Urls with the draft box ticked. Each one costs a model call, so the
        caller must also subtract drafted_urls() before spending anything."""
        return self._ticked_urls("draft")

    def drafted_urls(self) -> set[str]:
        """Urls already in the Drafts tab. The tick stays ticked after a draft -
        unticking it would be the program editing the user's own column - so this
        is what stops it drafting the same job every five minutes, forever."""
        import re

        try:
            ws = self.spreadsheet.worksheet("Drafts")
        except Exception:
            return set()
        index = DRAFT_HEADERS.index("url")
        try:
            cells = ws.col_values(index + 1, value_render_option="FORMULA")[1:]
        except Exception:
            return set()
        out: set[str] = set()
        for cell in cells:
            match = re.search(r'HYPERLINK\("([^"]+)"', str(cell))
            url = match.group(1) if match else str(cell).strip()
            if url:
                out.add(url)
        return out

    def _existing_closed_tab(self):
        """The Closed tab if the sheet already has one. Never creates it, unlike
        `_closed_tab`: a caller that only wants to READ that tab must not be the
        reason an empty one appears."""
        if not self.closed_title:
            return None
        if self.closed_ws is None:
            try:
                self.closed_ws = self.spreadsheet.worksheet(self.closed_title)
            except Exception:
                return None
        return self.closed_ws

    def existing_urls(self) -> set[str]:
        """Every url already in either job tab, or in the Closed one.

        Backfill reads the whole database, so without this a second run writes
        the whole thing again underneath the first. Matching on url rather than
        row count is what makes it re-runnable after the score split moved rows
        or after the threshold changed.

        The Closed tab counts because a row in it is still in the database: left
        out, one backfill puts back onto the shortlist exactly the jobs that were
        taken off it, delisted and hand-filed alike. Its url column is located
        from its own header row rather than from HEADERS, because this reads that
        tab without migrating it and an older one may still be a column short.
        """
        import re

        tabs = [(ws, HEADERS.index("url")) for ws in self._tabs()]
        closed = self._existing_closed_tab()
        if closed is not None:
            try:
                tabs.append((closed, closed.row_values(1).index("url")))
            except Exception as err:
                logger.warning("could not find the url column on %s: %s",
                               closed.title, err)
        out: set[str] = set()
        for ws, col in tabs:
            try:
                cells = ws.col_values(col + 1, value_render_option="FORMULA")[1:]
            except Exception as err:
                logger.warning("could not read url column on %s: %s", ws.title, err)
                continue
            for cell in cells:
                match = re.search(r'HYPERLINK\("([^"]+)"', str(cell))
                url = match.group(1) if match else str(cell).strip()
                if url:
                    out.add(url)
        return out

    @staticmethod
    def _next_row(ws, headers: list | None = None) -> int:
        """The first row with no job in it, found from the data rather than asked for.

        Not append_row/append_rows. Those ask the API to find the end of the
        table, and the tick-box validation in _setup makes every cell in column A
        exist as far as that search is concerned — so it reports the bottom of
        the *grid*, and rows land at 1001 under a thousand blank ones. The sheet
        then looks empty and the data is real but unreachable, which is the worst
        of both. job_title is read instead because every row has one.
        """
        headers = headers or HEADERS
        col = ws.col_values(headers.index("job_title") + 1)
        while col and not col[-1].strip():
            col.pop()
        return max(len(col), 1) + 1

    def _write(self, ws, rows: list[list[str]], headers: list | None = None) -> None:
        """Put rows at an explicit range, growing the grid first if it is short."""
        if not rows:
            return
        start = self._next_row(ws, headers)
        need = start + len(rows) - 1
        grid = ws._properties["gridProperties"]["rowCount"]
        if need > grid:
            ws.add_rows(need - grid)
        ws.update(rows, f"A{start}", value_input_option="USER_ENTERED")

    def append_draft(self, job: dict, result: dict) -> str:
        """Log one drafted application. Returns the tab name written to.

        Created lazily: a sheet belonging to someone who has never run --draft
        should not carry an empty tab explaining that they have not.
        """
        from datetime import datetime

        if self.draft_ws is None:
            self.draft_ws = self._tab("Drafts", DRAFT_HEADERS, tickbox=False, clip=True)

        requirements = result.get("requirements") or []
        gaps = [r.get("requirement", "") for r in requirements
                if (r.get("verdict") or "").lower() == "gap"]
        salary = ""
        questions = []
        for question in result.get("questions") or []:
            text = (question.get("question") or "").strip()
            answer = (question.get("answer") or "").strip() or "[BLANK - answer this yourself]"
            questions.append(f"Q: {text}\nA: {answer}")
            if "salary" in text.lower() and not salary:
                salary = answer

        row = {
            "drafted_at": datetime.now().isoformat(timespec="seconds"),
            "score": job.get("score", ""),
            "job_title": job.get("job_title") or job.get("title") or "",
            "company": job.get("company") or "",
            "url": hyperlink(job.get("url") or ""),
            "apply_via": result.get("apply_method") or "",
            "salary_answer": salary,
            # Numbered so the count is visible without reading them.
            "gaps": "\n".join(f"{i}. {g}" for i, g in enumerate(gaps, 1)),
            "cover_letter": result.get("cover_letter") or "",
            "tailored_resume": result.get("tailored_resume") or "",
            "questions": "\n\n".join(questions),
        }
        self._write(self.draft_ws, [[str(row.get(h, "")) for h in DRAFT_HEADERS]],
                    DRAFT_HEADERS)
        return self.draft_ws.title

    def append_many(self, records: list[dict]) -> int:
        """Append many rows, one call per tab. Backfill sends hundreds, and one
        write each would be hundreds of round trips and a rate limit."""
        batches: dict[int, tuple] = {}
        for record in records:
            ws = self._target(record)
            batches.setdefault(id(ws), (ws, []))[1].append(
                [_cell(h, record) for h in HEADERS])

        total = 0
        for ws, rows in batches.values():
            self._write(ws, rows)
            total += len(rows)
        return total

    def append(self, record: dict) -> None:
        # Sheets links a bare url on its own, but shows the whole address; the
        # formula gives the same click behind a narrow "open" cell instead.
        self._write(self._target(record), [[_cell(h, record) for h in HEADERS]])
