"""Optional output — append every scored job to a Google Sheet for browsing."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Column order for the sheet (also the header row).
HEADERS = [
    "timestamp", "source", "job_title", "company", "location", "remote", "salary",
    "skills_required", "job_type", "experience_level", "duration", "url",
    "description_summary", "score", "skill_match", "experience_fit", "interest_fit",
    "matching_skills", "missing_skills", "reasoning", "status", "reported_at",
]


class SheetWriter:
    """Wraps a gspread worksheet. Created only when google_sheet.enabled is true."""

    def __init__(self, gs_config):
        import gspread
        from google.oauth2.service_account import Credentials

        creds = Credentials.from_service_account_file(
            gs_config.service_account_file,
            scopes=["https://www.googleapis.com/auth/spreadsheets"],
        )
        client = gspread.authorize(creds)
        spreadsheet = client.open_by_key(gs_config.sheet_id)

        try:
            self.ws = spreadsheet.worksheet(gs_config.worksheet)
        except gspread.WorksheetNotFound:
            self.ws = spreadsheet.add_worksheet(
                title=gs_config.worksheet, rows=1000, cols=len(HEADERS)
            )
            self.ws.append_row(HEADERS, value_input_option="RAW")

        # Ensure a header row exists on a fresh/empty sheet.
        if not self.ws.get_all_values():
            self.ws.append_row(HEADERS, value_input_option="RAW")

    def append(self, record: dict) -> None:
        row = [str(record.get(h, "")) for h in HEADERS]
        self.ws.append_row(row, value_input_option="USER_ENTERED")
