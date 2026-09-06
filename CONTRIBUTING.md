# Contributing

This is a personal tool that turned out to be useful to other people. Patches are
welcome; so is a bug report that just says what you expected and what happened.

## Before you send a change

There is no test suite. There are eight `dryrun_*.py` scripts instead, and they
are the closest thing to one. Each answers a single question about behaviour and
prints what it found rather than asserting — the point is that you read the
output and judge it, because most of the interesting failures here are "this
scored 22 on a snippet" rather than "this raised an exception".

**Two run with no network and no API key.** Start here, and if your change
touches filtering or dedup, these are the ones that matter:

```bash
python dryrun_dedup.py       # normalised title::company dedup — does it over- or under-merge?
python dryrun_geography.py   # the location rules, in increasing order of surprise
```

**The other six need something.** They import a source adapter or the LLM layer,
so they will make real requests:

| script | needs |
|---|---|
| `dryrun_sources.py` | network — fetches jobstreet + the remote feeds |
| `dryrun_unpriced.py` | network — checks what the salary parser can't read |
| `dryrun_injection.py` | API key — feeds hostile postings to the draft module |
| `dryrun_jobstreet.py` | network + API key — the full pipeline for one source |
| `dryrun_onlinejobs.py` | network + API key |
| `dryrun_remote_feeds.py` | network + API key |

An API key means the run costs money. A network run means real traffic to a job
board, so do not loop them.

## Testing a source adapter without hitting the board

The adapters split fetching from parsing on purpose, so the parsing half can be
tested against a saved response:

1. Capture one response by hand — `httpx.get(...)` in a REPL, or your browser's
   network tab — and save the JSON or HTML.
2. Call the module's private parse helper directly on it. In
   `sources/jobstreet.py` that is `_arrangement`, `_location`, `_description`;
   in `sources/onlinejobs.py` the BeautifulSoup selectors.
3. Assert on what you get back.

That is how the `workArrangements` field was verified rather than assumed: one
live response saved, `_arrangement(raw)` called on it, `'Hybrid'` returned.

If you are adding a **new** source, keep the same split — a `fetch` that talks to
the network and a pure function that turns one raw record into the common job
dict. It makes the parser testable and it makes the delay/pacing logic one
place.

## Things worth knowing before you change them

- **Filters run before the expensive steps, on purpose.** `filters.py` runs ahead
  of enrich and score so a job that fails a hard rule is dropped before it costs
  an LLM call. Moving a check later is a cost regression even when it reads
  better.
- **Unknown is not treated as bad.** A missing date, salary or location does not
  fail a filter. Most email sources state almost nothing, and rejecting silence
  would drop most of the inbox.
- **Everything from outside is untrusted.** URLs and posting text arrive from
  strangers who can put mail in an inbox. `safefetch.py` exists for that, and
  `dryrun_injection.py` covers the draft path. If your change makes the program
  fetch or execute anything new, say so in the PR.
- **The database migrates itself** on start via `PRAGMA user_version`, and it is
  a one-way trip. Add migrations forward-only.

## Style

Match what is there. Comments explain *why*, not *what* — several in this repo
record a decision that was measured and then revised, and those are the ones
worth keeping.
