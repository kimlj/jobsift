"""Optional output — Telegram ping for high-scoring jobs."""

from __future__ import annotations

import html
import logging

import httpx

logger = logging.getLogger(__name__)


def _esc(value) -> str:
    return html.escape(str(value or "").strip())


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
        # Always a bare URL, never <a href>. Telegram prompts "Open link?" before
        # following a link whose anchor text hides the destination, so an anchor
        # costs a tap on every single alert. enrich already resolves tracking
        # redirects and strips referral params, so these are short anyway.
        lines.append("")
        lines.append(_esc(url))

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
