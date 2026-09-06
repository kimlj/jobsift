# Running on your VPS

No n8n, no subscription — it's a plain Python script.

## 1. Get the code + install

```bash
git clone <your-repo-url> jobsift && cd jobsift
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## 2. Configure

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

**Option A — systemd (recommended):** create `/etc/systemd/system/jobsift.service`:

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

If you have been running locally, move the database rather than starting fresh —
otherwise the first VPS pass treats every job in your inbox as new:

```bash
scp data/jobs.db YOU@vps:~/jobsift/data/
```

### Upgrading on the VPS

```bash
cd ~/jobsift && git pull
sudo systemctl restart jobsift
.venv/bin/python -m jobsift --resync-sheet     # only after a release that changes columns
```

The database migrates itself on start. The sheet migrates its columns on the next
pass. `--resync-sheet` is the third part — it rewrites values under columns whose
meaning changed — and it is free.

**Option B — cron** (with `--once` every 5 min):

```cron
*/5 * * * * cd /home/YOU/jobsift && .venv/bin/python -m jobsift --once >> data/cron.log 2>&1
```

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
