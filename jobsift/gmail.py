"""Read job-alert emails from a Gmail inbox over IMAP (App Password auth)."""

from __future__ import annotations

import email
import imaplib
import logging
from datetime import datetime, timedelta
from email.header import decode_header
from email.utils import parseaddr

from .utils import html_to_text

logger = logging.getLogger(__name__)

IMAP_HOST = "imap.gmail.com"


class GmailReader:
    def __init__(self, address: str, app_password: str):
        self.address = address
        # App passwords are shown with spaces ("abcd efgh ..."); IMAP wants them removed.
        self.app_password = app_password.replace(" ", "")

    def fetch_recent(self, lookback_days: int = 2) -> list[dict]:
        """Return recent inbox messages as dicts: uid, from, subject, date, text."""
        messages: list[dict] = []
        conn = imaplib.IMAP4_SSL(IMAP_HOST)
        try:
            conn.login(self.address, self.app_password)
            conn.select("INBOX")
            since = (datetime.utcnow() - timedelta(days=lookback_days)).strftime("%d-%b-%Y")
            typ, data = conn.uid("search", None, f"(SINCE {since})")
            if typ != "OK" or not data or not data[0]:
                return messages
            uids = data[0].split()
            for uid in uids:
                typ, msg_data = conn.uid("fetch", uid, "(RFC822)")
                if typ != "OK" or not msg_data or not msg_data[0]:
                    continue
                raw = msg_data[0][1]
                msg = email.message_from_bytes(raw)
                messages.append(
                    {
                        "uid": uid.decode() if isinstance(uid, bytes) else str(uid),
                        "from": parseaddr(msg.get("From", ""))[1].lower(),
                        "subject": self._decode(msg.get("Subject", "")),
                        "date": msg.get("Date", ""),
                        "text": self._get_text(msg),
                    }
                )
        finally:
            try:
                conn.logout()
            except Exception:
                pass
        return messages

    @staticmethod
    def _decode(value: str) -> str:
        if not value:
            return ""
        parts = decode_header(value)
        out = []
        for text, enc in parts:
            if isinstance(text, bytes):
                out.append(text.decode(enc or "utf-8", errors="replace"))
            else:
                out.append(text)
        return "".join(out)

    @staticmethod
    def _get_text(msg) -> str:
        """Prefer text/plain; fall back to HTML converted to text."""
        plain, html = "", ""
        if msg.is_multipart():
            for part in msg.walk():
                ctype = part.get_content_type()
                if part.get("Content-Disposition", "").startswith("attachment"):
                    continue
                try:
                    payload = part.get_payload(decode=True)
                    if payload is None:
                        continue
                    charset = part.get_content_charset() or "utf-8"
                    decoded = payload.decode(charset, errors="replace")
                except Exception:
                    continue
                if ctype == "text/plain":
                    plain += decoded
                elif ctype == "text/html":
                    html += decoded
        else:
            try:
                payload = msg.get_payload(decode=True)
                charset = msg.get_content_charset() or "utf-8"
                decoded = payload.decode(charset, errors="replace") if payload else ""
            except Exception:
                decoded = ""
            if msg.get_content_type() == "text/html":
                html = decoded
            else:
                plain = decoded

        if plain.strip():
            return plain
        return html_to_text(html)
