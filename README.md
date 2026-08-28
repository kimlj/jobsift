# jobsift

A self-hosted, open-source job-hunting pipeline — a plain Python script, **no n8n, no
subscription.** Instead of scraping job boards (fragile, rate-limited, constantly
blocked), it lets the **boards email you** and turns your inbox into the data source.

```
poll Gmail (IMAP) → find job emails (known senders / keywords)
   → LLM extract listings → dedup (title::company)
   → follow link + LLM enrich (skip login-walled domains)
   → score vs your resume (skills/30 + experience/30 + salary/40)
   → save to SQLite → Google Sheet archive → Telegram alert if score ≥ threshold
```

You subscribe to job alerts on each board once (Indeed, LinkedIn, Foundit, Jobstreet,
remote & PH boards…), point them at one Gmail, and the script does the rest.

Some boards offer no email alerts at all. For those there is an **optional, opt-in
source adapter** (`jobsift/sources/`) that reads a public listing directly — off by
default, paced to the site's robots.txt `Crawl-delay`. Email remains the default and
the recommended path; read a board's Terms of Service before enabling an adapter,
since some prohibit automated access regardless of what their robots.txt allows.

## Why email-ingestion beats scraping

Every board already offers email alerts. Subscribing gives you a clean, structured feed
that never gets Cloudflare-blocked and never rots when a site changes its HTML. One
inbox replaces a dozen brittle scrapers.

## Everything is a param

- **`.env`** — secrets (your LLM API key — OpenAI or Anthropic — Gmail app password, Telegram token).
- **`config.yaml`** — the tunables: which senders count as job alerts, score threshold,
  which models, and whether the Google Sheet / Telegram outputs are on.

Both outputs are **optional**: Telegram = high-score pings (kept sparse so it doesn't
flood), Google Sheet = full browsable archive of every scored job.

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env                  # fill in secrets
cp config.example.yaml config.yaml    # tune settings
cp resume.example.txt resume.txt      # paste your profile

python -m jobsift --once          # test one pass
python -m jobsift                 # run continuously
```

Full deployment (systemd / cron on a VPS) → [docs/deploy.md](docs/deploy.md).
Which boards to subscribe to → [docs/job-alert-sources.md](docs/job-alert-sources.md).

## Layout

```
jobsift/            the app
  __main__.py           entry point (python -m jobsift)
  config.py             loads .env + config.yaml
  gmail.py              IMAP inbox reader (App Password)
  classify.py           known_senders / keyword match
  extract.py            LLM: pull jobs out of an alert email
  enrich.py             follow job link → clean page → LLM details
  score.py              resume scoring (skills + experience + salary)
  store.py              SQLite dedup + job log
  sheets.py             optional Google Sheet output
  notify.py             optional Telegram output
  pipeline.py           orchestration
  sources/              optional non-email sources (opt-in, off by default)
    onlinejobs.py       onlinejobs.ph public listing reader
config.example.yaml     copy to config.yaml
.env.example            copy to .env
reference/              the original n8n workflow, kept as the design blueprint
applier/                (planned) Claude-in-Chrome auto-applier
```

## Roadmap

- [ ] Applier: Claude-in-Chrome semi-auto form fill (pre-fill + draft answers, human submits)
- [ ] Standard ATS support (Greenhouse / Lever / Ashby) + email-apply drafting
- [ ] Optional web dashboard over the SQLite log

## Responsible use

Personal job-hunting tool: apply to real openings with individually reviewed,
personalized applications. Respect each board's Terms of Service. The planned applier
pauses for your approval before submitting — keep it that way.

## Secrets

`.env`, `config.yaml`, `resume.txt`, `service-account.json`, and `data/` are gitignored.
Never commit real tokens. If one leaks into git history, rotate it.
