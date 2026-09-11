# jobsift architecture

A complete description of how jobsift works, written to be handed to someone
(or a model) who has never seen the code, so their suggestions fit the real
system. Facts here were read from the code on 11 Sep 2026; `docs/decisions.md`
and `TODO.md` hold the reasoning and the measurements behind them.

## What it is

A personal job-hunting pipeline, in Python, that runs unattended as one
long-lived process. It finds job postings, throws out the ones that cannot fit,
scores the rest against the candidate's resume with an LLM, and puts them in a
Google Sheet and a Telegram alert. It can also draft an application for one job.

**It never applies to anything and never sends anything on the candidate's
behalf.** Drafts are text for a human to read, edit and send.

The candidate is a full-stack developer in the Philippines, so everything is
tuned for PH-based or worldwide-remote roles, with salaries normalised to
monthly PHP.

## The shape of it

```
                ┌───────────────────── one Python process, loop every 300s ─────────────────────┐
                │                                                                                │
 SOURCES        │   1. evidence index refresh (hourly)                                           │
                │   2. sheet: file rows staged "Closed"; serve rows with the draft box ticked    │
 Gmail (IMAP) ──┼─► 3. inbox pass:                                                               │
  job-alert     │        receipts ─► mark jobs Applied (regex, no LLM)                           │
  emails from   │        classify ─► extract (LLM) ─► dedup ─► hard filters ─► enrich (LLM)      │
  Indeed,       │        ─► score (LLM + code) ─► filters again ─► store ─► sheet ─► Telegram    │
  LinkedIn,     │                                                                                │
  Jobstreet,    │   4. scrape sources (each at most every 1200s):                                │
  Working       │        remote JSON feeds, onlinejobs.ph listing pages                          │
  Nomads...     │        ─► same dedup ─► filters ─► enrich ─► score ─► store ─► sheet ─► alert  │
                │                                                                                │
 remote JSON ───┤   state: SQLite (data/jobs.db)                                                 │
 feeds          │   outputs: Google Sheet, Telegram, CSV export on demand                        │
 onlinejobs.ph ─┘                                                                                │
                └────────────────────────────────────────────────────────────────────────────────┘
```

Every source produces the same job dict, so everything after "extract" is
source-agnostic.

## Sources

| Source | How | Notes |
|---|---|---|
| **Email alerts** (the default, and most of the volume) | IMAP with a Gmail app password. `classify.py` decides if a mail is a job alert from `known_senders` (domain -> board name) and subject keywords | Boards email you, so nothing has to be scraped. Indeed, LinkedIn, Jobstreet and Working Nomads arrive this way. Indeed's alert carries a ~160-character snippet and its job page refuses automated fetches, so Indeed jobs are scored on the snippet |
| **Remote JSON feeds** (`sources/remote_feeds.py`) | Public, unauthenticated APIs: remotive, Working Nomads, himalayas, jobicy | Full posting text inline, so no page fetch is needed. Himalayas is cursor-paged |
| **onlinejobs.ph** (`sources/onlinejobs.py`) | Reads public listing and detail pages, honouring its robots.txt `Crawl-delay: 5` | The best source: every job that has scored 90+ came from here. **Answers a datacenter IP with a Cloudflare 403**, which is why the pipeline runs on a home connection (see Deployment). Skips detail pages for listings already stored: one pass logged "skipping 377 detail pages, saves 1885s" |
| Jobstreet search API | SEEK's public search endpoint | **Off.** The paths it used are disallowed by Jobstreet's robots.txt, and it 403s a datacenter anyway. Jobstreet still arrives by email |

Sources are opt-in per source in `config.yaml`, each with its own interval, and
an adapter that fails never takes down the email pass.

## The per-job pipeline

In `pipeline.run_once`:

1. **Receipts first.** `applied.py` scans the fetched mail for application
   confirmations (Indeed and Jobstreet templates observed in a real inbox) and
   marks the matching stored job Applied. Regex only, no LLM.
2. **Classify** each unprocessed mail; non-job mail is marked processed and dropped.
3. **Extract** (LLM, `extract.py`): pull individual listings out of an alert
   email. One call per email.
4. **Dedup** on a normalised `title::company` key (`utils.job_key`). Company
   names are normalised hard ("WeSupport, Inc." = "WeSupport Incorporated"),
   titles lightly, because a wrong merge silently loses a real job while a missed
   one only costs a duplicate alert.
5. **Hard filters** (`filters.py`, pure code, no network), BEFORE any further
   spend: excluded companies (about 50 named PH call-centre firms), excluded titles
   (junior, intern, virtual assistant...), a salary floor in monthly PHP,
   and a four-tier geography rule (PH anywhere is fine; outside PH it must be
   remote AND open to a PH-based candidate, read from the feed's eligibility
   field). The salary parser handles ~30 currencies, hourly/annual periods and
   European digit separators. The USD rate is fetched daily (`fx.py`).
