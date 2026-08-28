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
- **Google Sheet (optional)** — set `google_sheet.enabled: true`, put your Sheet id in,
  drop a service-account JSON at `service-account.json`, and **share the Sheet with the
  service account's email** as Editor.

## 3. Test it once

```bash
python -m jobsift --once
```

Watch the log: it should find job emails, extract, score, and write to your outputs.

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
- The Gmail App Password approach uses IMAP — no OAuth, nothing to expire.
