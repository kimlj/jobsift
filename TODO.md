# TODO

Working state as of 2026-08-28. Deployed at `/opt/jobsift` on the VPS, **not yet
running continuously** — runs are manual (`--once`) until the systemd unit is installed.

## Waiting on external

- [x] **Working Nomads** — subscribed 2026-08-28 (Development only, Daily, Anywhere+APAC+PH,
      Mid/Senior, Full-time + Contract). Welcome email arrived from `hello@workingnomads.com`;
      `workingnomads.com` added to `known_senders`. Still to confirm: that the daily
      *digest* arrives and extracts cleanly — only the welcome email has been seen so far.
- [ ] Consider We Work Remotely and Remotive — both are pure email digests, no profile needed.

## Code

- [x] **Narrow the LinkedIn sender.** Key is now `jobs-noreply@linkedin.com`, so
      `messages-noreply` and `notifications-noreply` are correctly ignored.
      Note: `updates-noreply` ("Louise posted: WE'RE HIRING...") still slips through via
      the `hiring` subject keyword. Decide whether to drop `hiring` from
      `job_subject_keywords` — it is the loosest one, but it is also the fallback that
      catches boards not yet in `known_senders`.
- [ ] **`--suggest-senders` flag.** Report inbox senders that are NOT in `known_senders`
      but look job-shaped, so a new user isn't guessing what to put in their config.
      This is the worst onboarding gap in the project.
- [ ] **Two-phase IMAP fetch.** `gmail.py` downloads every message in the window in full
      (`RFC822`), one at a time. Fetch headers first, run `classify()`, then pull bodies
      only for job mail. Roughly a third of the window is Strava/GitHub/Google.
- [ ] **Jobstreet enrichment fails.** `url.jobstreet.com` redirector links return
      `400 Bad Request`, so Jobstreet jobs get scored without page enrichment.
- [ ] **Rewrite `docs/job-alert-sources.md`.** Still references n8n nodes ("the *Classify
      email* node") from before the Python rewrite. Should become a real setup guide:
      the three rules (email not in-app / correct address / domain in config), the
      filter-at-source principle, and the subscribe -> read From -> add domain loop.

## Deployment

- [ ] systemd unit from `docs/deploy.md` not installed yet.
- [ ] VPS `config.yaml` has `first_run_lookback_days: 2` (smoke-test value); repo default
      is 7. The DB is no longer fresh, so the backfill will not re-trigger — wipe
      `data/jobs.db` first if a clean full backfill is wanted when going live.

## Do NOT add to known_senders

Verified against real inbox subjects — these send mail but no job listings:

- `glassdoor.com` — Glassdoor *Community* forum threads ("Have you ever been fired?")
- `notifications.freelancer.com` — direct messages ("Re: Mandy") and promos
- `onlinejobs.ph` — profile-onboarding drip only; it is a profile-first marketplace where
  employers message you directly, so it will never emit parseable job alerts
