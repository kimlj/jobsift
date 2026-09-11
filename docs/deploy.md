# Deploying jobsift

No n8n, no subscription — it's a plain Python script.

## 0. Choose the host first

This is a decision, not a detail, because **the host decides which sources you can
have.** Some boards answer a datacenter IP with a Cloudflare 403 while serving the
identical request from a home connection — measured table under *The scrape sources
may not work from a datacenter*.

| What you want | Where to run it |
|---|---|
| Email alerts and the remote JSON feeds | A server. Unattended, always on, nothing blocked |
| `onlinejobs.ph`, or any board that 403s a datacenter | A machine on a residential connection: a laptop, or a small always-on box at home. Or split: the core on a server and a [residential worker](residential-worker.md) at home reading only these boards |
| Both | Still **one** host. Pick whichever carries the sources you actually use |

The email half is most of jobsift and works anywhere, so a server is the right
default. The scrape sources are the part that cares where you are.

A home deployment only runs while that machine is on. Nothing is lost when it is
off — unread email waits in the mailbox and listings stay up — but alerts arrive
when the machine next wakes rather than when the job was posted. That gap only
costs you something if you would have acted on the alert during it.

Whatever you choose, **never run two copies against one database**. See *Do not run
two copies* below.

## 1. Get the code + install

