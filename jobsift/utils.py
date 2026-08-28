"""Small shared helpers."""

from __future__ import annotations

import json
import re

from bs4 import BeautifulSoup

_URL_RE = re.compile(r"https?://\S+")
_TOKEN_RE = re.compile(r"\[?URL(\d+)\]?", re.IGNORECASE)

# Below this many characters of posting text, what we hold is an alert snippet
# rather than a posting. Boards mail a teaser and keep the rest behind a click,
# and some of those domains are deliberately never fetched (`skip_link_domains`),
# so this is the normal case rather than an error.
#
# It matters because a snippet cannot support a judgement. One Indeed listing
# carried 141 characters - "It requires research, planning, design, execution,
# testing, communication, problem-solving, and the determination to finish what
# was started" - and was scored 63/100 against a resume, over the alert
# threshold. Nothing in the number said it was a guess.
#
# Shared, so the scorer and the drafter agree on what "enough to judge" means.
SNIPPET_CHARS = 400


def evidence_chars(job: dict) -> int:
    """How much posting text is actually available to judge this job on.

    Counts only the fields describing the WORK. Title, company and salary are
    always present and say nothing about whether the role fits.
    """
    parts = [
        str(job.get("description_summary") or job.get("description") or ""),
        " ".join(str(item) for item in (job.get("requirements") or [])),
        " ".join(str(item) for item in (job.get("skills_required") or [])),
        " ".join(str(item) for item in (job.get("responsibilities") or [])),
    ]
    return len(" ".join(part for part in parts if part.strip()).strip())

# Characters that routinely sit right after a URL in prose or in bracketed link
# markup but are not part of it. \S+ above is greedy, so "[https://x/y]" would
# otherwise capture the closing bracket and produce a URL that 400s.
_URL_TRAILING = "]),.;:!?'\"<>"


def _split_trailing(url: str) -> tuple[str, str]:
    """Split a captured URL into (url, trailing punctuation)."""
    cut = len(url)
    while cut > 0 and url[cut - 1] in _URL_TRAILING:
        cut -= 1
    return url[:cut], url[cut:]


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
        url, trailing = _split_trailing(match.group(0))
        if not url:
            return match.group(0)
        urls.append(url)
        # Re-emit the trailing punctuation so the surrounding text is unchanged.
        return f"[URL{len(urls)}]{trailing}"

    return _URL_RE.sub(swap, text), urls


def unmask_url(token: str, urls: list[str]) -> str:
    """Resolve a [URL7] token back to its URL; pass anything else through."""
    token = (token or "").strip()
    if not token:
        return ""
    match = _TOKEN_RE.fullmatch(token)
    if not match:
        # Model returned a real URL (or junk) rather than a token. Strip the same
        # trailing punctuation so a copied-out link is still usable.
        if token.startswith(("http://", "https://")):
            return _split_trailing(token)[0]
        return token
    index = int(match.group(1)) - 1
    return urls[index] if 0 <= index < len(urls) else ""


# Params that only identify the referral, never the job. Anything not listed is
# kept, because some boards put the job id in the query string (Indeed's "jk").
_TRACKING_PARAMS = {
    "token", "tracking", "src", "source", "ref", "referrer", "campaign", "cid",
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "gclid", "fbclid", "mc_cid", "mc_eid", "sid", "trk", "trkcampaign", "from",
    # Indeed alert links carry these; "jk" is the job id and is deliberately kept.
    "qd", "rd", "tk", "alid", "bb", "tmtk", "xkcb", "xpse", "vjs", "acatk", "jsa",
}


def strip_tracking_params(url: str) -> str:
    """Drop referral cruft so a resolved link is short enough to show as text."""
    if not url or "?" not in url:
        return url
    base, _, query = url.partition("?")
    kept = [
        pair
        for pair in query.split("&")
        if pair and pair.split("=", 1)[0].lower() not in _TRACKING_PARAMS
    ]
    return f"{base}?{'&'.join(kept)}" if kept else base


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
