# jobsift

**Job alerts, read and ranked for you.** jobsift reads the job-alert emails that
Indeed, LinkedIn, Jobstreet and other job boards send you, plus four free
remote-job feeds. It throws away the jobs you said you don't want, scores the rest
against your resume with AI, and sends the best ones to your phone.

It runs on your own computer or your own small server, under your control. It
never applies to anything and never sends anything on your behalf: it finds and
ranks, and you decide.

jobsift is free and open source. The only running cost is the AI service you
choose, which you pay directly.

## Start here (no programming needed)

### 1. What you need

- **A Gmail address** where your job alerts arrive, with 2-Step Verification turned
  on. jobsift reads it with an "app password", which only exists once 2-Step
  Verification is on.
- **An AI key** from one of these: [DeepSeek](https://platform.deepseek.com) (the
  cheapest), [OpenAI](https://platform.openai.com), or
  [Claude](https://console.anthropic.com). You pay that company for what jobsift
  uses; see *What it costs* below.
- **Your resume**, as a PDF or a text file.
- **Optional:** Telegram on your phone, for alerts.
- **A computer that stays on**, or a small cloud server (see step 6).

### 2. Install it: one line

**Windows:** open PowerShell (press the Start button, type `PowerShell`, press
Enter), then paste this and press Enter:

```powershell
irm https://raw.githubusercontent.com/kimlj/jobsift/main/install.ps1 | iex
```

**Mac or Linux:** open Terminal, paste this and press Enter:

```bash
curl -fsSL https://raw.githubusercontent.com/kimlj/jobsift/main/install.sh | sh
```

It downloads jobsift into a folder called `jobsift` in your home folder, installs
what it needs, and starts the setup. It needs two free tools, git and Python; if
either is missing, it tells you the one line that installs it. Running the same
line again later updates jobsift and keeps all your answers.

Prefer to read something before running it? The two scripts are
[install.ps1](install.ps1) and [install.sh](install.sh), in this repository.

### 3. Answer the setup questions

Eight short steps. Press Enter to keep an answer or skip anything optional:

| Step | What it asks |
|---|---|
| 1. Files | Nothing; it makes your settings files |
| 2. AI provider | deepseek, openai or claude, and your key. It checks the key works |
| 3. Gmail | Your address and an app password (it gives you the link to make one). It checks it can sign in |
| 4. Your resume | The path to your resume file, or paste the text in |
| 5. Your search | Words that should be in a job title (for example `virtual assistant, developer`), words to leave out, companies to skip, your lowest monthly pay, and the score that earns an alert. All optional; it starts open |
| 6. Where jobs come from | Which job boards to subscribe to, and whether to read the free remote-job feeds |
| 7. Telegram | Optional, about two minutes: connect a bot, and a test message arrives on your phone |
| 8. Google Sheet | Optional, and the longest step. Skip it the first time |

To change any answer later, run the setup again (see step 5 for how).

### 4. Get job alerts flowing

jobsift can only read alerts you have subscribed to. On each job board, search for
the work you want, then turn on email alerts for that search, **sent to the same
Gmail**:

- Indeed: <https://ph.indeed.com>
- LinkedIn: <https://www.linkedin.com/jobs>
- Jobstreet: <https://ph.jobstreet.com>
- More boards, and what each one sends: [docs/job-alert-sources.md](docs/job-alert-sources.md)

The free remote-job feeds need nothing from you; they are on unless you turned
them off in step 6. Until your first alerts arrive, they are where jobs come from.

### 5. Run it

Open the `jobsift` folder in PowerShell or Terminal:

- Windows: `cd $HOME\jobsift`
- Mac or Linux: `cd ~/jobsift`

| Type this (Windows) | Or this (Mac, Linux) | What happens |
|---|---|---|
| `.\jobsift.cmd --once --no-telegram` | `./jobsift.sh --once --no-telegram` | One check that alerts nobody. **Do this first** |
| `.\jobsift.cmd --export jobs.csv` | `./jobsift.sh --export jobs.csv` | Every job it found, its score, and why each was kept or dropped, in a file Excel opens |
| `.\jobsift.cmd --suggest-senders` | `./jobsift.sh --suggest-senders` | Which of your emails look like job alerts it does not know yet |
| `.\jobsift.cmd --setup` | `./jobsift.sh --setup` | Change any setup answer |
| `.\jobsift.cmd` | `./jobsift.sh` | Keep running, checking every 5 minutes |

The first check reads the last 7 days of your email, so it takes a few minutes and
finds the most. After that it only reads what is new.

A Telegram alert looks like this:

```
79/100 · Shopify Virtual Assistant
🏢 Acme Online Store
💰 $800 per month
Skills 24/30 · Exp 20/30 · Pay 35/40
✅ Shopify, Canva, Zendesk
❌ Klaviyo
📝 Manage the store's orders, customer emails and product listings...
https://...
```

The score is out of 100: how well your skills and experience match (up to 60), and
how the pay compares (up to 40). A warning line appears when the job was scored on
a short snippet, or when it states a degree as required.

### 6. Keep it running 24/7

**On your own computer.** At the end of the install, the Windows installer offers
to start jobsift by itself whenever you sign in. It pauses while the computer
sleeps and carries on when it wakes; nothing is lost, it only arrives later.

**On a small cloud server,** so it runs even when your computer is off. A cloud
server (often called a "VPS") is a small computer you rent in a data centre, always
on. If you have never had one, this is the whole process, about 15 minutes:

1. **Sign up at [DigitalOcean](https://www.digitalocean.com).** It asks for a card
   or PayPal. It is recommended here because signing up is simple, it has a data
   centre in Singapore (close to the Philippines, so it is fast), and its smallest
   useful server is US$6 a month (price checked 11 Sep 2026). Any provider that
   offers Ubuntu works the same way: Hetzner and Vultr are two others.
2. **Create the server.** Click **Create**, then **Droplets** (DigitalOcean's name
   for a server), and choose:
   - Region: **Singapore**
   - Image: **Ubuntu**, the newest version marked **LTS**
   - Size: **Basic**, **Regular**, the **1 GB / US$6** plan. The US$4 plan has half
     the memory, which is tight for installing jobsift.
   - Authentication: **Password**. Make it long, and keep it in your password manager.

   Then click **Create Droplet**. It is ready in about a minute.
3. **Open its console.** Click the new server's name, then **Console** (under
   *Access*). A black window opens: you are now typing on the server.
4. **Paste the Mac or Linux line** from step 2 of this guide and press Enter.
   Answer the setup questions as you would on your own computer. For the resume,
   paste its text in.
5. **Answer `Y`** when it asks *"Keep jobsift running on this server, now and after
   every restart?"*. That's it: jobsift is running, and it starts again by itself
   after every restart.

Afterwards, from the same console: `journalctl -u jobsift -f` shows what it is
doing (press Ctrl+C to leave it), and pasting the install line again updates it.
To stop paying, destroy the server on its DigitalOcean page; DigitalOcean bills by
the hour, so nothing else keeps charging.

One board, onlinejobs.ph, blocks cloud servers; email alerts and the remote feeds
work normally from one.

**With Docker**, if you already use it: `docker compose run --rm jobsift --setup`,
then `docker compose up -d`. See *Option D* in [docs/deploy.md](docs/deploy.md).

### What it costs

- **jobsift:** free.
- **The AI:** you pay your chosen provider for each job it reads and scores. A day of
  alerts is roughly 150 AI calls. As a rough estimate that is around 10 US cents a
  day with DeepSeek, and around a dollar a day with Claude. The first run, which
  reads a week of email, costs more. Your provider's website shows exactly what you
  have used.
- **A cloud server:** optional. DigitalOcean's 1 GB server is US$6 a month (checked
  11 Sep 2026).
- **Gmail and Telegram:** free.

### Your privacy

- jobsift runs on your computer or your server. Your email, resume and keys stay
  there, in the `jobsift` folder, and never go to the people who make jobsift.
- It reads your inbox, but only works on job-alert emails, and it marks nothing as
  read.
- To score a job, it sends the job's text and your resume to the AI provider you
  chose, and to nobody else.
- It never applies to a job and never sends an email for you.

### If something goes wrong

| What you see | What to do |
|---|---|
| "Gmail refused that address and app password" | Make an app password (step 3 gives the link). It needs 2-Step Verification turned on first |
| "the key was refused" | Copy the key again from your AI provider's website; it is shown only once, when it is made |
| No jobs after the first run | No alerts subscribed yet (step 4), or your search words dropped them. `--export jobs.csv` shows why each job was dropped |
| You want to change an answer | Run `--setup` again. Enter keeps everything you do not change |
| Anything else | Open an issue on this repository's GitHub page and paste what you saw |

---

## For developers

A plain Python program (3.10+): no n8n, no subscription. Rather than scraping job
boards (fragile, rate-limited, constantly blocked), it lets the **boards email
you** and turns your inbox into the data source, alongside adapters for the boards
that publish a real JSON API.

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

Some boards give you more than an email will. For those there are **source
adapters** (`jobsift/sources/`). The remote feeds are on in the example config;
the others are opt-in:

- **A public JSON API, where one exists** — Jobstreet PH rides on SEEK's search API,
  which hands over salary in pesos-per-month, work arrangement and country already
  structured. Four remote boards (remotive, Working Nomads, himalayas, jobicy) go
  further and include the **whole posting** in the feed, so there is no page to
  follow at all. One GET, no HTML, no LLM extract call.
- **An HTML listing, as a last resort** — onlinejobs.ph is a profile-first marketplace
  that emits no job emails at all. Paced to the site's robots.txt `Crawl-delay`, and
  read a board's Terms of Service before enabling it: some prohibit automated access
  regardless of what their robots.txt allows.

Every adapter's `search_keywords` and `include_keywords` may be left empty, and then
they follow `filters.include_titles`, the job words `--setup` asks for.

### Where each source should come from

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

### Everything is a param

- **`.env`** — secrets (your LLM API key — DeepSeek, OpenAI or Anthropic — Gmail app password, Telegram token).
- **`config.yaml`** — the tunables: which senders count as job alerts, the search
  words, score threshold, which models, and whether the Google Sheet / Telegram
  outputs are on.

Both outputs are **optional**: Telegram = high-score pings (kept sparse so it doesn't
flood), Google Sheet = full browsable archive of every scored job.

### Installing by hand

The installers above do exactly this. Each provider defaults to a cheap model for
reading and a stronger one for judging, and `config.example.yaml` lists what they
cost. Drafting an application is a further call per job, on the stronger model,
and only ever when you ask for one - by running `--draft` or by ticking the
`draft` box on a row in the sheet.

```bash
git clone https://github.com/kimlj/jobsift.git && cd jobsift
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python -m jobsift --setup         # makes the files, asks for each account, checks each one works
python -m jobsift --suggest-senders   # which of your emails are job alerts it does not know yet
cp career.example.yaml career.yaml    # optional: what your repos are, with sources

python -m jobsift --index-repos   # count your repos into the brief drafts are written from

python -m jobsift --once          # test one pass
python -m jobsift --once --no-telegram   # ...and send no alerts while you inspect it
python -m jobsift                 # run continuously

python -m jobsift --export jobs.csv      # every stored job, one row each, for Excel
python -m jobsift --draft "Acme" --posting posting.txt   # cover letter + answers
```

**`--setup`** makes `config.yaml`, `.env`, `resume.txt` and `profile.yaml` from the
examples, then asks for an AI key, your Gmail app password, your resume, your
search words and the sources, and optionally a Telegram bot and a Google Sheet,
checking that each account signs in before it is saved. Secrets go to `.env` only,
`config.yaml` keeps every comment, and running it again changes only what you
change. To do it by hand instead, copy the `*.example` files and fill them in;
[docs/deploy.md](docs/deploy.md) explains every field.

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

What a draft may claim about you, and how to give it more than your resume →
[docs/evidence-index.md](docs/evidence-index.md).
Running on a server while onlinejobs.ph is read from home →
[docs/residential-worker.md](docs/residential-worker.md).
Full deployment — systemd, Windows Task Scheduler or cron, and how the host you
pick decides which sources you can have → [docs/deploy.md](docs/deploy.md).
Which boards to subscribe to → [docs/job-alert-sources.md](docs/job-alert-sources.md).
Why something is built the way it is → [docs/decisions.md](docs/decisions.md).

### Layout

```
jobsift/            the app
  __main__.py           entry point (python -m jobsift)
  config.py             loads .env + config.yaml
  llm.py                pluggable provider layer (deepseek | openai | anthropic)
  gmail.py              IMAP inbox reader (App Password)
  classify.py           known_senders / keyword match
  extract.py            LLM: pull jobs out of an alert email
  filters.py            hard gates: geography, salary, age, title
  safefetch.py          SSRF-hardened HTTP for URLs we did not choose
  enrich.py             follow job link → clean page → LLM details
  score.py              resume scoring (skills + experience + salary)
  draft.py              cover letter + answers for one job (never sends)
  agents.py             drafting as roles: extract, draft, verify each claim
  evidence.py           counts your repos: commits, stack, migrations, CI
  career.py             career.yaml + the counts -> the evidence brief; rule checks
  worker.py             residential worker: onlinejobs.ph from home, the rest on a server
  store.py              SQLite dedup + job log (versioned migrations)
  export.py             every stored job → CSV for Excel
  sheets.py             optional Google Sheet output
  notify.py             optional Telegram output
  utils.py              shared helpers (title/company normalisation, job_key)
  pipeline.py           orchestration
  onboard.py            --setup: connect each account and check it works
  senders.py            --suggest-senders: alert senders config.yaml is missing
  sources/              non-email sources (remote feeds on; the rest opt-in)
    jobstreet.py        Jobstreet PH via SEEK's public search API
    remote_feeds.py     remotive / Working Nomads / himalayas / jobicy
    onlinejobs.py       onlinejobs.ph public listing reader
install.ps1, install.sh   the one-line installers
jobsift.cmd, jobsift.sh   run it with the folder's own Python, from anywhere
config.example.yaml     copy to config.yaml
career.example.yaml     copy to career.yaml (optional)
.env.example            copy to .env
Dockerfile, compose.yaml  the container (docs/deploy.md, Option D)
applier/                (planned) Claude-in-Chrome auto-applier
```

### Everything from outside is untrusted

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

### Roadmap

- [x] Drafting: cover letter + per-question answers for review (`--draft`), sends nothing
- [ ] Applier: Claude-in-Chrome semi-auto form fill (pre-fill, human submits)
- [ ] Standard ATS support (Greenhouse / Lever / Ashby) + email-apply drafting
- [ ] Optional web dashboard over the SQLite log

### Responsible use

Personal job-hunting tool: apply to real openings with individually reviewed,
personalized applications. Respect each board's Terms of Service. The planned applier
pauses for your approval before submitting — keep it that way.

### Secrets

`.env`, `config.yaml`, `resume.txt`, `service-account.json`, and `data/` are gitignored.
Never commit real tokens. If one leaks into git history, rotate it.

### Contributing

There is no test suite; there are sixteen `dryrun_*.py` scripts, ten of which run
offline. See [CONTRIBUTING.md](CONTRIBUTING.md) for which need a key or network,
and how to test a source adapter without hitting a live board.

### License

MIT. See [LICENSE](LICENSE).