6. **Enrich** (LLM, `enrich.py`): follow the job link and read structured
   details off the page. Sources that already carry the full posting skip it.
   All fetches go through `safefetch.py`: HTTPS only, every resolved address
   must be public, the connection is pinned to the validated IP, every redirect
   re-checked. Links come out of emails a stranger can send, so this is an SSRF
   defence.
7. **Score** (`score.py`): skills 0-30 and experience 0-30 from the LLM against
   `resume.txt`; salary 0-40 computed in code. A job scored on a snippet is
   capped, because a model asked to infer from a teaser invents a stack and
   matches against its own invention. If skills+experience fall below
   `min_fit_ratio` (0.3), the total is held under the threshold however well it
   pays. The system prompt is byte-identical across calls so the resume is
   prompt-cached.
8. **Filters again** with what scoring learned (e.g. a stated degree requirement).
9. **Store, sheet, alert.** Saved to SQLite, appended to the sheet, and sent to
   Telegram if the score is at least `score_threshold` (60).

Cost: roughly 150 LLM calls for one pass over a day of alerts plus the scrape
sources. Models are set per stage in `config.yaml` (Anthropic or OpenAI,
`llm.py` hides which).

## State

**SQLite, `data/jobs.db`, on one machine. It is the only record** of which mail
was processed, which jobs were seen, and which were applied to. Tables:
`processed_emails`, `seen_jobs` (dedup keys, including filtered-out jobs, expire
after 30 days), `jobs` (every saved job as JSON), `applied_jobs`, `source_runs`
(last run per scrape source). Key-format changes are migrated via
`PRAGMA user_version`.

**Two copies of jobsift must never run against two databases.** They do not
coordinate: both read the same mail, both score it, and every job is paid for
and alerted twice. Any design with two machines has to give one of them the
database and make the other a feeder.

## Outputs

- **Google Sheet** (service account). Tabs: *Shortlist* (score at or above the
  bar), *Below the bar*, *Closed*, *Drafts*. Each row has a `stage` dropdown:
  New, To apply, Applied, Interviewing, Rejected, Ignore, Closed. Setting
  **Closed** moves the row to the Closed tab on the next pass. Ticking the row's
  **draft** box makes the loop draft that job on the next pass and write the
  result to the Drafts tab. Postings that disappear from their board are moved to
  Closed as `delisted` (`--check-listings`).
- **Telegram**: sparse, only jobs at or above the threshold, with a warning when
  the score came from a snippet.
- **CSV export** (`--export`): every stored job, every field.

## Drafting an application

Triggered by the sheet's draft box or `--draft "Company" --posting file.txt`.
Two implementations behind one interface (`multi_agent_drafting` picks):

- `draft.py`: one LLM call writes the requirement table, cover letter, answers
  and a tailored resume.
- `agents.py` (in use): four roles so one can check another.
  - **extractor** (cheap model) reads only the posting and lists what it
    requires. It never sees the candidate, so it cannot bend a requirement toward
    them.
  - **drafter** (strong model) writes from the requirements plus the trusted
    sources, and must list every factual claim it makes.
  - **verifier** (cheap model, one call per claim, parallel) sees the claim and the
    trusted sources, never the posting or the letter, and answers supported,
    overstated or unsupported.
  - **supervisor** sends rejections back for at most two revisions, then ships
    with the failures visible.

Trusted sources are `resume.txt`, `profile.yaml` (facts and decisions: years by
role, education, salary expectation, availability) and the **evidence brief**
below. The salary answer is computed from the posting's advertised band, not
decided by the model. Posting text is fenced as untrusted: instructions to the
applicant ("start with the word PURPLE") are followed; instructions to the model
are refused and reported as `injection_attempts`.

## The evidence index

A resume always lags the work, and a draft can only claim what it can check.
The index is everything else the candidate has built, in a form a draft may cite.

- `evidence.py` finds every git repo under configured folders and on GitHub
  (repos only on GitHub are cloned into `data/mirrors/`). Identity comes from the
  GitHub remote, not the folder name. It counts, from files: commits, languages,
  named dependencies (including in sub-packages), SQL migrations and RLS
  policies, test files, CI. **Only what the candidate's own commits touched is
  counted**, so cloned or forked code never lends its languages to the
  candidate. It also reads the portfolio's skills list and pull requests merged
  into other people's projects, with what each changed and who reviewed it.
  Output: `data/evidence.yaml`.
- `career.yaml` (hand-written, gitignored): which repo is which product, facts
  each with a source that the build checks exists, cautions, gaps with honest
  bridges, and rules (e.g. "a lifetime player count must carry the daily count").