```bash
git clone <your-repo-url> jobsift && cd jobsift
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## 2. Configure

`python -m jobsift --setup` does this whole section for you. It makes the files
below from their examples, asks for each account in turn, and checks that each one
signs in before saving it. Secrets go to `.env` only, `config.yaml` keeps every
comment, and running it again changes only what you change. The rest of this
section is what it does, for doing it by hand or checking its work.

```bash
cp .env.example .env                  # fill in secrets
cp config.example.yaml config.yaml    # tune settings
cp resume.example.txt resume.txt      # paste your profile
```

- **`.env`** — your LLM key (`ANTHROPIC_API_KEY` or `OPENAI_API_KEY`, matching
  `llm_provider` in config.yaml), `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`
  (Google Account → Security → 2-Step Verification → **App passwords**), and
  optionally `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`.
- **`config.yaml`** — `llm_provider`, `known_senders`, `score_threshold`, and outputs.
- **Google Sheet (optional)** - a live copy of every saved job, which a CSV cannot
  be: an export locks while the file is open in Excel, does not refresh a sheet you
  are already reading, and overwrites any notes you added to it. Setup below.

### Google Sheet (optional)

Every saved job is appended to a sheet as it is found, so the list can be worked
from a phone and you can keep your own columns beside it.

1. **A new Google Cloud project** - console.cloud.google.com, *New Project*. Use
   one of your own rather than a client's or an employer's: the key below can
   write to any sheet it has been shared with.
2. **Enable the Google Sheets API** - *APIs & Services -> Library*. Only that one.
   The code asks for the `spreadsheets` scope and nothing else, so the Drive API is
   not needed here (exporting whole files is what needs Drive).
3. **Create a service account** - *Credentials -> Create credentials -> Service
   account*. Skip the optional role step. It needs no project-level IAM role,
   because its access comes from the sheet being shared with it, which is the
   narrower arrangement.
4. **Download a JSON key** - the account's *Keys* tab -> *Add key -> Create new key
   -> JSON*. Save it as `service-account.json` in the repo root; it is gitignored.
   Treat it as a password: it can write to every sheet it has been shared with.
5. **Create the sheet and copy its id from the URL** - the long string between
   `/d/` and `/edit`.
6. **Share the sheet with the service account.** Open `service-account.json`, copy
   the `client_email` value (it ends `.iam.gserviceaccount.com`), and share the
   sheet with that address as **Editor**.

   This is the step that gets missed. Everything else can be right and it will
   still fail, because the program opens the sheet by id with no Drive scope - it
   cannot discover a sheet, only open one it was explicitly given.
7. **Point the config at it:**

   ```yaml
   google_sheet:
     enabled: true
     sheet_id: "1AbC...XyZ"
     worksheet: Shortlist
     worksheet_below: Below the bar
     service_account_file: ./service-account.json
   ```

Both tabs are created and given a header row on the first write, so an empty
sheet is fine. A `403` or `PermissionError` on the first run means step 6.

### What you get

Every job that survives the filters is written to one of two tabs, split at
`score_threshold`: `Shortlist` at or above it, `Below the bar` under it. Nothing
scored is thrown away, because the rows just under the bar are how you tell
whether the bar is in the right place. Set `worksheet_below: ""` for a single tab.

Each tab is set up on every run, so there are no menus to find:

| | |
|---|---|
| **Column A, `applied`** | A tick box, frozen alongside the header row. It is yours — the program writes `FALSE` on a new row and never touches it again |
| **`url`** | A narrow `open` link rather than the full address |
| **Row 1** | Bold, with a filter on it, so any column can be sorted — click the funnel on `score` |

Tick `applied` on a row and pass `--skip-applied` to leave it out of later
exports and backfills. Both tabs are read, since a job worth applying to is not
always one the scorer liked.

```bash
python -m jobsift --backfill-sheet --min-score 0 --only-passing
```

fills the tabs from jobs already in the database — safe to re-run, as it skips
every url the sheet already holds.

### The applied box mostly ticks itself

Indeed and Jobstreet email a receipt when an application goes in, and jobsift
already reads that mailbox, so the tick can come from mail addressed to you
rather than from scraping a board you are logged in to. Every run does this;
`--scan-applied` does it on demand over a longer window:

```bash
python -m jobsift --scan-applied 180    # 180 days back; default is 90
```

It costs nothing — a regex over mail already in the account, no LLM call.

| board | what it sends |
|---|---|
| Indeed | `Indeed Application: <title>` on submission. Names no company |
| Jobstreet | `Your application was successfully submitted`, naming title and employer |
| Jobstreet | `<employer> has viewed your application for <title>`. Not a receipt, but nobody views an application that was never sent, so it only ever adds a tick |
| onlinejobs.ph | Unknown — no evidence either way. Add a rule to `jobsift/applied.py` if a receipt turns up |

Two things it will not do. It **never unticks** a row: the boards confirm
applications, not their absence, so a tick you made by hand always stands. And a
receipt that names only a title, matching two stored jobs with that title, is
reported rather than guessed at — a tick on the wrong row hides a job you never
applied to, which is worse than no tick at all.

`--skip-applied` reads both the database and your own ticks, so it works whether
the mark came from a receipt or from you.

### Drafting: the one thing here that costs money per use

> **Every draft is a paid model call.** Scoring already costs one call per job and
> happens on its own; drafting costs *another*, on the more expensive model, and it
> only happens when you ask for it. Nothing in this section runs by itself.

Two ways to ask.

**From the terminal**, when you have the posting text in front of you:

```bash
python -m jobsift --draft 309 --posting posting.txt
```

**From the sheet**, which is the easier one: tick the **`draft`** box on any row.
The next pass picks it up, drafts, and writes the result to the **Drafts** tab.

Nothing happens the instant you tick. A sheet cannot call the program - the program
reads the sheet, once a pass - so the **`draft_status`** column beside the box is
where it answers you:

| `draft_status` | means |
|---|---|
| *(empty)* | not seen yet. Wait one poll interval, five minutes by default |
| `queued` | seen, and waiting behind the per-pass cap |
| `drafting...` | being written now |
| **see Drafts** | done - the cell is a link straight to the Drafts tab |
| `failed: ...` | it broke, with the reason |

That column is written by the program; the `draft` and `applied` boxes are yours.

### Upgrading

Pull a newer jobsift and your sheet catches up on its own. Columns added or moved
by a release are inserted and moved **with their data** the next time the program
opens the sheet, before the header row is touched.

What that does not do is rewrite rows already written. A column can change meaning
without changing name, so after upgrading run:

```bash
python -m jobsift --resync-sheet
```

It rewrites every row from the database using the current version's columns and
formatting. **Free** - no model calls - and it leaves your `applied` and `draft`
ticks alone.

If you added a column of your own, the migration stops and says so rather than
guessing where it belongs. Remove or rename it and the sheet migrates.

### Currency

Salaries appear twice: as the board wrote them, and normalised to pesos a month.
The second is what the salary filter actually compared, so a listing that looks
underpaid is usually one where the two disagree - `$25/hr` is not a low number.

The USD rate is **fetched daily** (ECB reference rates, no key) and cached in
`data/fx.json`. If the fetch fails it falls back to the cache, then to
`filters.usd_to_php` in your config, then to a built-in - a currency API being
down never stops a run. The startup log says which was used:

```
USD to PHP: 62.67 (live from frankfurter, 2026-09-05)
```

Set `filters.usd_to_php` only if you want to pin a rate; it is the fallback, not
an override.
Draft requests are served at the *start* of a pass, before the sources are scraped,
because a scrape can run for several minutes on its own.

Either way you get a requirements table, a cover letter, answers to the employer's
questions and a tailored resume. `--no-sheet` prints without logging.

What the tick costs and what stops it running away:

| | |
|---|---|
| Cost | One model call per ticked row, on the scoring model |
| Rate | At most **3 per pass**. Tick forty rows and it does three, logs the rest, and picks them up on later passes |
| Repeats | A url already in the Drafts tab is never drafted again. **The tick is never cleared** — that column is yours, and the program only reads it |
| Posting text | The stored page text first; the live page if that is thin; the summary only as a last resort, and the draft says so when it had to |

**Most email-sourced rows will not have enough posting text**, and no amount of
waiting changes that: the boards that send alert emails are the same ones on
`skip_link_domains`, so their pages are never fetched. What drafts well is the
remote JSON feeds (whole posting inline) and onlinejobs.ph (its scraper reads the
page). For anything else, `--draft <id> --posting FILE` is the method rather than
the fallback — open the posting, save it, pass it in.

The Drafts tab is created the first time you draft something, never before. Its rows
are clipped to one line each, since a cover letter and a resume in one row make it
about 950px tall. Click a cell to read or copy the whole thing.

The resume is plain text in a cell, to be copied into whatever you actually send. It
is not a generated document on purpose: a cell holds 50,000 characters, the text is
editable in place, and producing a .docx would buy formatting you are going to
replace anyway.

Where the posting advertises a salary band whose midpoint beats your stated
expectation, the salary answer is that midpoint rather than your `profile.yaml`
figure. That figure is what you would accept from an unknown employer; one who has
published a band has already told you their budget.

## 3. Test it once

First, find out which of your emails it will recognise:

```bash
python -m jobsift --suggest-senders       # the last 30 days; pass a number for more
```

It lists senders that look like job alerts but are not in `known_senders`, with the
lines to paste, and names the known ones that sent nothing. It reads only senders
and subjects, marks nothing read, and changes nothing.
[job-alert-sources.md](job-alert-sources.md) has the details. Then one pass:

```bash
python -m jobsift --once --no-telegram    # score and store everything, alert nobody
python -m jobsift --export jobs.csv       # then read what it actually did
```

Watch the log: it should find job emails, extract, score, and write to your outputs.

Do the first run with `--no-telegram`. A first pass over a backlogged inbox can
match far more than a steady-state run, and the CSV is the honest view — Telegram
only ever shows jobs over `score_threshold`, so it cannot tell you what the
filters dropped or why. Once the CSV looks right, drop the flag.

## 4. Run it continuously

**Option A — systemd** (a Linux server or an always-on box at home): create `/etc/systemd/system/jobsift.service`:

```ini
[Unit]
Description=jobsift
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=YOU
WorkingDirectory=/home/YOU/jobsift
ExecStart=/home/YOU/jobsift/.venv/bin/python -u -m jobsift
Restart=always
RestartSec=30

