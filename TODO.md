# TODO

Working state as of 2026-08-28. Deployed at `/opt/jobsift` on the VPS, **not yet
running continuously** — runs are manual (`--once`) until the systemd unit is installed.

## Waiting on external

- [ ] **Working Nomads** — subscribed 2026-08-28 (Development only, Daily, Anywhere+APAC+PH,
      Mid/Senior, Full-time + Contract). When the first email lands, read its `From`
      domain and add it to `known_senders`. Do not guess the domain: Jobstreet turned
      out to be `e.jobstreet.com`, Indeed `jobalert.indeed.com`.
- [ ] Consider We Work Remotely and Remotive — both are pure email digests, no profile needed.

## Code

- [ ] **Narrow the LinkedIn sender.** `known_senders` matches bare `linkedin.com`, which
      swallows all four LinkedIn senders. Only `jobs-noreply@linkedin.com` carries job
      alerts; the other ~75% is profile-view and feed noise being sent to the LLM for
      nothing. `classify()` substring-matches the full sender, so the fix is to use
      `jobs-noreply@linkedin.com` as the key.
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
