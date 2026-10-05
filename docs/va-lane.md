# The VA lane

A second jobsift running beside the developer lane, for tech virtual-assistant
work on onlinejobs.ph. Same code, its own config, database, sheet and profile,
so VA listings never mix with developer ones and each lane's scores mean one
thing.

| | Developer lane | VA lane |
|---|---|---|
| Config | `config.yaml` | `config.va.yaml` (from `config.va.example.yaml`) |
| Runs on | the droplet (core), fed by the laptop worker | the laptop, as a full pipeline |
| Database | `data/jobs.db` on the droplet | `data/va.db` on the laptop |
| Scored against | `resume.txt` | `resume-va.txt` |
| Sources | Gmail alerts, remote feeds, onlinejobs.ph via the worker | onlinejobs.ph only, hourly |
| Sheet | the jobsift sheet | its own sheet |
| Telegram | same chat | same chat, every alert prefixed `[VA]` |
| Task Scheduler | `jobsift` | `jobsift-va` |
| Log | `logs/jobsift-YYYYMMDD.log` | `logs/jobsift-va-YYYYMMDD.log` |

## Why it is shaped this way

- **A separate database, not a second tab.** The developer lane's filters drop VA
  titles on purpose (`filters.exclude_titles`), and its scores are against a
  developer profile. One database would need both rule sets to agree on every
  listing; two lanes need nothing from each other.
- **On the laptop, not the droplet.** onlinejobs.ph answers a datacenter IP with
  a 403 (docs/residential-worker.md), and this lane reads nothing else. Running it
  where the board answers keeps it one process with one database, the same rule
  the worker split was built on. "Never run both machines as full pipelines" is
  about one database; this lane has its own.
- **Inbox off** (`email_enabled: false`). The inbox belongs to the developer lane.
  A second reader would extract and score every alert email again into `va.db`.
- **One crawler at a time.** Both lanes read onlinejobs.ph from the same IP.
  `sources/onlinejobs.py` holds an OS file lock (`data/onlinejobs.lock`) for a
  whole read, so the second lane waits for the first and the board still sees
  one request per Crawl-delay. A process that dies releases the lock.
- **Tech VA only.** Measured 2026-10-05 over 434 live VA listings: tech VA work
  (GoHighLevel, Zapier, CRM, AI, automation) was the biggest group, 104 of them,
  and the best paid, median about PHP 46,000 a month. It is also where this
  profile is strong. Searches are tool names, because the site's search reads the
  whole posting: `zapier` also finds a plainly titled "Virtual Assistant" whose
  body asks for Zapier.
- **Unpriced listings are kept**, unlike the developer lane, with a PHP 25,000
  floor on the ones that state pay, and a 7-day age limit because VA postings
  fill fast.

## Running it

```powershell
# one pass, no Telegram, to look at what it finds
.venv\Scripts\python.exe -m jobsift --config config.va.yaml --once --no-telegram

# fill the sheet from va.db after setting google_sheet.sheet_id
.venv\Scripts\python.exe -m jobsift --config config.va.yaml --backfill-sheet
```

Every command that takes `--config` works on this lane's database, on the laptop:
the refusal for database commands applies only to a config with `worker.enabled`.

The task `jobsift-va` is a copy of `jobsift` (logon, unlock and 15-minute
watchdog triggers, one instance) whose action passes
`-Config config.va.yaml -LogName jobsift-va` to `run-jobsift.ps1`.

## Applying

Start a tech VA application from the Tech VA master, named explicitly:

```powershell
.venv\Scripts\python.exe -m jobsift --render-init <Company> --master data\masters\Kim_Julongbayan_Resume_TechVA.docx
```

Its spec is `data/applications/TechVA.yaml`; edit that and re-render to change the
master. Both stay on this machine: the VA resume is never put in `~/port` or on the
website (owner, 2026-10-05). It is kept out of `render.masters` on purpose: with no `--master`,
`--render-init` takes the newest master, and a developer application must not
start from this one by accident.

## Changing what it looks for

`scrape_sources.onlinejobs_ph` in `config.va.yaml`: `search_keywords` is what the
board is asked for, `include_keywords` is the title whitelist, and
`exclude_keywords` catches what a broad whitelist word let in. `dryrun_va_lane.py`
checks the gate against real titles; add a title there when you change a list.

To score differently, edit `resume-va.txt`. Its "not on record" list is what keeps
a cold-calling or Amazon posting from scoring as a match.
