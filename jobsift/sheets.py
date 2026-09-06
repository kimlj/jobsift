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
    "job_title", "url", "source", "company", "salary", "location", "remote",
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


def _cell(header: str, record: dict) -> str:
    """One cell. `applied` is the user's tickbox, so it goes out as FALSE rather
    than empty — an empty cell under tickbox validation reads as blank, a FALSE
    reads as unticked. `url` gets the narrow clickable form; see utils.hyperlink."""
    if header in ("applied", "draft"):
        return "FALSE"
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
        if current != headers:
            # A tab written before a column existed holds its data one column to
            # the left of where the new headers say it is. Rewriting row 1 alone
            # would silently relabel every value in it, so make room first.
            added = [h for h in headers if h not in current]
            kept = [h for h in headers if h not in added]
            if current and added and kept == current:
                # Ascending order of the FINAL index: each insert shifts what is
                # to its right, so a later column's index is already correct by
                # the time its turn comes.
                self.spreadsheet.batch_update({"requests": [
                    {"insertDimension": {
                        "range": {"sheetId": ws.id, "dimension": "COLUMNS",
                                  "startIndex": headers.index(name),
                                  "endIndex": headers.index(name) + 1},
                        "inheritFromBefore": False}}
                    for name in sorted(added, key=headers.index)
                ]})
            elif current and kept != current:
                # Columns were removed or reordered, not just added. Writing the
                # new header row over this would relabel every value under it,
                # and a wrong label on real data is worse than a stale one.
                logger.warning(
                    "%s has headers this version cannot migrate (%s); leaving row 1 alone",
                    ws.title, current)
                return ws
            ws.update([headers], "A1", value_input_option="RAW")

        self._setup(ws, headers, tickbox, clip)
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

    def _setup(self, ws, headers: list | None = None, tickbox: bool = True,
               clip: bool = False) -> None:
        """Make a tab usable without anyone opening a menu.

        Tick boxes on the applied column, a frozen header row and applied
        column, and a filter on row 1 so it can be sorted by score. All of it
        is idempotent — re-applying the same validation and freeze is a no-op,
        and a basic filter that already exists is left alone.

        Validation runs to row 5000 rather than the current last row, so rows
        appended by later runs land inside it and get a tick box too.
        """
        headers = headers or HEADERS
        url_col = headers.index("url")
        requests = []
        if tickbox:
            for name in ("applied", "draft"):
                column = headers.index(name)
                requests.append({"setDataValidation": {
                    "range": {"sheetId": ws.id, "startRowIndex": 1,
                              "endRowIndex": 5000,
                              "startColumnIndex": column,
                              "endColumnIndex": column + 1},
                    "rule": {"condition": {"type": "BOOLEAN"}, "showCustomUi": True}}})
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
                                                  "frozenColumnCount": 2 if tickbox else 0}},
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
        """Tick the applied box on every row whose url is in `urls`.

        Only ever writes TRUE. An unticked row is not evidence of anything —
        the boards confirm applications, they do not confirm the absence of one —
        so a row the user ticked by hand is never cleared by this.
        """
        wanted = set(urls)
        if not wanted:
            return 0
        applied_col = HEADERS.index("applied")
        letter = chr(ord("A") + applied_col)
        ticked = 0
        for ws in self._tabs():
            rows = [row for url, row in self._url_rows(ws).items() if url in wanted]
            if not rows:
                continue
            try:
                ws.batch_update([
                    {"range": f"{letter}{row}", "values": [["TRUE"]]} for row in sorted(rows)
                ], value_input_option="USER_ENTERED")
                ticked += len(rows)
            except Exception as err:
                logger.warning("could not tick applied on %s: %s", ws.title, err)
        return ticked

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
        letter = chr(ord("A") + column)
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
        letter = chr(ord("A") + status_col)
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
