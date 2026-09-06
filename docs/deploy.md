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
     worksheet: Jobs
     service_account_file: ./service-account.json
   ```

The worksheet is created and given a header row on the first write, so an empty
sheet is fine. A `403` or `PermissionError` on the first run means step 6.

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
