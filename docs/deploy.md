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

[Service]
WorkingDirectory=/home/YOU/jobsift
ExecStart=/home/YOU/jobsift/.venv/bin/python -m jobsift
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now jobsift
journalctl -u jobsift -f      # follow logs
```

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