# Python buffers stdout when it is a pipe, and journalctl is a pipe. Without
# this (or the -u above) the log arrives in 8KB blocks, so `journalctl -f`
# shows nothing for the first half hour and you conclude it is not running.
Environment=PYTHONUNBUFFERED=1

# It reads a mailbox, holds API keys and writes one directory. Nothing here is
# exotic; it is the standard set, and a compromised dependency is the reason.
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=/home/YOU/jobsift/data /home/YOU/jobsift/logs

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now jobsift
journalctl -u jobsift -f      # follow logs
```

**Check these three lines on the first start.** Each is a thing that fails
quietly rather than crashing:

```
USD to PHP: 62.67 (live from frankfurter, 2026-09-05)   <- not "fallback"
Google Sheet output enabled (Shortlist)                  <- not an exception
Telegram alerts enabled (threshold 60)
```

`ProtectHome=read-only` is the one that usually bites: the service account JSON,
the resume, `.env` and `config.yaml` are all read-only under it, which is
correct, but `data/` and `logs/` must be listed in `ReadWritePaths` or the
database cannot be written. If the paths are wrong the unit fails on the first
write, not on start.

### What it needs that the local run does not

| | |
|---|---|
| **Outbound HTTPS** | Anthropic or OpenAI, `imap.gmail.com:993`, Google Sheets, the boards, `api.frankfurter.app` |
| **The clock** | Ensure `systemd-timesyncd` is running. IMAP `SINCE` searches and the crawl-delay bookkeeping are both date arithmetic |
| **`data/` on disk you back up** | `jobs.db` holds dedup state, processed-email uids and every applied confirmation. Losing it re-alerts everything |
| **The whole config set** | `.env`, `config.yaml`, `resume.txt`, `profile.yaml`, `service-account.json`. Four of the five are gitignored, so `git clone` on the VPS gets you none of them |

Copy the gitignored files over rather than recreating them, and `chmod 600` them:

```bash
scp .env config.yaml resume.txt profile.yaml service-account.json YOU@vps:~/jobsift/
ssh YOU@vps 'chmod 600 ~/jobsift/{.env,service-account.json,profile.yaml}'
```

### The scrape sources may not work from a datacenter

**Check this before assuming a quiet VPS is broken.** Some boards answer a
datacenter IP with a Cloudflare 403 while serving the identical request from a
home connection. Measured from a DigitalOcean droplet, same code and same
User-Agent as a working local run:

| | from a VPS |
|---|---|
| `onlinejobs.ph` | **403** |
| `jobstreet` search and GraphQL | **403** |
| `remotive`, `jobicy`, `workingnomads` | 200 |
| Anthropic / OpenAI, Google Sheets, the FX endpoint | fine |

**The email half is unaffected**, and that is most of jobsift: alerts arrive over
IMAP from your own mailbox, not fetched from the board. Scoring, the sheet,
Telegram, drafting from an emailed posting and the applied-receipt scan all work
exactly as they do locally.

If a source is blocked on your host, **turn it off there** rather than leaving it
on. Requesting a page every five minutes from a site that has answered 403 is
pointless and rude, and it is the kind of traffic that gets a block widened:

```yaml
scrape_sources:
  onlinejobs_ph:
    enabled: false     # per-host: 403 from this address, fine from home
