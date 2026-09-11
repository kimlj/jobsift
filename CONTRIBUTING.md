# Contributing

This is a personal tool that turned out to be useful to other people. Patches are
welcome; so is a bug report that just says what you expected and what happened.

## Before you send a change

There is no test suite. There are eighteen `dryrun_*.py` scripts instead, and they
are the closest thing to one. Each answers a single question about behaviour and
prints what it found rather than asserting — the point is that you read the
output and judge it, because most of the interesting failures here are "this
scored 22 on a snippet" rather than "this raised an exception".

**Twelve run with no network and no API key.** Start here, and if your change
touches filtering, dedup, the applied detector, the sheet, the evidence index,
the residential worker, the inbox, setup, sender suggestions, the employer
check or rendering an application, these are the ones that matter:

```bash
python dryrun_dedup.py       # normalised title::company dedup — does it over- or under-merge?
python dryrun_geography.py   # the location rules, in increasing order of surprise
python dryrun_applied.py     # confirmation emails: does it fire only on real receipts?
python dryrun_seen_skip.py   # a job already stored — is it skipped before it costs anything?
python dryrun_closed.py      # staging a row Closed: does the right row move, intact, once?
python dryrun_evidence.py    # the evidence index: miscounts, wrong identities, stale sources
python dryrun_worker.py      # the residential worker: dedup across machines, retries, the pinned key
python dryrun_setup.py       # --setup: writes only what it was told, keeps comments, prints no secret
python dryrun_suggest_senders.py  # --suggest-senders and the daily add: the right boards, never Gmail, never twice, ignore wins
python dryrun_inbox.py       # a pass: nothing marked read, nothing downloaded twice, receipts cost nothing
python dryrun_vet.py         # --vet: the employer check lands on every copy of a posting, and replaces the last one
python dryrun_render.py      # --render and --publish: the fullest page that fits, every check, the right files per board
```

Each of the twelve exits non-zero when a case misbehaves, so `pytest` runs all of
them at once (`pip install -r requirements-dev.txt` first), and GitHub Actions
runs the same on every push and pull request. A red check on your PR means one
of these printed FAIL; the log shows which case.

`dryrun_applied.py` is the one to read closely if you touch `applied.py`. Half
its cases are mail that must NOT match — job alerts from the same senders, a
posting whose title contains the word "Application" — because the cost of a
false positive there is a job silently marked applied that never was.

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

## Changing the installers or setup

`install.sh` and `install.ps1` are the first thing a new user runs, so a mistake
in them is the whole first impression. Before pushing a change to either:

```bash
sh -n install.sh
powershell -NoProfile -Command "$null = [scriptblock]::Create((Get-Content -Raw install.ps1))"
```

After pushing, run the real one-liner in a clean Linux container, with setup
skipped (the one-liner downloads from `main`, so test what you pushed):

```bash
docker run --rm -e JOBSIFT_SKIP_SETUP=1 python:3.12-slim sh -c \
  'apt-get update -qq && apt-get install -y -qq git curl >/dev/null &&
   curl -fsSL https://raw.githubusercontent.com/kimlj/jobsift/main/install.sh | sh &&
   ~/jobsift/jobsift.sh --help && ls -l ~/jobsift/jobsift.sh'
```

On Windows, commit shell scripts with `git add --chmod=+x install.sh jobsift.sh`
and check that `git ls-tree HEAD install.sh jobsift.sh` says `100755`: a commit
that names its paths takes the mode from the Windows working tree, which has no
executable bit, and the launcher then fails on Linux with "permission denied".

A change to setup's questions needs the matching lines in `dryrun_setup.py`,
which scripts the whole conversation, prompt by prompt.

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

[docs/decisions.md](docs/decisions.md) is the long version: what cost real
debugging time, and what looks wrong until you know why. Read it before undoing
something that seems overcomplicated - most of it is there because the obvious
alternative was tried first.


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
