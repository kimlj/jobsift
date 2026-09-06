"""Optional output — append every scored job to a Google Sheet for browsing."""

from __future__ import annotations

import logging

from .utils import hyperlink

logger = logging.getLogger(__name__)

# Column order for the sheet (also the header row).
HEADERS = [
    # Same leading order as the CSV export: the columns you decide on, first.
    # Both outputs answer "is this worth opening" and they had drifted apart —
    # score sat at column N here while the export put it third.
    #
    # url is beside company because the sheet is scanned and clicked down. Sheets
    # auto-links a bare URL under USER_ENTERED, so unlike the CSV this needs no
    # HYPERLINK formula.
    # "applied" is yours, not the program's. It is written FALSE on every new row
    # and never read back, so it can be turned into a tickbox column and frozen at
    # column A. It exists because append_row writes from column A outward: a
    # checkbox you added there yourself would be overwritten by the next row.
    "applied",
    "score", "job_title", "url", "company", "salary", "location", "remote",
    "source", "timestamp", "job_type", "experience_level", "duration",
    "skills_required", "description_summary", "skill_match", "experience_fit",
    "interest_fit", "matching_skills", "missing_skills", "reasoning", "status",
    "reported_at",
]


def _cell(header: str, record: dict) -> str:
    """One cell. `applied` is the user's tickbox, so it goes out as FALSE rather
    than empty — an empty cell under tickbox validation reads as blank, a FALSE
    reads as unticked. `url` gets the narrow clickable form; see utils.hyperlink."""
    if header == "applied":
        return "FALSE"
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

    def _tab(self, title: str):
        """Fetch or create one tab, header it, and make it usable."""
        import gspread

        try:
            ws = self.spreadsheet.worksheet(title)
        except gspread.WorksheetNotFound:
            ws = self.spreadsheet.add_worksheet(
                title=title, rows=1000, cols=len(HEADERS)
            )

        if not ws.get_all_values():
            ws.append_row(HEADERS, value_input_option="RAW")

        self._setup(ws)
        return ws

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

    def _setup(self, ws) -> None:
        """Make a tab usable without anyone opening a menu.

        Tick boxes on the applied column, a frozen header row and applied
        column, and a filter on row 1 so it can be sorted by score. All of it
        is idempotent — re-applying the same validation and freeze is a no-op,
        and a basic filter that already exists is left alone.

        Validation runs to row 5000 rather than the current last row, so rows
        appended by later runs land inside it and get a tick box too.
        """
        applied_col = HEADERS.index("applied")
        url_col = HEADERS.index("url")
        requests = [
            {"setDataValidation": {
                "range": {"sheetId": ws.id, "startRowIndex": 1,
                          "endRowIndex": 5000,
                          "startColumnIndex": applied_col,
                          "endColumnIndex": applied_col + 1},
                "rule": {"condition": {"type": "BOOLEAN"}, "showCustomUi": True}}},
            {"updateSheetProperties": {
                "properties": {"sheetId": ws.id,
                               "gridProperties": {"frozenRowCount": 1,
                                                  "frozenColumnCount": 1}},
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
                                     "endColumnIndex": len(HEADERS)}}}}]})
        except Exception:
            pass

    def _tabs(self) -> list:
        return [w for w in (self.ws, self.ws_below) if w is not None]

    def applied_urls(self) -> set[str]:
        """The urls of jobs ticked as applied, across both tabs.

        Both, because a job worth applying to may well be one the scorer put
        below the bar — that is the case the second tab exists for.

        Matched on url because it is the only field a person will not retype.
        The cell holds a HYPERLINK formula, so the url is read from the
        formula rather than the rendered value, which is just the word open.
        """
        import re

        applied_col = HEADERS.index("applied")
        url_col = HEADERS.index("url")
        out: set[str] = set()

        for ws in self._tabs():
            try:
                flags = ws.col_values(applied_col + 1)[1:]
                formulas = ws.col_values(
                    url_col + 1, value_render_option="FORMULA")[1:]
            except Exception as err:
                logger.warning("could not read applied column on %s: %s", ws.title, err)
                continue

            for flag, cell in zip(flags, formulas):
                if str(flag).strip().upper() not in ("TRUE", "1", "YES"):
                    continue
                match = re.search(r'HYPERLINK\("([^"]+)"', str(cell))
                url = match.group(1) if match else str(cell).strip()
                if url:
                    out.add(url)
        return out

    def existing_urls(self) -> set[str]:
        """Every url already in either tab.

        Backfill reads the whole database, so without this a second run writes
        the whole thing again underneath the first. Matching on url rather than
        row count is what makes it re-runnable after the score split moved rows
        or after the threshold changed.
        """
        import re

        url_col = HEADERS.index("url")
        out: set[str] = set()
        for ws in self._tabs():
            try:
                cells = ws.col_values(url_col + 1, value_render_option="FORMULA")[1:]
            except Exception as err:
                logger.warning("could not read url column on %s: %s", ws.title, err)
                continue
            for cell in cells:
                match = re.search(r'HYPERLINK\("([^"]+)"', str(cell))
                url = match.group(1) if match else str(cell).strip()
                if url:
                    out.add(url)
        return out

    def append_many(self, records: list[dict]) -> int:
        """Append many rows, one call per tab. Backfill sends hundreds, and an
        append_row each would be hundreds of round trips and a rate limit."""
        batches: dict[int, tuple] = {}
        for record in records:
            ws = self._target(record)
            batches.setdefault(id(ws), (ws, []))[1].append(
                [_cell(h, record) for h in HEADERS])

        total = 0
        for ws, rows in batches.values():
            ws.append_rows(rows, value_input_option="USER_ENTERED")
            total += len(rows)
        return total

    def append(self, record: dict) -> None:
        # Sheets links a bare url on its own, but shows the whole address; the
        # formula gives the same click behind a narrow "open" cell instead.
        row = [_cell(h, record) for h in HEADERS]
        self._target(record).append_row(row, value_input_option="USER_ENTERED")
