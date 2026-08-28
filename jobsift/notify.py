"""Optional output — Telegram ping for high-scoring jobs."""

from __future__ import annotations

import html
import logging

import httpx

logger = logging.getLogger(__name__)


def _esc(value) -> str:
    return html.escape(str(value or "").strip())


def _short_source(url: str) -> str:
    """A readable label for the link instead of a 300-character tracking URL."""
    host = url.split("//", 1)[-1].split("/", 1)[0].lower()
    host = host[4:] if host.startswith("www.") else host
    return host or "listing"


def send_telegram(bot_token: str, chat_id: str, record: dict) -> None:
    url = str(record.get("url") or "").strip()
    skills = _esc(record.get("matching_skills"))

    lines = [
        f"<b>{_esc(record.get('score'))}/100</b> · {_esc(record.get('job_title'))}",
        f"🏢 {_esc(record.get('company'))}",
        f"💰 {_esc(record.get('salary'))}",
        "",
        f"<i>Skills {_esc(record.get('skill_match'))} · "
        f"Exp {_esc(record.get('experience_fit'))} · "
        f"Pay {_esc(record.get('interest_fit'))}</i>",
    ]
    if skills and skills != "N/A":
        lines.append(f"✅ {skills[:180]}")
    if url.startswith("http"):
        # Anchor text keeps the message short — Telegram renders the label, not
        # the URL, and these boards use 300-char tracking links.
        lines.append(f"\n<a href=\"{_esc(url)}\">Open on {_esc(_short_source(url))} →</a>")

    try:
        resp = httpx.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            data={
                "chat_id": chat_id,
                "text": "\n".join(lines),
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=15,
        )
        resp.raise_for_status()
    except Exception:
        logger.exception("Telegram send failed")