```

Do not work around it by pretending to be a browser. The block is the site's
answer, and this program's whole approach is to use what boards publish - a JSON
feed, an alert email, a documented endpoint - rather than to look like something
it is not.

The honest options are: run the scrape sources on a machine with a residential
connection and the email sources anywhere; run everything at home; or accept the
feeds that do answer. **Do not run two copies against one database** - see below.

### Do not run two copies

The dedup state is in SQLite on one machine, so a laptop and a VPS running at
once do not coordinate: both fetch the same mail, both score it, and you pay
twice for duplicate Telegram alerts and duplicate sheet rows. Stop the local one
before enabling the service.

When you move hosts, move the database rather than starting fresh — otherwise the
first pass on the new host treats every job in your inbox as new, and re-alerts a
backlog you have already read:

```bash
scp data/jobs.db YOU@vps:~/jobsift/data/      # local -> server
scp YOU@vps:~/jobsift/data/jobs.db data/      # server -> local
```

Stop the old host **before** copying, so nothing writes to the file mid-transfer,
and disable it (`systemctl disable jobsift`) so a reboot cannot quietly restart a
second copy. If both hosts have been running, compare the two files rather than
assuming the newer one wins — a copy that has been off for a week may still hold
rows the other never saw.

### Upgrading a systemd host

```bash
cd ~/jobsift && git pull
sudo systemctl restart jobsift
.venv/bin/python -m jobsift --resync-sheet     # only after a release that changes columns
```

The database migrates itself on start. The sheet migrates its columns on the next
pass. `--resync-sheet` is the third part — it rewrites values under columns whose
meaning changed — and it is free.

**Option B — Windows Task Scheduler** (for a laptop or home desktop):

`run-jobsift.ps1` in the repo root is the launcher. It derives every path from its
own location, so the repo can live anywhere, and writes a dated log under `logs/`.
Register it to start at logon:

```powershell
$script = "$PWD\run-jobsift.ps1"
$action  = New-ScheduledTaskAction -Execute 'powershell.exe' `
  -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`""
