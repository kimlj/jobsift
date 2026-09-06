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

# ── Dedup ───────────────────────────────────────────────────────────────────
#
# Jobs are deduped on title::company, which fails the moment one board writes the
# employer's name differently from another — or from itself. "WeSupport
# Incorporated" and "WeSupport, Inc." are one company advertising one job, and the
# exact-match key saved it twice in a single Jobstreet run.
#
# The asymmetry that decides how hard to normalise: a missed duplicate costs one
# extra line in Telegram, while a wrong merge silently loses a real job and nothing
# reports it. So the company side is normalised hard, because legal suffixes carry
# no identity, and the title side only lightly, because two genuinely different
# roles at one employer often differ by punctuation alone.

# Dropped only from the END of a name, repeatedly: "Foo Systems Pte Ltd" -> "foo
# systems". Words that look corporate but carry identity are deliberately absent —
# "group", "global", "holdings", "solutions", "technologies", "services" all
# distinguish real companies from each other.
_LEGAL_SUFFIXES = {
    "inc", "incorporated", "corp", "corporation", "company", "co", "ltd",
    "limited", "llc", "llp", "lp", "plc", "pte", "pty", "bhd", "sdn", "gmbh",
    "ag", "bv", "nv", "sa", "sas", "srl", "spa", "oy", "ab", "kk", "kft", "sro",
    "dmcc", "fzco", "fze", "ou", "aps",
    # A PH job board is full of "<company> Philippines" arms of one employer.
    "philippines", "philippine", "phils", "ph",
}

# Left dangling once a suffix goes: "Accenture in the Philippines" would otherwise
# key as "accenture in the".
_TRAILING_FILLER = {"in", "the", "of", "and", "at", "for"}

_PARENTHETICAL_RE = re.compile(r"\([^)]*\)")
_NON_WORD_RE = re.compile(r"[^\w\s]+", re.UNICODE)


def normalize_company(name: str) -> str:
    """An employer name reduced to the part that identifies it.

    "WeSupport, Inc." and "WeSupport Incorporated" both become "wesupport";
    "REALPAGE (PHILIPPINES) INC." becomes "realpage".
    """
    text = (name or "").lower()
    # "(Philippines)", "(Clark)", "(formerly X)" — a parenthetical in an employer
    # name is a qualifier on the same company, never a different one.
    text = _PARENTHETICAL_RE.sub(" ", text)
    text = _NON_WORD_RE.sub(" ", text)
    words = text.split()
    while words and words[-1] in (_LEGAL_SUFFIXES | _TRAILING_FILLER):
        words.pop()
    return " ".join(words)


def normalize_title(title: str) -> str:
    """A job title with punctuation and spacing flattened, and nothing else.

    Deliberately light. Boards decorate titles with the same information they also
    put in structured fields — "(Hybrid)", "| WFH", "- Urgent" — and stripping
    those would merge "Senior AI Engineer - Hybrid" into "Senior AI Engineer" at
    the same employer, which may well be two different openings.
    """
    return " ".join(_NON_WORD_RE.sub(" ", (title or "").lower()).split())


def job_key(job: dict) -> str:
    """The dedup identity of a listing: normalised title::company.

    Lives here rather than in pipeline.py so `store` can use it to migrate keys
    written in the older exact-match format without importing the pipeline.
    """
    company = normalize_company(job.get("company"))
    if not company:
        # Some sources (onlinejobs.ph listings) never name the employer. Falling
        # back to the URL slug keeps two different postings that share a title
        # from collapsing into one entry.
        url = str(job.get("url") or "").strip().rstrip("/")
        company = normalize_company(url.rsplit("/", 1)[-1][:80]) if url else ""
    return f"{normalize_title(job.get('title'))}::{company}"


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

def hyperlink(url: str, label: str = "open") -> str:
    """A narrow clickable cell for Excel or Sheets, instead of a long address.

    These links run long - an onlinejobs.ph slug repeats the whole job title, and
    Indeed's cts.indeed.com redirects encode the destination in the path, so some
    exceed a thousand characters. A column wide enough to read one pushes
    everything else off screen, on surfaces whose whole purpose is to be scanned
    and clicked down.

    The label is fixed, not the job title. A cell beginning with `=` is a formula
    and both applications execute what is in it, and these titles arrive from the
    open internet. The url still has to be interpolated, so its quotes are doubled
    (the escape both use) and anything not plainly http(s) is returned as inert
    text instead.

    Shared by the CSV export and the Sheets writer so the two cannot drift, which
    they already did once over column order.
    """
    url = (url or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        return url
    return '=HYPERLINK("' + url.replace('"', '""') + '","' + label + '")'
