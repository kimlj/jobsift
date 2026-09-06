"""The USD to PHP rate, fetched rather than assumed.

`filters.USD_TO_PHP` was a constant, and a wrong one costs more than it looks:
it is the multiplier on every dollar-quoted listing, so it moves what passes
`min_salary_php`, what the scorer sees, and the order of the whole sheet. A rate
typed once and left is quietly wrong by a few percent within months.

Three rules shape this file.

**filters.py stays pure.** It is the one module with no network and no I/O, which
is what lets two of the dry-run scripts run for free and makes the parser
testable at all. So the fetch lives here and is applied at startup, rather than
being called from inside the parser.

**A failure is never fatal.** The rate falls back to the last good value on disk,
then to whatever config says, then to the built-in. A currency API being down is
not a reason to stop reading the inbox, and a slightly stale rate is a far
smaller error than no run.

**Once a day is enough.** FX moves in fractions of a percent daily, and every
listing this touches is quoted in round numbers to begin with. Refreshing per run
would be a request every five minutes to learn nothing.
"""

from __future__ import annotations

import json
import logging
import os
import time

logger = logging.getLogger(__name__)

# Two providers, both free and keyless. Frankfurter reads the ECB's published
# reference rates; open.er-api is the fallback for when it is down or drops PHP.
PROVIDERS = (
    ("frankfurter", "https://api.frankfurter.app/latest?from=USD&to=PHP",
     lambda d: (d.get("rates") or {}).get("PHP"), lambda d: d.get("date", "")),
    ("open.er-api", "https://open.er-api.com/v6/latest/USD",
     lambda d: (d.get("rates") or {}).get("PHP"), lambda d: d.get("time_last_update_utc", "")),
)

MAX_AGE_SECONDS = 24 * 3600

# A rate outside this band is not a rate. USD/PHP has spent the last decade
# between roughly 45 and 65; anything else means the endpoint changed shape and
# handed back a different number, and multiplying every salary by it would be
# worse than using yesterday's.
PLAUSIBLE = (35.0, 90.0)


def _read_cache(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _write_cache(path: str, payload: dict) -> None:
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
    except Exception as exc:
        # A cache that cannot be written just means fetching again tomorrow.
        logger.info("fx: could not write %s (%s)", path, exc)


def usd_to_php(fallback: float, cache_path: str = "./data/fx.json",
               max_age_seconds: int = MAX_AGE_SECONDS) -> tuple[float, str]:
    """Return (rate, where it came from). Never raises.

    The note is returned rather than logged here so the caller can put it in the
    startup line: a figure that moves everything downstream should say how old it
    is, every run, without anyone going looking.
    """
    cached = _read_cache(cache_path)
    age = time.time() - float(cached.get("fetched_at") or 0)
    if cached.get("rate") and age < max_age_seconds:
        return float(cached["rate"]), f"cached {int(age / 3600)}h ago, {cached.get('date', '?')}"

    import httpx

    for name, url, pick_rate, pick_date in PROVIDERS:
        try:
            response = httpx.get(url, timeout=10,
                                 headers={"User-Agent": "jobsift/0.1 (+https://github.com/kimlj/jobsift)"})
            response.raise_for_status()
            payload = response.json()
            rate = float(pick_rate(payload) or 0)
            if not PLAUSIBLE[0] <= rate <= PLAUSIBLE[1]:
                logger.warning("fx: %s returned %s, outside %s-%s; ignoring",
                               name, rate, *PLAUSIBLE)
                continue
            date = str(pick_date(payload) or "")
            _write_cache(cache_path, {"rate": rate, "date": date,
                                      "source": name, "fetched_at": int(time.time())})
            return rate, f"live from {name}, {date}"
        except Exception as exc:
            logger.info("fx: %s unavailable (%s)", name, exc)

    if cached.get("rate"):
        # Stale beats absent: the last real rate is closer to today's than a
        # number somebody typed into a config file a year ago.
        return float(cached["rate"]), f"STALE cache, {int(age / 86400)}d old"
    return float(fallback), "fallback, no live rate available"