$triggers = @(
  New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
  # The watchdog: every 15 minutes, forever. A no-op while jobsift runs.
  New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 15)
)
$unlock = New-CimInstance -ClientOnly -CimClass (Get-CimClass `
  -Namespace ROOT\Microsoft\Windows\TaskScheduler -ClassName MSFT_TaskSessionStateChangeTrigger)
$unlock.StateChange = 8                                  # workstation unlock
$unlock.UserId      = "$env:USERDOMAIN\$env:USERNAME"
$triggers += $unlock
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew `
  -ExecutionTimeLimit ([TimeSpan]::Zero) `
  -RestartInterval (New-TimeSpan -Minutes 5) -RestartCount 999 -StartWhenAvailable
$settings.DisallowStartIfOnBatteries = $false
$settings.StopIfGoingOnBatteries     = $false
Register-ScheduledTask -TaskName jobsift -Action $action -Trigger $triggers -Settings $settings
```

Four of those settings are load-bearing, and the Task Scheduler defaults get all
four wrong for this program:

- **`IgnoreNew`** — one pass at a time. Without it a restart can put two processes
  on one SQLite file, which is the same hazard as running two hosts.
- **`DisallowStartIfOnBatteries` / `StopIfGoingOnBatteries` false** — both default
  to *true*, so on a laptop the task silently refuses to start unplugged and dies
  the moment you unplug it. This is the setting people lose an afternoon to.
- **`ExecutionTimeLimit` zero** — it is a daemon, not a job. The default kills it
  after three days.

**The two extra triggers are the restart, and `-RestartCount` is not.** The
process survives sleep and resume; it does not survive being killed. On
10 Sep 2026 it was terminated from outside while waiting between two passes, the
laptop awake (exit `0xC000013A`), and stayed down for twelve hours: `-RestartCount` only covers a task
that fails to *launch*, and a logon trigger never fires on a laptop that sleeps
instead of signing out. The 15-minute trigger brings it back within a quarter
hour of any exit, the unlock trigger the moment you open the laptop, and
`IgnoreNew` makes both of them no-ops while it is running. Missed firings during
sleep run on waking, because of `-StartWhenAvailable`.

Watch it with `Get-Content logs\jobsift-*.log -Tail 40 -Wait`.

**Why the launcher redirects through `cmd.exe`.** Python's `logging` writes to
stderr. Windows PowerShell 5.1 wraps every stderr line from a native command in a
`NativeCommandError`, so under `*>>` redirection with
`$ErrorActionPreference = 'Stop'` the first line jobsift logs becomes a terminating
error and the task dies on startup. The symptom is indistinguishable from a broken
interpreter: task result `1`, an empty log, no process, and a manual
`python -m jobsift --help` that works perfectly — because `--help` writes to
stdout. `cmd`'s own `>>` and `2>&1` do no such wrapping.

**Option C — cron** (with `--once`):

```cron
*/15 * * * * cd /home/YOU/jobsift && .venv/bin/python -m jobsift --once >> data/cron.log 2>&1
```

**Mind the interval if any `scrape_sources` are enabled.** A pass that reads
onlinejobs.ph spends its time inside the five-second Crawl-delay, and a catch-up
pass after that source has been off is long: one measured 2026-09-07 read 350
detail pages, which is **29 minutes** in one pass. A `*/5` or `*/15` schedule
starts the next copy before that finishes, and cron will not stop it.

Steady-state passes are far shorter, because a listing already in `seen_jobs` no
longer costs a detail fetch. Size the interval on your slowest pass rather than
your typical one, or use Option A or B, where the program's own loop paces itself
and cannot overlap.

**Option D — Docker** (any machine with Docker, a VPS included): nothing to install
but Docker. The image holds the code and nothing of yours. `.dockerignore` is an
allowlist, so `.env`, the service-account key, your resume and the database cannot
end up inside it, and `compose.yaml` mounts this folder at `/work`, where they stay.

```bash
git clone <your-repo-url> jobsift && cd jobsift
docker compose run --rm jobsift --setup     # the same setup, inside the container
docker compose up -d                        # run it; restarts on failure and at boot
docker compose logs -f                      # watch it
```

Every other command works the same way, and anything it writes lands in this
folder: `docker compose run --rm jobsift --export jobs.csv`. Upgrading is
`git pull && docker compose up -d --build`; the database migrates itself on start,
as it does anywhere else.

What differs from running it directly:

| | |
|---|---|
| **File ownership (Linux)** | The container runs as uid 1000 and has to write `data/`. If `id -u` says something else, start it as `JOBSIFT_UID=$(id -u) JOBSIFT_GID=$(id -g) docker compose up -d`. Docker Desktop on Windows and macOS does not care |
| **The evidence index** | Not in the image: it needs `git`, the `gh` CLI and your repos on disk. Build the brief where the repos live, as the residential worker does, and set `evidence.roots: []` in the container's config so it is not rebuilt from nothing |
| **Hardening** | Read-only filesystem, no Linux capabilities, `no-new-privileges`, a 256 MB memory cap: Option A's systemd settings, in compose form |
| **One copy** | Still one copy per database. `restart: unless-stopped` brings it back after a reboot, so stop any Task Scheduler job or systemd unit first |
| **The code** | Always the image's, even with a checkout mounted at `/work` (`PYTHONSAFEPATH`). A `git pull` changes nothing until `--build` |

## Notes

- Dedup + processed-email state lives in `./data/jobs.db` (gitignored). Back it up if you care about history.
- The database **migrates itself** on start (`PRAGMA user_version`), so pulling a
  new version needs no manual step and no re-scoring — but it is a one-way trip,
  so take a copy of `jobs.db` before upgrading if you might roll back.
- If you enable anything under `scrape_sources`, this box starts making outbound
  requests to job boards on a schedule. The pacing state lives in the same
  database (`source_runs`), so deleting `jobs.db` also resets the crawl-delay
  bookkeeping and the next run will hit every source immediately.
- The Gmail App Password approach uses IMAP — no OAuth, nothing to expire.
