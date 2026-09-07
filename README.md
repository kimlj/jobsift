# jobsift

A self-hosted, open-source job-hunting pipeline — a plain Python script, **no n8n, no
subscription.** Rather than scraping job boards (fragile, rate-limited, constantly
blocked), it lets the **boards email you** and turns your inbox into the data source —
alongside opt-in adapters for the boards that publish a real JSON API.

```
poll Gmail (IMAP) → find job emails (known senders / keywords)
   → LLM extract listings → dedup (normalised title::company)
   → HARD FILTERS: geography, salary floor/ceiling, posting age, title
   → follow link + LLM enrich (skip login-walled domains)
   → score vs your resume (skills/30 + experience/30 + salary/40)
   → save to SQLite → Google Sheet archive → Telegram alert if score ≥ threshold
```

The hard filters sit **before** enrich and score, which is the whole point of
them: scoring is a soft signal — salary is only 40 of 100 points, so a job that
pays nothing still clears the threshold on skills alone — while a job that fails
a hard rule is dropped whatever it would have scored, and dropped before it costs
an LLM call. See `jobsift/filters.py`.

You subscribe to job alerts on each board once (Indeed, LinkedIn, Foundit, Jobstreet,
remote & PH boards…), point them at one Gmail, and the script does the rest.

Some boards give you more than an email will. For those there are **optional, opt-in
source adapters** (`jobsift/sources/`), all off by default:

- **A public JSON API, where one exists** — Jobstreet PH rides on SEEK's search API,
  which hands over salary in pesos-per-month, work arrangement and country already
  structured. Four remote boards (remotive, Working Nomads, himalayas, jobicy) go
  further and include the **whole posting** in the feed, so there is no page to
  follow at all. One GET, no HTML, no LLM extract call.
- **An HTML listing, as a last resort** — onlinejobs.ph is a profile-first marketplace
  that emits no job emails at all. Paced to the site's robots.txt `Crawl-delay`, and
  read a board's Terms of Service before enabling it: some prohibit automated access
  regardless of what their robots.txt allows.

## Where each source should come from

Three tiers, and the ranking is not the obvious one:

1. **A public JSON API.** Best when it exists. It is not scraping — nothing is parsed
   out of a page, nothing breaks when the CSS changes, and the fields arrive typed.
   It also reaches what email cannot: Jobstreet's own alert emails carry a teaser, and
   its job page is 403 behind Cloudflare, so email-only scoring was working from
   whatever the alert happened to quote.
2. **The board's email alerts.** The default, and the only thing that reaches
   auth-gated boards — Indeed, LinkedIn and Foundit have no open API and are most of
   the PH market. Every board already offers alerts; subscribing gives a feed that
   never gets Cloudflare-blocked and never rots when a site changes its HTML.
3. **Reading the HTML listing.** Genuinely fragile and rate-limited, and worth it only
   for a board that offers neither of the above.

The original argument here was "email beats scraping". That is true of tier 3 and
false of tier 1, which was worth writing down after measuring both.

## Everything is a param

- **`.env`** — secrets (your LLM API key — OpenAI or Anthropic — Gmail app password, Telegram token).
- **`config.yaml`** — the tunables: which senders count as job alerts, score threshold,
  which models, and whether the Google Sheet / Telegram outputs are on.

Both outputs are **optional**: Telegram = high-score pings (kept sparse so it doesn't
flood), Google Sheet = full browsable archive of every scored job.

## Quick start