- `career.py` merges both into `data/career-brief.md` (~20 KB), the one document
  the drafter, verifier and tailoring tool read. Rules are checked in code
  against every finished draft.
- **It refreshes itself**: jobsift's loop checks hourly, every draft checks
  first, only repos with new commits are re-counted, and GitHub is read once a
  day. An unchanged check costs one git call per repo.

A separate Claude Code skill on the laptop (outside this repo) uses the same
brief to tailor a resume PDF and cover letter for one posting interactively.

## Deployment today

**The candidate's Windows laptop, not a server.** One process started by Task
Scheduler through `run-jobsift.ps1`, logging to `logs/jobsift-YYYYMMDD.log`.
Triggers: at logon, at workstation unlock, and every 15 minutes as a watchdog;
`MultipleInstances IgnoreNew` makes the extra triggers no-ops while it runs, so
it comes back within 15 minutes of being killed. Battery stops and time limits
are off.

**Why not the VPS it used to run on** (a 1 GB DigitalOcean droplet with a
systemd unit, now stopped; the droplet also hosts other services and stays):
measured from that droplet, with the same code and User-Agent:

| From a datacenter IP | Result |
|---|---|
| onlinejobs.ph | **403** (Cloudflare) |
| Jobstreet search | **403** |
| remotive, jobicy, Working Nomads | 200 |
| Gmail IMAP, Anthropic/OpenAI, Google Sheets, FX | fine |

So everything except onlinejobs.ph works from a server, and onlinejobs.ph is
the best source. Rejected on purpose: residential proxy services (renting
someone else's home IP is impersonation), browser impersonation, and ignoring
robots.txt.

**What the laptop costs**: when it sleeps, jobs are delayed, not lost. Alert
mails wait in Gmail until the next pass, and onlinejobs.ph keeps postings up for
months.

## Constraints any redesign must respect

1. **One database, one writer.** Two machines means one owns `jobs.db` and
   the other feeds it. The feeder must not keep its own dedup state.
2. **onlinejobs.ph needs a residential connection**, and nothing else does.
3. **The "already seen" check has to reach the scraper.** Skipping detail pages
   for stored listings saves about 30 minutes per pass at the site's 5-second
   crawl delay. A scraper on another machine must ask the database owner which
   listings are new before fetching details.
4. **No new public attack surface lightly.** The droplet hosts other services.
   Anything that accepts job text feeds it to an LLM; SSH or a private network
   (Tailscale) beats a public HTTP endpoint.
5. **Drafting runs where the loop runs.** Moving the loop moves drafting, and
   the evidence brief it needs: a server builds it from GitHub mirrors (so it
   needs a token that can read private repos) and loses local-only folders.
6. **Nothing sends on the candidate's behalf.** Drafts stay text for a human.
7. **Respect the boards**: public pages or published feeds only, their crawl
   delays, their robots.txt.
8. **Personal data stays out of git**: `config.yaml`, `.env`, `resume.txt`,
   `profile.yaml`, `career.yaml` and `data/` are gitignored; the repo is public.

## Hosting options on the table (Sep 2026)

| Option | What changes | Result |
|---|---|---|
| Keep the laptop | Nothing (watchdog now restarts it) | Nothing lost; alerts delayed while it sleeps |
| Always-on box at home (N100 mini PC, Pi, NAS) running **all** of jobsift | Copy `jobs.db`, install the existing systemd unit, retire the laptop task | Every source 24/7 from a residential IP, one machine, no new code |
| VPS runs everything except onlinejobs.ph; laptop scrapes onlinejobs.ph and feeds the VPS | **Built** as the residential worker (`jobsift/worker.py`, [residential-worker.md](residential-worker.md)): a pinned SSH key, an inbox the core's loop reads, a `seen` query, pushed brief and personal files | Email and feeds 24/7; onlinejobs.ph only while the laptop is awake; one database, still one writer |

## Where to look

| Path | What |
|---|---|
| `jobsift/__main__.py` | CLI flags and the main loop |
| `jobsift/pipeline.py` | The per-job pipeline above |
| `jobsift/sources/` | Non-email sources |
| `jobsift/filters.py` | Hard filters and salary normalisation (pure, no I/O) |
| `jobsift/score.py`, `extract.py`, `enrich.py` | The three LLM stages |
| `jobsift/agents.py`, `draft.py` | Drafting |
| `jobsift/evidence.py`, `career.py` | Evidence index |
| `jobsift/sheets.py`, `notify.py`, `export.py` | Outputs |
| `jobsift/store.py` | SQLite |
| `docs/deploy.md` | Hosting, the datacenter measurements, Task Scheduler setup |
| `docs/decisions.md` | Why things are built the way they are |
| `docs/evidence-index.md` | Evidence index setup |
| `dryrun_*.py`, `tests/` | Checks; the six offline ones run in CI on every push |
