"""Optional output — Telegram ping for high-scoring jobs."""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)


def send_telegram(bot_token: str, chat_id: str, record: dict) -> None:
    text = (
        f"Score: {record.get('score')}/100\n\n"
        f"💼 {record.get('job_title')}\n"
        f"🏢 {record.get('company')}\n"
        f"💰 {record.get('salary')}\n\n"
        f"Skills: {record.get('skill_match')} | "
        f"Exp: {record.get('experience_fit')} | "
        f"Salary: {record.get('interest_fit')}\n"
        f"✅ {record.get('matching_skills')}\n\n"
        f"{record.get('url')}"
    )
    try:
        resp = httpx.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            data={"chat_id": chat_id, "text": text, "disable_web_page_preview": False},
            timeout=15,
        )
        resp.raise_for_status()
    except Exception:
        logger.exception("Telegram send failed")