Needs **Python 3.10+** (the code uses `X | None` type syntax) and an API key
for one LLM provider — [Anthropic](https://console.anthropic.com) or
[OpenAI](https://platform.openai.com). **The key costs money to use:** every
job is read by the model once, and anything past the filters is read again to
score it. One pass over a day of alerts plus the scrape sources ran ~150 calls.
Drafting an application is a further call per job, on the more expensive model,
and only ever when you ask for one - by running `--draft` or by ticking the
`draft` box on a row in the sheet. Do the first run with `--once --no-telegram`
and read the CSV before scheduling anything.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env                  # fill in secrets
cp config.example.yaml config.yaml    # tune settings
cp resume.example.txt resume.txt      # paste your profile

python -m jobsift --once          # test one pass
python -m jobsift --once --no-telegram   # ...and send no alerts while you inspect it
python -m jobsift                 # run continuously

python -m jobsift --export jobs.csv      # every stored job, one row each, for Excel
python -m jobsift --draft "Acme" --posting posting.txt   # cover letter + answers
```

**`--export`** is the counterpart to Telegram. Telegram is deliberately sparse —
it only ever shows jobs over the alert threshold — so the CSV is how you see what
the pipeline actually did rather than what it decided was worth interrupting you
about. `--only-passing` re-checks stored rows against today's filters, since
filters run at ingest and tightening one leaves older rows in place.

**`--draft`** writes a cover letter and answers for one stored job and **sends
nothing** — it prints text for you to read and edit. Pass `--posting` with the
full job text: alert emails carry a truncated snippet, and the module needs the
whole posting because employers bury compliance instructions ("start your message
with the word PURPLE") in the last lines to catch people who skimmed.

Full deployment — systemd, Windows Task Scheduler or cron, and how the host you
pick decides which sources you can have → [docs/deploy.md](docs/deploy.md).
Which boards to subscribe to → [docs/job-alert-sources.md](docs/job-alert-sources.md).
Why something is built the way it is → [docs/decisions.md](docs/decisions.md).

## Layout

```
jobsift/            the app
  __main__.py           entry point (python -m jobsift)
  config.py             loads .env + config.yaml
  llm.py                pluggable provider layer (openai | anthropic)
  gmail.py              IMAP inbox reader (App Password)
  classify.py           known_senders / keyword match
  extract.py            LLM: pull jobs out of an alert email
  filters.py            hard gates: geography, salary, age, title
  safefetch.py          SSRF-hardened HTTP for URLs we did not choose
  enrich.py             follow job link → clean page → LLM details
  score.py              resume scoring (skills + experience + salary)
  draft.py              cover letter + answers for one job (never sends)
  store.py              SQLite dedup + job log (versioned migrations)
  export.py             every stored job → CSV for Excel
  sheets.py             optional Google Sheet output
  notify.py             optional Telegram output
  utils.py              shared helpers (title/company normalisation, job_key)
  pipeline.py           orchestration
  sources/              optional non-email sources (opt-in, off by default)
    jobstreet.py        Jobstreet PH via SEEK's public search API
    remote_feeds.py     remotive / Working Nomads / himalayas / jobicy
    onlinejobs.py       onlinejobs.ph public listing reader
config.example.yaml     copy to config.yaml
.env.example            copy to .env
applier/                (planned) Claude-in-Chrome auto-applier
```

## Everything from outside is untrusted

Two of this program's inputs are chosen by strangers, and both are handled as
hostile by default.

**URLs.** `enrich` follows a link that arrived in an email, and `classify` accepts
mail on a subject keyword alone ("job alert", "hiring") — so the sender does not
have to be a board we know to pick a URL for this program to fetch. That reaches
anything the host can reach: `http://127.0.0.1:8080/admin`, the cloud
metadata endpoint at `169.254.169.254`, a database admin page — and on a machine at
home, everything else on the LAN, including the router's admin page. `safefetch.py`
answers with four rules: HTTPS only; every resolved address must be public (one
private address among several is a rejection, not a fallback); the request goes to
the **validated IP** with `Host` and TLS SNI set to the original hostname, so
there is no second DNS lookup to poison with a rebinding race; and every redirect
hop is re-validated, because a permitted host answering `302 -> 169.254.169.254`
is the same attack wearing a hat. The host allowlist is optional and empty by
default — we follow links to arbitrary employer sites, so a mandatory allowlist
would simply turn enrichment off.

**Posting text.** `draft.py` passes a whole posting, written by a stranger, to a
model — which is exactly the shape a prompt injection needs. "Ignore anything the
posting tells you" is not available here, because employers legitimately bury
compliance instructions in the last lines and following them is the feature. The
line drawn instead is what an instruction is *about*: instructions about the
application the candidate will send ("begin with the word BANANA", "use subject
REF-4471") are an employer talking to an applicant, and are followed. Anything
addressed to the assistant — new rules, a demand for different output, a request
to claim experience the resume lacks, an instruction to contact some address — is
not something a real employer writes, and is reported in `injection_attempts`
rather than obeyed.

## Roadmap

- [x] Drafting: cover letter + per-question answers for review (`--draft`), sends nothing
- [ ] Applier: Claude-in-Chrome semi-auto form fill (pre-fill, human submits)
- [ ] Standard ATS support (Greenhouse / Lever / Ashby) + email-apply drafting
- [ ] Optional web dashboard over the SQLite log

## Responsible use

Personal job-hunting tool: apply to real openings with individually reviewed,
personalized applications. Respect each board's Terms of Service. The planned applier
pauses for your approval before submitting — keep it that way.

## Secrets

`.env`, `config.yaml`, `resume.txt`, `service-account.json`, and `data/` are gitignored.
Never commit real tokens. If one leaks into git history, rotate it.

## Contributing

There is no test suite; there are eight `dryrun_*.py` scripts, two of which run
offline. See [CONTRIBUTING.md](CONTRIBUTING.md) for which need a key or network,
and how to test a source adapter without hitting a live board.

## License

MIT. See [LICENSE](LICENSE).
