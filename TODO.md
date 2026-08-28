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
- [x] **Malformed URLs (was misdiagnosed as "Jobstreet enrichment fails").** The real
      cause was `mask_urls`: `https?://\S+` is greedy over non-whitespace, so a link
      written as `[https://...]` captured the closing bracket. 24% of stored URLs ended
      in `]`, which 400'd on fetch AND shipped broken links to Telegram. Fixed by
      trimming trailing punctuation and re-emitting it into the text.
- [ ] **Jobstreet pages return 403 to automated fetches.** With URLs now clean, the
      redirector resolves correctly to `ph.jobstreet.com/job/<id>`, but that page blocks
      us (a browser User-Agent alone does not help). Enrichment degrades gracefully, so
      Jobstreet jobs are scored from email content only. Low priority — the emails
      already carry title/company/salary.
- [ ] **Rewrite `docs/job-alert-sources.md`.** Still references n8n nodes ("the *Classify
      email* node") from before the Python rewrite. Should become a real setup guide:
      the three rules (email not in-app / correct address / domain in config), the
      filter-at-source principle, and the subscribe -> read From -> add domain loop.

## Deployment

- [ ] systemd unit from `docs/deploy.md` not installed yet.
- [ ] VPS `config.yaml` has `first_run_lookback_days: 2` (smoke-test value); repo default
      is 7. The DB is no longer fresh, so the backfill will not re-trigger — wipe
      `data/jobs.db` first if a clean full backfill is wanted when going live.

## Filtering & alerts (done 2026-08-28)

- [x] Hard filters in `filters.py`, applied to every source BEFORE enrich/score so a
      rejected job costs nothing: exclude_companies (50 named PH BPOs, whole-word),
      exclude_titles (jr/junior/intern/...), min_salary_php.
- [x] One salary normaliser returning monthly PHP. Fixed real bugs: "PHP 180,000 -
      350,000 a year" (15k/mo) had been scoring a full 40/40 as if monthly and was the
      top alert at 82; peso-symbol and weekly listings silently fell back to the
      10-point "unknown" default. Ranges read their LOW end.
- [x] Hourly conversion follows hours the listing states ("30 hrs per week"), falling
      back to 40h/week, or 20h/week when tagged part-time. Note: measured on the real
      db, 0 of 149 listings state hours and only 3 quote hourly — this matters for
      onlinejobs.ph and remote boards, not for Indeed/Jobstreet PH.
- [x] Telegram: bare URLs only. Tested four variants against a real client — plain URL
      with or without HTML parse mode opens directly; only <a href> triggers the
      "Open link?" confirmation. HTML formatting kept.
- [x] enrich resolves tracking redirects and strips referral params, so a Jobstreet
      link is 37 chars instead of 286. Job-identifying params (Indeed's "jk") kept.
- [x] iOS "open in app" on jobstreet links: `/job/*` is a registered Universal Link in
      their apple-app-site-association, so nothing sender-side avoids it. Resolved on
      the device via the status-bar breadcrumb, which sets a persistent per-domain
      preference. Confirmed working. If it ever regresses, the fallback is a
      `resolve_redirects: false` flag to keep links on the non-registered
      url.jobstreet.com host, at the cost of 286-char URLs.
- [x] usd_to_php and salary_baseline_php moved out of code into config.

## Known data gaps

- [ ] **Indeed gives us almost nothing to filter on.** Its alert emails carry a
      ~160-char truncated snippet (25 of 149 stored jobs sit at exactly 160-161
      chars), and the full page is unreachable: /viewjob returns 401 and the
      rc/clk link returns 403 with a captcha challenge. So `requirements` is empty
      for 0/149 jobs and only 3/149 mention a degree anywhere. Any filter needing
      the full posting text will silently under-fire on Indeed, which is ~95% of
      volume. Nothing to fix in code — it is a source limitation worth remembering
      before promising a filter works.

## Open questions

- [ ] `drop_when_salary_unknown` is false, so listings with no stated salary bypass the
      floor entirely — 5 of the 15 current survivors are unpriced. Flipping it enforces
      a hard floor but loses every listing that does not publish pay, which on Indeed
      is a large share.
- [ ] Cloudstaff, Emapta and Pointwest are in exclude_companies but are staff-leasing /
      software services rather than call-centre BPO, and pay competitively for dev
      roles. By the "fine as long as they pay well" rule they arguably belong out.
- [ ] config.example.yaml ships min_salary_php 40000; the live config is 50000. Kept
      apart so personal numbers are not the project default.

## Scrape sources

- [x] `jobsift/sources/` adapter layer — opt-in, off by default, emits the same job
      dicts the extractor does so dedup/enrich/score/notify are unchanged.
- [x] onlinejobs.ph adapter. Notes: the site serves a listing-less page on the first
      request of a session and the populated one on a repeat, so one paced retry is
      required, not optional. It exposes no server-side filter drivable from a URL
      (`?jobkeyword=` is ignored; the form is JS-driven and there is no JSON API on
      either the classic or v2 site), so keyword filtering happens in the adapter,
      before the scoring call. On a live run: 90 listings read, 5 kept.
- [ ] Decide whether onlinejobs.ph earns its keep. It is VA/marketing-heavy — the one
      job scored end-to-end came back 15/100. Revisit after a week of real results.
- [ ] ToS note: clause 7.4 prohibits automated access without express permission.
      Enabled anyway as a deliberate personal-use decision (public pages only, 5s
      delay, no redistribution). Asking them for permission remains the clean path.

## Do NOT add to known_senders

Verified against real inbox subjects — these send mail but no job listings:

- `glassdoor.com` — Glassdoor *Community* forum threads ("Have you ever been fired?")
- `notifications.freelancer.com` — direct messages ("Re: Mandy") and promos
- `onlinejobs.ph` — profile-onboarding drip only; it is a profile-first marketplace where
  employers message you directly, so it will never emit parseable job alerts
