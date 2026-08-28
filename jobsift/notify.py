"""Optional output — Telegram ping for high-scoring jobs."""

from __future__ import annotations

import html
import logging
import re

import httpx

logger = logging.getLogger(__name__)

# Limits chosen from the real distribution of these fields, so the common case is
# never truncated: description tops out near 161 chars, matching/missing skills
# near 257, and reasoning has a median of ~606 with a p90 of ~765.
MAX_DESCRIPTION = 400
MAX_SKILLS = 280
MAX_REASONING = 900

_SENTENCE_END = re.compile(r"[.!?](?=\s|$)")


def _esc(value) -> str:
    return html.escape(str(value or "").strip())


def _clip(text: str, limit: int) -> str:
    """Truncate on a sentence boundary, falling back to a word boundary.

    Cutting mid-sentence reads as a bug rather than a summary, and the ellipsis
    is only added when something was actually removed.
    """
    text = (text or "").strip()
    if len(text) <= limit:
        return text

    window = text[:limit]
    ends = [m.end() for m in _SENTENCE_END.finditer(window)]
    # Only honour a sentence break if it keeps a useful amount of the text.
    if ends and ends[-1] >= limit * 0.6:
        return window[: ends[-1]].rstrip()

    cut = window.rsplit(" ", 1)[0].rstrip(" ,;:")
    return f"{cut}…"


def _has(value: str) -> bool:
    return bool(value) and value != "N/A"


def send_telegram(bot_token: str, chat_id: str, record: dict) -> None:
    url = str(record.get("url") or "").strip()

    lines = [
        f"<b>{_esc(record.get('score'))}/100</b> · {_esc(record.get('job_title'))}",
        f"🏢 {_esc(record.get('company'))}",
        f"💰 {_esc(record.get('salary'))}",
        "",
        f"<i>Skills {_esc(record.get('skill_match'))} · "
        f"Exp {_esc(record.get('experience_fit'))} · "
        f"Pay {_esc(record.get('interest_fit'))}</i>",
    ]

    matching = _esc(record.get("matching_skills"))
    if _has(matching):
        lines.append(f"✅ {_clip(matching, MAX_SKILLS)}")

    missing = _esc(record.get("missing_skills"))
    if _has(missing):
        lines.append(f"❌ {_clip(missing, MAX_SKILLS)}")

    summary = _esc(record.get("description_summary"))
    if _has(summary):
        lines.append("")
        lines.append(f"📝 {_clip(summary, MAX_DESCRIPTION)}")

    why = _esc(record.get("reasoning"))
    if _has(why):
        lines.append(f"🧠 <i>{_clip(why, MAX_REASONING)}</i>")

    if url.startswith("http"):
        # Always a bare URL, never <a href>. Telegram prompts "Open link?" before
        # following a link whose anchor text hides the destination, so an anchor
        # costs a tap on every single alert. Tracking params are already stripped
        # upstream, so these are short.
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
