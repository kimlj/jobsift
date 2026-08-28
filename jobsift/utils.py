"""Small shared helpers."""

from __future__ import annotations

import json
import re

from bs4 import BeautifulSoup

_URL_RE = re.compile(r"https?://\S+")
_TOKEN_RE = re.compile(r"\[?URL(\d+)\]?", re.IGNORECASE)


def html_to_text(html: str, limit: int | None = None) -> str:
    """Strip HTML to readable text."""
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    # get_text() keeps link text but drops the href, which loses the job URL
    # entirely in HTML-only emails. Inline each href so it survives as text.
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        if href.startswith(("http://", "https://")):
            anchor.append(f" {href} ")
    text = soup.get_text(separator=" ", strip=True)
    text = " ".join(text.split())
    return text[:limit] if limit else text


def mask_urls(text: str) -> tuple[str, list[str]]:
    """Replace every URL with a short [URL1]-style token.

    Job-alert emails are mostly tracking parameters — a single Indeed link runs
    ~400 chars. Masking them keeps the model from spending its output budget
    transcribing (and potentially mangling) URLs it can't improve on.
    Returns the masked text plus the URLs, indexed from 1 to match the tokens.
    """
    urls: list[str] = []

    def swap(match: re.Match) -> str:
        urls.append(match.group(0))
        return f"[URL{len(urls)}]"

    return _URL_RE.sub(swap, text), urls


def unmask_url(token: str, urls: list[str]) -> str:
    """Resolve a [URL7] token back to its URL; pass anything else through."""
    token = (token or "").strip()
    if not token:
        return ""
    match = _TOKEN_RE.fullmatch(token)
    if not match:
        return token  # model returned a real URL (or junk) — leave it be
    index = int(match.group(1)) - 1
    return urls[index] if 0 <= index < len(urls) else ""


def parse_json_object(content: str) -> dict:
    """Parse a JSON object from an LLM response, tolerating ```json fences."""
    if not content:
        return {}
    cleaned = content.strip()
    if cleaned.startswith("```"):
        # drop the first and last fence lines
        cleaned = cleaned.split("```", 2)[1] if "```" in cleaned else cleaned
        if cleaned.lstrip().startswith("json"):
            cleaned = cleaned.lstrip()[4:]
        cleaned = cleaned.strip().rstrip("`").strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # last resort: find the outermost braces
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(cleaned[start : end + 1])
            except json.JSONDecodeError:
                return {}
        return {}
