# TODO

Working state as of 2026-09-07. **Running continuously** on the VPS
(`159.223.59.45`) as a systemd unit at `/root/jobsift`, every 300s, since
2026-09-06. `journalctl -u jobsift -f` to watch it.

## Waiting on external

- [x] **Working Nomads — confirmed end to end 2026-08-29.** Subscribed 2026-08-28
      (Development only, Daily, Anywhere+APAC+PH, Mid/Senior, Full-time + Contract).
      The first daily digest arrived from `hello@workingnomads.com`, subject "Your Daily
      Job Alert from Working Nomads". Dry run of the real email path: classified
      `workingnomads`, 5 of 5 jobs extracted with clean title/company/location/salary,
      all 5 enriched from the page, all `evidence: full` (1,313-2,162 chars) — the first
      email source ever to reach full evidence. 4 would save, 2 would alert, 1 was
      dropped for a stated degree requirement. Nothing was written and the email is
      still unprocessed, so the next real run will pick it up.
- [x] ~~Consider We Work Remotely and Remotive~~ — **Remotive done 2026-09-01** via
      `sources/remote_feeds.py`, no subscription and no email needed. Working Nomads,
      himalayas and jobicy came with it, and Working Nomads' API is not subject to the
      5-jobs-a-day digest cap.
      **We Work Remotely is NOT covered.** Its feed is RSS, not JSON
      (`weworkremotely.com/remote-jobs.rss`, 856 KB), so it needs a parser the adapter
      does not have. Left out deliberately rather than half-done — nodesk is the same
      shape if both are ever worth adding together.

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
- [x] **Jobstreet pages return 403 to automated fetches — routed around 2026-09-01.**
      `sources/jobstreet.py` reads the SEEK v5 search API instead of the page, and
      `ph.jobstreet.com` is now in `skip_link_domains` so enrich stops paying a request
      to be refused. The page is still 403; we simply no longer need it. Original note
      below for the record. With URLs now clean, the
      redirector resolves correctly to `ph.jobstreet.com/job/<id>`, but that page blocks
      us (a browser User-Agent alone does not help). Enrichment degrades gracefully, so
      Jobstreet jobs are scored from email content only. Low priority — the emails
      already carry title/company/salary.
      **Confirmed still 403 (Cloudflare) on 2026-08-30, and now solved a different
      way** — the SEEK v5 search API is public and returns structured PHP salary and
      work arrangement without touching the page. See *career-ops research → Jobstreet PH*.
- [ ] **Rewrite `docs/job-alert-sources.md`.** Still references n8n nodes ("the *Classify
      email* node") from before the Python rewrite. Should become a real setup guide:
      the three rules (email not in-app / correct address / domain in config), the
      filter-at-source principle, and the subscribe -> read From -> add domain loop.

## Deployment

- [x] **systemd unit installed and running, 2026-09-06.** At `/root/jobsift`, not
      `/opt`. `MemoryMax=220M` / `MemorySwapMax=256M` are deliberate: the same
      droplet runs the MDS Pro payroll API, SendIt and three containers on 1GB,
      and if anything ever runs away jobsift should be what the kernel kills
      rather than letting it choose. Steady peak is 85M, so the cap is not tight.
      `ProtectHome=read-only` means `data/` and `logs/` must stay in
      `ReadWritePaths` or the database cannot be written.
- [x] The local database was moved across rather than starting fresh, so the
      first pass did not treat the whole inbox as new. 366 jobs, 1,575 dedup
      keys, 202 processed uids, 1 applied confirmation.

- [ ] **Get onlinejobs.ph flowing again from the VPS — the best source, currently
      off there.** It is 108 of 366 stored jobs, every `evidence: full` row that
      is not a remote feed, and every score of 90+. The droplet gets a Cloudflare
      403 on it; a residential connection running the identical code and
      User-Agent is served normally, so it is the datacenter IP and nothing else.

      **This one is worth doing, and it is not the same question as Jobstreet.**
      onlinejobs.ph's `robots.txt` allows the job-search pages and asks for
      `Crawl-delay: 5`, which `sources/onlinejobs.py` already honours. Jobstreet
      *disallows* the paths it was being read through, which is why that source is
      off for good. Here the 403 contradicts the site's own published policy
      rather than restating it.

      The honest fix is to egress through **your own** home connection: a
      Tailscale exit node on the home machine, with only onlinejobs.ph routed
      through it. Your traffic, your address, doing what the site permits at the
      pace it asks for.

      What NOT to do: a residential proxy service. Renting somebody else's home
      IPs to look like a home user is impersonation, and it is what gets a block
      widened for everyone.

      Cost of the honest fix: the home machine has to be on. If that is
      unacceptable, the fallback is to run onlinejobs.ph at home occasionally and
      leave the VPS on the email half — but **never both at once**, because the
      dedup state is SQLite on one machine and two copies will double-score and
      double-alert.

- [x] **onlinejobs.ph no longer re-reads detail pages it already has — done
      2026-09-07.** `sources/__init__.py` builds an `is_seen` callback and was
      passing it to `jobstreet_api` only; `onlinejobs_ph` got `fetch_jobs(settings)`
      and so read a detail page for every title-gated listing, including ones the
      pipeline discards the line after they arrive.

      Measured on the first pass after the source came back: **106 of 112 detail
      pages, 95%, were listings already in `seen_jobs`** — five seconds of
      Crawl-delay each. That is not a first-run artefact, because the board leaves
      postings up for months, so the same pages would be re-read every pass.

      The hook is now passed through. It skips the DETAIL fetch only: all jobs are
      still returned, so the documented contract ("purely an efficiency signal —
      nothing is filtered by it") holds. `dryrun_seen_skip.py` proves that offline
      with a stubbed client, asserting on requests made rather than on a log line.

- [ ] **Back up `data/jobs.db` off the droplet.** It now holds the only record of
      which jobs have been seen, which emails are processed, and which
      applications the boards have confirmed. Losing it re-alerts everything and
      loses the applied history. Nothing backs it up today.

- [x] ~~VPS `config.yaml` has `first_run_lookback_days: 2`~~ — moot; the database
      moved across populated, so no backfill was triggered.

- [ ] The VPS `config.yaml` deliberately DIFFERS from the local one: `jobstreet_api`
      and `onlinejobs_ph` are both `enabled: false` there. Backed up as
      `config.yaml.bak`. Keep them in sync by hand when editing filters, or the
      two hosts will disagree about what gets dropped.

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

## Dedup key (normalised 2026-09-01)

`utils.job_key` — normalised `title::company`, replacing the exact-match version.

**The asymmetry that set how hard to normalise each half.** A missed duplicate costs
one extra line in Telegram. A wrong merge silently loses a real job and nothing
reports it. So:

- **Company is normalised hard.** Parentheticals go ("REALPAGE (PHILIPPINES) INC." ->
  `realpage`), then punctuation, then legal-form words stripped repeatedly from the
  END — inc / incorporated / corp / ltd / pte / gmbh / sdn bhd and the rest, plus
  `philippines` and `phils`, because a PH board is full of "<company> Philippines"
  arms of one employer. Trailing filler goes too, or "Accenture in the Philippines"
  keys as `accenture in the`.
- **Words that LOOK corporate but carry identity are deliberately absent** from that
  list: group, global, holdings, solutions, technologies, services. Without that,
  "ATOMIT Corp." and "ATOMIT Business Solutions Corp" would become one company, and
  "AS White Global" and "AS White" likewise.
- **Title is normalised only lightly** — case and punctuation, nothing else. Boards
  decorate titles with information they also put in structured fields ("(Hybrid)",
  "| WFH", "- Urgent"), and stripping those would merge "Senior AI Engineer - Hybrid"
  into "Senior AI Engineer" at the same employer, which may be two real openings.

Measured before wiring it in, over the 149 stored jobs plus 165 live Jobstreet
listings: **exactly 2 merges, both correct**, and replaying the 149 stored jobs
through the new key gives 149 distinct keys — it collapses nothing that was already
separate. 71 of 123 distinct employer names in the database normalise to something
shorter.

**Changing the key format is a silent un-dedup, so `store._migrate` exists.** A job
saved last week would otherwise reappear in tomorrow's alert email under a key nothing
recognises, and be saved and alerted a second time. The migration is guarded by
`PRAGMA user_version` (now 1), recomputes keys for every row in `jobs`, and carries
`first_seen` over from `created_at` so `cleanup()`'s 30-day window still measures the
job's real age. Tested on a copy of the live database: 149 jobs migrated, seen_jobs
149 -> 268, every saved job still recognised as seen, and running it twice changes
nothing.

Only jobs we actually SAVED can be migrated — `seen_jobs` also holds keys for jobs
that were FILTERED OUT, and those rows carry no title or company to recompute from.
They are left alone deliberately: such a job may be re-examined once, which is free
because the hard filters run before enrich and scoring, and `cleanup()` expires them
after 30 days anyway.

`dryrun_dedup.py` checks both directions — 6 pairs that must collapse, 6 that must
stay apart — then replays the whole database. No network, no LLM, nothing written.

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

## NEXT SESSION — onlinejobs.ph is the focus

Three commits landed: evidence-aware scoring, the onlinejobs source, the sign-off.
`config.yaml` is gitignored, so its settings are local only — `config.example.yaml`
carries them.

**Still switched off.** `scrape_sources.onlinejobs_ph.enabled: false`. Turning it
on is the next step, and the ToS question is unanswered — the scraper is
well-behaved (public pages, honest UA, 5s Crawl-delay, robots.txt allows all) but
that is robots compliance, not terms compliance.

The dry run to repeat: `.venv\Scripts\python.exe dryrun_onlinejobs.py python "full stack" --pages 1 --limit 12`
It writes nothing. Last run: 60 listings → 40 past the title gate → 11 would save,
6 would alert, all `evidence: full`, and it dropped a 60/100 job for stating a
degree requirement — the first time that filter has ever fired.

**Re-run it before enabling.** Those numbers predate the currency fix, so several
of the 12 jobs it filtered would now survive (`~2,400` was read as ₱2,400 and is
really $2,400 = ₱139,200).

### Open

- ~~**European salaries parse to junk.**~~ **Fixed 2026-09-01** — separators are read
  by digit count and ~30 currencies have rates. See *Remote feeds adapter → The
  separator bug*. It also ran the other way: "$31,2k" was being read as $312k.
- **Degree-drops are invisible.** A 82/100 Principal AI Engineer at $160-200k was
  binned for stating a degree requirement and never surfaced. Decide whether high
  scorers should alert with a warning instead of vanishing.
- **Working Nomads is live and enriching** (see above). Still not live, configured
  with zero jobs ever: foundit, kalibrr, bossjob, remotive, weworkremotely,
  virtualstaff — each needs an alert created on its site pointed at GMAIL_ADDRESS.
- **The Working Nomads digest carries no description at all** — just
  `Title - Company [URL] | Anywhere | Full Time | Mid Level | date` and sometimes a
  salary, ~1,100 masked chars for all 5 jobs. Every WN score therefore rests entirely
  on enrichment. It works today (`newsletter.workingnomads.com/jobs/go/...` redirects
  to the canonical `www.workingnomads.com/jobs/<slug>`, which returns full text), but
  if that page ever blocks us these jobs degrade to title-only, not to a snippet.
- **The free digest is capped at 5 jobs/day** — the mail itself says there are "10
  additional jobs that match your criteria in the premium account".
- The 149 existing rows keep their old inflated scores and read `evidence: unknown`.
  Deliberately not backfilled.

---

# career-ops research (2026-08-30)

Investigated `github.com/santifer/career-ops` after spotting it. Everything below was
**verified live on 2026-08-30**, not read off their README. Endpoints tested with curl,
counts are real responses. Nothing here is implemented yet.

## What it is, and the one thing to un-learn

MIT, created 2026-04-04, 69,277 stars, 13,093 forks, 333 open issues, last push
2026-08-29. 1,462 files, ~70 root `.mjs` scripts, ~80 board modules in `providers/`,
200+ test files, a Go TUI dashboard, Docker, 16 translated READMEs.

**It is not US-focused.** That was the wrong assumption going in. It is EU- and
Asia-heavy: `arbeitsagentur` + `scan-interamt` (German public sector), `deutschebahn`,
`rheinmetall`, `dassault`, `personio`, `softgarden` (DE); `justjoin`, `nofluffjobs`,
`solidjobs` (PL); `wttj` (FR), `manfred` (ES), `thehub` (DK), `vdab` (BE),
`landingjobs` (PT); `getonbrd`, `torre` (LatAm); `senjob` (SN); `jobbankca` (CA); and
for us the ones that matter — `jobstreet`, `glints`, `mycareersfuture`, `itviec`,
`careerviet`, `yourator`, `alibaba`, `tencent`, `meituan`. US is just `amazon`, `ibm`,
`higheredjobs` and the ATS vendors. The canonical mode files are `oferta.md`,
`aplicar.md` — the author is Spanish-speaking and US-only was never the design.

**Architecture, and the CLI/backend question.** Two layers with a hard line:

- **Deterministic Node scripts** (`scan.mjs` + `providers/`, `generate-pdf.mjs`).
  Public no-auth APIs, **zero tokens**, idempotent, appends to `data/pipeline.md`.
  This is a plain cron job and their `docs/AUTOMATION.md` ships the crontab, the
  launchd plist, and the `Register-ScheduledTask` PowerShell line.
- **The "brain"** — ~200 Markdown prompt files in `modes/`, executed by an AI coding
  CLI **with a human present**. `ARCHITECTURE.md`: *"the tool prepares and evaluates;
  the human reviews and clicks. It never submits applications on your behalf."*

So "run the CLI in the backend" is right, but only the first layer runs that way. The
one unattended AI step they document is a deliberately Read/Write-only triage prompt
via `claude -p` / `codex exec` that ranks pending rows on **title + location alone** —
no URL fetch, no subagents, one small prompt — writing a shortlist. Full evaluation
stays manual because it is the expensive part.

**The transferable idea: a free deterministic tier gating a paid LLM tier.** Our
`filters.py` hard-filter-before-enrich is the same instinct; we just never built the
tier below it. Today every job costs an LLM extract call because email is our only
front door.

## Geographic rule (decided 2026-08-30, SHIPPED 2026-09-01)

Applies to every source, new and existing. Sits with the hard filters in `filters.py`,
**before** enrich/score so a reject costs nothing.

1. **Location is Philippines** → keep, on-site/hybrid/remote alike.
2. **Location is not PH and the role is remote** → keep, *but only if a PH-based
   candidate is actually eligible* (see the trap below).
3. **Location is not PH and requires physical presence** → **discard.** On-site and
   hybrid both fail this; hybrid outside PH means being there.
4. Unknown/empty location → do not discard on geography alone; let scoring decide.
   Silently binning missing data is how we lost jobs before.

### Shipped 2026-09-01 — `filters.geography_check`

Four tiers, evaluated in order, structured fields only (`location`,
`candidate_location`, `country_code`), no LLM and no page fetch:

1. `exclude_locations` from config — never overridable, empty by default.
2. `HOME_TERMS` + `HOME_REGION_TERMS` — PH places, plus the regions we sit inside.
3. `FOREIGN_TERMS` — countries, regions and the timezone bands we cannot cover.
4. `OPEN_TERMS` — "worldwide", "anywhere": open, but naming no geography.

A listing carrying `candidate_location` — an eligibility field the feed filled in
itself — takes a strict variant of the same order, where anything unrecognised is a
restriction rather than missing data. See *Two things himalayas taught us* below.

Tier 3 above tier 4 is the career-ops lesson: **"Anywhere (working US business
hours)"** is a real Working Nomads string, and an allow-first order rescues it on
"anywhere". Tier 2 above tier 3 is a lesson the live data taught us afterwards —
the largest single bucket on Working Nomads reads **"Europe, North America, Latin
America, APAC"**, which a PH candidate *can* take. A plain block on "america"
binned 7 of 45 jobs before "apac" was promoted above it.

Deliberately no foreign *cities* in tier 3. A city in a location field is usually
the employer's office; a country or region is usually the restriction. Guessing
wrong on a city would drop a worldwide-remote job for having a San Francisco head
office. The cost is that an unrecognised place name falls through to rule 4 and is
kept — which is also what protects PH barangays the home list does not carry
("Ugong", "Fort Bonifacio").

One knob, `filters.geography.drop_when_unknown`, defaults to false and is rule 4 as
written. Flipped true it keeps only listings that positively say they are open, and
that is stricter than it sounds — "Salcedo Village" and "N/A" both go with it.

`dryrun_geography.py` re-runs all of it: 33 cases, the drop_when_unknown pair, every
stored job, then the four live feeds (himalayas cursor-paged). Measured 2026-09-01:

| feed | eligible | biggest reason for dropping |
|---|---|---|
| remotive | 9 / 19 (47%) | `usa` ×5 |
| workingnomads | 14 / 45 (31%) | `cet` ×13 |
| himalayas | 43 / 300 (14%) | `united states` ×158 |
| jobicy | 3 / 50 (6%) | `usa` ×39 |

remotive 9/19 and workingnomads 14/45 reproduce the hand-count of 2026-08-30
(9/19, 14/44) exactly, from independent code — the best evidence available that
the rule reads these fields the way a human does. **All 149 stored jobs survive
unchanged**, so nothing already collected is disturbed.

### Two things himalayas taught us, both worth knowing before step 4

**Its `limit` is a lie.** `?limit=100` returns 20 and says so in the response
(`"limit": 20`), `offset` is deprecated in favour of `?cursor=` (the feed's own
`comments` field announces this), `totalCount` is ~105,000, and results are
newest-first. So one page is *the last few minutes of postings*, not a sample. A
first pass over one page read 0/20 eligible and nearly wrote the source off; paged
properly by cursor it is 43/300, and 22 of 800 named the Philippines outright.
Quote no number from this feed that was not cursor-paged.

**An explicit eligibility field must be read strictly, and that is now how it
works.** Over 800 sampled jobs, 34 (4%) slipped through the hand-written
`FOREIGN_TERMS` — Costa Rica, Serbia, Morocco, Kazakhstan, Bosnia and Herzegovina,
the Cayman Islands, Macao, the Holy See. Enumerating all 195 countries would have
been the obvious fix and the wrong one. The right one: a feed that *fills in*
`locationRestrictions` is being explicit, so anything it names that is not ours
counts as a restriction, known country name or not. Free-text `location` keeps the
forgiving path, because there "Ugong" is a barangay and not a restriction. The
whole leak closes without a country list.

That puts a hard requirement on every adapter in step 4: **the eligibility field
goes in `candidate_location`.** Working Nomads' restriction lives in a field named
`location`, so an adapter that copies it across by name alone sends it down the
forgiving path and reopens the leak. The filter defends itself as far as it can —
strict mode reads the whole listing, so a country in `location` still blocks a job
whose `candidate_location` holds only a workable UTC offset — but it cannot infer
which field the feed meant to be binding.

Timezone restrictions are read too: `UTC+8 ±3`, so "CET (+/- 3 hours)" and
himalayas' `[-10, -9, -8, -7, -6, -5, 14]` both fail, and `[7, 8, 9]` passes.

### What survives the filter is not what you would guess (2026-09-01)

Splitting the kept jobs by WHY they were kept, over one sample of each feed:

| feed | kept | PH-only | APAC | open | clock |
|---|---|---|---|---|---|
| remotive | 9 / 19 | 0 | 3 | 6 | 0 |
| workingnomads | 14 / 45 | 1 | 8 | 5 | 0 |
| himalayas | 67 / 800 | 22 | 0 | 43 | 2 |
| jobicy | 7 / 100 | 3 | 0 | 4 | 0 |

"PH-only" means the feed states `locationRestrictions: ["Philippines"]` — remote,
but you must be *based here*. It is a location restriction and has nothing to do
with timezone; the timezone rule earns its keep only twice in 800 himalayas jobs
("clock"), so it is a correctness detail, not a source of volume.

**The PH-only slice is VA work.** Of himalayas' 22: Remote Customer Service Agent,
Odoo Bookkeeper, Freelance Ilocano Interpreter, Admitting Receptionist, Contact
Center Collections, Social Media & Admin Assistant, Patient Scheduling VA. Two of
the 22 are tech — a Mulesoft Software Engineer and a Microsoft L1 Support Engineer.
jobicy's three are SEO, video editing and growth marketing: none. This is the same
finding as *Scrape sources → onlinejobs.ph is VA/marketing-heavy*, from a completely
different direction — a foreign employer who restricts a remote role to the
Philippines is usually buying cost arbitrage on support work.

**The developer jobs are in the "open" bucket.** 43 of himalayas' 67 keeps are
Worldwide/Global, and that is where the tech-shaped titles sit (27 of 67 kept look
technical; only 2 of those are PH-only). So step 4's case for these feeds rests on
worldwide-remote listings, not on PH-restricted ones — and the PH-only slice would
mostly be dropped downstream by exclude_titles and min_salary_php anyway.


### The trap: "remote" does not mean "you can take it"

Measured on live feeds, 2026-08-30. Share of remote listings a PH candidate is
plausibly eligible for:

| feed | eligible / sampled | the giveaway field |
|---|---|---|
| remotive | 9 / 19 | `candidate_required_location` |
| workingnomads | 14 / 44 | `location` |
| himalayas | 1 / 5 * | `locationRestrictions` + `timezoneRestrictions` |
| jobicy | 1 / 5 * | `jobGeo` |

`*` small sample, `limit=5` — re-measure before trusting. **Done 2026-09-01** by
`dryrun_geography.py`: remotive 9/19, workingnomads 14/45, himalayas 43/300 (the
`limit=5` reading of 1/5 was a page of the newest postings, not a sample), jobicy
3/50. Step 7 below is closed.

So **roughly half to two-thirds of "remote" jobs are geo-locked** and worthless to us:
`"USA"`, `"Europe"`, `"Time zone: CET (+/- 3 hours)"`, `"United States"`. The keepers
read `"Worldwide"`, `"Global"`, `"Anywhere"`, `"Philippines"`, or a list containing
`APAC` / `Asia`. Every feed exposes this in a structured field — we do not need an LLM
to read it, and rule 2 is worthless without it.

career-ops hit this too and it is their open issue #2093: their `location_filter` only
reads the structured field, while the real restriction ("US-based candidates only")
often lives in the description body. Their answer is a second opt-in filter that reads
body text and cross-references `location.country`. Worth copying **after** the
structured filter, not instead of it.

Their filter ordering is also worth stealing outright — four tiers evaluated in order:
`block_hard` (never overridable) → `always_allow` (rescues multi-location postings) →
`block` → `allow`. It exists because `"Porto Alegre, Brazil"` passes an
`always_allow: ["Porto"]` and never reaches `block: ["Brazil"]`. Our equivalent
landmine: a whole-word `always_allow` on `"Manila"` would rescue nothing bad, but
`"Cebu"` / `"Davao"` inside a foreign string is the same class of bug.

## Verified live endpoints — all no-auth, all HTTP 200 on 2026-08-30

| source | endpoint | bytes | full description inline? |
|---|---|---|---|
| remoteok | `https://remoteok.com/api` | 406 KB | partial (~430 chars) |
| remotive | `https://remotive.com/api/remote-jobs` | 224 KB | **yes** (~5.5 KB) |
| workingnomads | `https://www.workingnomads.com/api/exposed_jobs/` | 196 KB | **yes** (~2.8 KB) |
| himalayas | `https://himalayas.app/jobs/api?limit=50` | 32 KB | **yes** (~3.7 KB) |
| jobicy | `https://jobicy.com/api/v2/remote-jobs?count=50` | 45 KB | **yes** (~12 KB) |
| arbeitnow | `https://www.arbeitnow.com/api/job-board-api` | 1.9 MB | **yes** (~11 KB) |
| 4dayweek | `https://4dayweek.io/api/jobs` | 28 KB | no (has `work_arrangement`) |
| weworkremotely | `https://weworkremotely.com/remote-jobs.rss` | 856 KB | RSS |
| nodesk | `https://nodesk.co/remote-jobs/index.xml` | 11 KB | RSS |
| echojobs | `https://echojobs.io/api/jobs` | — | **HTTP 429**, rate-limited |

**The descriptions come inline.** That kills the whole enrich stage for these sources:
no page fetch, no 403, no login wall, no second LLM call, and `evidence: full` for
free. This is the single biggest efficiency win available to us.

### Two of these are already on our waiting list — and we do not need the emails

- **Working Nomads**: our note above says the free digest is **capped at 5 jobs/day**
  with the rest behind a premium account. `exposed_jobs/` returned **44 jobs in one
  unauthenticated call**, descriptions included. The cap is a digest limitation, not
  a data limitation.
- **remotive** and **weworkremotely** are listed under "configured with zero jobs
  ever, each needs an alert created on its site". Both have live public feeds. No
  subscription needed at all.

## Jobstreet PH — solves an open TODO above

Our note says *"Jobstreet pages return 403 to automated fetches"* and we degraded to
email-only scoring. The fix: **do not fetch the page.** Jobstreet runs on SEEK
infrastructure and the SEEK v5 search API is public.

career-ops allowlists `id/sg/my.jobstreet.com` and `hk.jobsdb.com` with siteKeys
`ID-Main`, `SG-Main`, `MY-Main`, `HK-Main`. **`PH-Main` is not in their list and it
works** — tested and confirmed:

```bash
curl -sG 'https://ph.jobstreet.com/api/jobsearch/v5/search' \
  --data-urlencode 'siteKey=PH-Main' \
  --data-urlencode 'keywords=python developer' \
  --data-urlencode 'pageSize=30' --data-urlencode 'page=1'
# HTTP 200, totalCount: 793
```

GET with query params, not POST (a POST 404s). Every field we filter on arrives
structured, no LLM needed:

- `salaryLabel` → `"PHP 87,000 - PHP 130,000 per month"` — already PHP, already
  monthly. Feeds `min_salary_php` directly and sidesteps the European-separator bug.
- `workArrangements.displayText` → `"Remote"` / `"Hybrid"` / `"On-site"` — rule 3.
- `locations[].countryCode` → `"PH"` — rules 1 and 2.
- `listingDate` (ISO 8601), `advertiser.description` (branded company name),
  `companyName`, `title`, `teaser`, `workTypes`, `classifications`.

**Server-side filters that work** (measured, `keywords=developer`):
`workarrangement=3` → 760 remote · `=2` → 1251 hybrid · `=1` → on-site.
`sortmode=ListedDate` for freshness. Filtering server-side means we never download
the on-site jobs rule 3 would discard.

**Caveat, confirmed by probe:** only *search* is open. The detail page
`ph.jobstreet.com/job/<id>` returns **HTTP 403 behind Cloudflare** ("Just a moment..."),
and `/api/jobsearch/v5/job/<id>`, `/jobdetails/<id>`, `/api/job-details/v1/jobs/<id>`
all 404. So we get rich metadata but **no full description** — the opposite trade to
the remote feeds above. Score Jobstreet from metadata + `teaser`, mark
`evidence: metadata`, and do not pretend otherwise.

### Jobstreet PH adapter — shipped 2026-09-01

`jobsift/sources/jobstreet.py`, config key `scrape_sources.jobstreet_api`, **off by
default**. One unauthenticated GET returning JSON: no HTML parsing, no LLM extract
call, no page fetch. Emits `source: "jobstreet_api"` rather than `"jobstreet"`, so
rows found this way stay distinguishable from the ones the alert emails brought in
and the tier can actually be measured later.

Verified against the live API on 2026-09-01:

- `pageSize` accepts up to **100** (not just 30), paging is clean with zero overlap
  between pages, and the endpoint answers a request with **no User-Agent at all** —
  so identifying ourselves honestly is a courtesy, not a requirement.
- `workarrangement` takes **several codes at once**: `3` → 743 remote, `2,3` → 1,955,
  omitted → 4,230 (keywords=developer). Filtering server-side means the on-site jobs
  geography rule 3 would discard are never downloaded.
- Config defaults to `[remote, hybrid]` and six developer keywords.

**Two salary values on this field that neither the docs nor the research predicted**,
both found in one page of 30 remote listings:
- `"$500 – $600 per month"` — a PH board quoting USD. `normalize_salary_php` reads the
  `$` and converts at `usd_to_php`, giving 29,000/mo. Correct already, by luck of the
  existing currency handling rather than by design for this source.
- `"Consecutive days off, Remote"` — an employer typing benefits into the salary box.
  Returns None, which is "unknown", not "zero", so it is not dropped as underpaid.
Only 9 of 30 remote listings state a salary at all, so most arrive unpriced and
survive on `drop_when_salary_unknown: false`.

**Evidence is a snippet, and that needed no new code.** `evidence` is derived from
`evidence_chars()` against `SNIPPET_CHARS`, so a teaser lands as `snippet` and Telegram
already prints "scored on an N-char alert snippet". The earlier plan to invent an
`evidence: metadata` label was unnecessary — the honest machinery existed. Measured on
81 real listings, every single one came in at 111-378 characters, i.e. **not one
Jobstreet listing will ever reach `full`**. The consequence to remember:
`exclude_degree_required` cannot fire here, because the degree sentence lives in a
posting we are never shown.

`skip_link_domains` gained `ph.jobstreet.com` and **deliberately not** a bare
`jobstreet.com`. The bare form would also match the `url.jobstreet.com` redirector in
the alert emails, and following that is what resolves a 286-character tracking link
down to 37. Verified both ways: the API url is skipped, the email redirector is not.

`dryrun_jobstreet.py` mirrors the pipeline and writes nothing; `--no-score` stops
before the LLM so the whole check is free. It calls `enrich_job` too, which is how the
skip is *proved* rather than assumed ("enrich: 0/20 pages fetched"). Live run,
6 keywords x 1 page: 81 fetched, 75 unique, 61 past the hard filters. The 14 drops were
7 companies (Genpact, TTEC, Emapta, Cloudstaff, two "bpo"-in-name shops), 6 junior
titles, 1 under the salary floor. Scoring 5 of them gave one alert — WFH Python
E-commerce Automation Engineer, ₱115,000/mo remote, 75/100 — and 15-16/100 for the
roles that do not fit, which is snippet mode behaving correctly.

**Dedup gap this surfaced — fixed 2026-09-01.** The same job advertised as
"WeSupport Incorporated" and "WeSupport, Inc." came through twice in one run, and
"NightOwl Consulting Philippines, Inc" / "Nightowl Consulting" likewise. Pre-existing
and not specific to this source, but agency re-posts under name variants make
Jobstreet hit it constantly. See *Dedup key* below.

- `glints` (SEA incl. PH) uses a GraphQL endpoint, `https://glints.com/api/v2-alc/graphql`
  — not probed yet, worth a look for PH coverage.
- No `onlinejobs.ph` provider exists anywhere in their ~80. Our `sources/onlinejobs.py`
  has no counterpart.

## Remote feeds adapter — shipped 2026-09-01

`jobsift/sources/remote_feeds.py`, config key `scrape_sources.remote_feeds`, **off by
default**. Four public JSON feeds behind one adapter, because they are the same shape
of thing. Live run, defaults, 2026-09-01: **264 listings, 248 unique, 129 through
every hard filter, and 129/129 at `evidence: full`.** No other non-email source has
ever reached full evidence for a single job, let alone all of them.

| feed | returned | eligible | note |
|---|---|---|---|
| remotive | 19 | 9 (47%) | 19 is the WHOLE feed; `limit`/`search`/`category` are ignored |
| workingnomads | 45 | 13 (37%) | no salary field exists at all |
| himalayas | 100 | 11 (12%) | 20/page over ~105k, cursor-paged |
| jobicy | 100 | 100 (100%)* | `geo=philippines` filters server-side |

`*` **That 100% is close to circular and should not be read as a quality score.** We
asked their server for PH-eligible jobs and then our rule agreed they were PH-eligible.
The other three columns are where our filter is actually discriminating; on jobicy it
is a backstop, not a gate.

It is not vacuous, though — checked in both directions against the unfiltered feed on
2026-09-01. **False passes: 0** (nothing they returned would our rule reject). **False
misses: 0** (of the 7 eligible jobs in an unfiltered 100, all 7 appear in the
geo=philippines set, so their filter hides nothing we want). The composition explains
the rest: 80 Anywhere, 10 APAC, 6 Philippines, and 4 multi-region lists kept on "apac"
beating "usa" — the same tier-2-over-tier-3 ordering the Working Nomads case forced.

**What the geo filter actually buys is volume per request, not quality.** Unfiltered we
would keep 7 of 100 and discard 93 after downloading them; filtered, the same single
call returns 100 keepers. Roughly 14x more usable jobs for the same bandwidth, which is
the filter-at-source principle earning its place. (`geo=asia` is a 400; "philippines"
is the value that works.)

### Three bugs this found, all of which would have shipped silently

1. **`enrich` was not being skipped.** The adapter's whole argument is that the
   posting comes inline, but the pipeline calls `enrich_job` regardless — so it
   fetched all 129 URLs, collected 403s from remotive, followed one listing to a
   Google Form, and for any that succeeded would have paid an LLM call to re-derive
   text we already had *and overwritten it*. `skip_link_domains` cannot fix this:
   himalayas' `applicationLink` points at whatever ATS the employer uses, so there is
   no hostname list to write. Fixed with a `full_posting: True` flag the source sets
   on the job, which `enrich_job` honours before it looks at the URL. Now 0 fetched.
2. **Salary with no stated period was read as monthly**, which is right for a PH
   board writing "25k-40k" and catastrophic for remotive writing "$150k - $230k":
   a live *Head of Marketing* listing normalised to **₱8,700,000/month**, a figure
   that clears every filter and scores a flat 40/40. Now: peso figures stay monthly
   (nothing about the PH boards changes), and a non-peso figure above 10,000 with no
   period is read as a year's pay. That listing is now ₱725,000/month.
3. **Currencies other than USD had no rate at all.** Measured over 600 live listings:
   himalayas quoted CAD 3, EUR 3, PHP 2, INR 1, GBP 1; jobicy EUR 9, CAD 4, GBP 3,
   ARS 1, MXN 1, PHP 1. Each of those fell through to the source default and read
   ~50x low, then got dropped as underpaid. `CURRENCY_TO_PHP` now covers ~30
   currencies, overridable as `filters.currency_rates`.

### The separator bug, closed on the way past

The *European salaries parse to junk* item under **Open** is fixed. `€40.000` read as
40.0 and `250 000 zl` as 250, because `.replace(",", "")` assumed one convention.
Worse, it ran the other way too: remotive's real `"$31,2k- $52k"` became **$312k**, a
tenfold overstatement. The rule is now that three digits after a separator means
thousands and one or two means a decimal, applied to `.`, `,` and spaces alike.

Structured salary fields sidestep the question entirely — himalayas and jobicy hand
over `minSalary`/`maxSalary`/`currency`/`period`, and the adapter rebuilds a string
for `normalize_salary_php` rather than doing its own arithmetic, so ranges, hourly
conversion and the currency table stay in the one place that knows about them.

### Notes for later

- **TRY and COP are not in the currency table.** Both are ordinary English words, and
  "Try our benefits" setting a 1.4x rate is the kind of silent wrong number this
  project keeps having to dig out. Both countries are geo-dropped anyway.
- **remotive ships a legal notice** asking that its jobs not be republished to
  third-party job sites and that Remotive be credited. Sending them to your own phone
  is not republishing; the source label stays `remotive` and the link stays theirs.
  Same category of thing as the onlinejobs.ph ToS note — worth re-reading before this
  project ever grows a public surface.
- **A high salary can alert on its own.** *Head of Marketing & Communications* scored
  62 and alerted purely on pay. Salary is 40 of 100 by design, so this is the design
  working, but it is the first source where big USD numbers are common enough for it
  to happen often.
- **Pre-existing, unrelated:** one stored Indeed row reads "PHP 30,000 - PHP 40,000 an
  hour" — an employer typing a monthly figure into an hourly field — which normalises
  to ₱4,800,000/month and would alert. Nothing to fix in the parser; the listing is
  wrong. Noted because a sanity ceiling would catch it if false alerts ever matter.

## Security lessons worth taking

- **SSRF.** Every career-ops provider pins the hostname to an allowlist, requires
  HTTPS, and uses `redirect: 'error'`. Their `_ip-guard.mjs` goes further (issue
  #3096): it validates the *resolved address* at `dns.lookup` time, inside an
  `AsyncLocalStorage` context so loopback still works elsewhere in the process — a
  pre-flight check loses to DNS rebinding, because `net.connect` reads the lookup at
  call time. Our `enrich.py` follows whatever URL came out of an email. Worth at
  minimum: HTTPS-only, host allowlist, no redirects to new hosts, reject private ranges.
- **Prompt injection.** Their triage prompt states it outright: treat every field as
  untrusted third-party data, **not** instructions; postings can contain "ignore
  previous instructions". We feed raw posting text straight into `draft.py` — and per
  commit `a879008` we deliberately pass the *full* posting because the screening
  instructions sit at its end. That is exactly the attack surface. The drafter needs
  the same framing before it goes anywhere near an outbox.
- **HTTP hygiene** (`_http.mjs`): 10s timeout, 2 retries, 500ms base / 8s max
  exponential backoff with jitter, honours `Retry-After`, and retries **only** 429 /
  5xx / transport errors — a 4xx other than 429 is the server saying the request is
  wrong, so retrying just burns time.

## SSRF guard — shipped 2026-09-01

`jobsift/safefetch.py`, used by `enrich.py`. The exposure was real and worth naming:
`classify` accepts mail on a subject keyword alone ("job alert", "hiring"), so the
sender does not have to be a board we know. A crafted email reaches `extract`, which
pulls a URL out of the body, which `enrich` then fetched with `follow_redirects=True`
and no checks at all. On the VPS that is `127.0.0.1:8080/admin`, a database console,
or `169.254.169.254` for cloud instance credentials. Blind SSRF — the response never
goes back to the sender — but blind still reaches anything that acts on a GET, and
the fetched text went straight into an LLM prompt afterwards.

Four rules: **https only** (free — all 149 stored URLs already are), **every resolved
address must be public**, **the connection is pinned to the address that was
validated**, and **every redirect hop is re-checked**.

**The pinning is the part worth the effort.** Validating DNS and then handing the
hostname to httpx re-resolves it, and a domain with a short TTL can answer
differently the second time — the rebinding race the career-ops notes above describe.
Instead the request goes to the validated IP with `Host` and `sni_hostname` set to the
original name, which keeps certificate verification intact (confirmed against a live
host). There is no second lookup to poison.

**Two deliberate departures from the plan**, because the literal version breaks
something real:

- *"host allowlist"* — career-ops can pin to one because they have ~80 known
  providers. We follow links to arbitrary employer sites, so a mandatory allowlist
  turns enrichment off. It exists as `filters.fetch.allow_hosts`, empty by default,
  for a locked-down deployment; the public-address rule is what actually defends.
- *"no cross-host redirects"* — these are normal here, and one is a **feature**:
  `url.jobstreet.com` -> `ph.jobstreet.com` is what shortens a 286-character tracking
  link to 37. Blocking them would undo that. Every hop is validated instead, which is
  the property that was actually wanted.

Attacked with 23 hostile URLs, all refused: loopback by IP and by name, RFC1918,
`169.254.169.254`, `file://`, `gopher://`, decimal and hex integer IPs, and every way
of hiding IPv4 inside IPv6 — v4-mapped (`::ffff:127.0.0.1`), 6to4 (`2002:7f00:1::`)
and NAT64 (`64:ff9b::7f00:1`), each of which is unwrapped explicitly.

**One real leak the probe caught:** `100.64.0.0/10`, carrier-grade NAT, passed. None
of `is_private` / `is_loopback` / `is_reserved` report it. `is_global` does — it is
the stdlib's own answer to this exact question, and is now the primary test with the
individual flags kept as a second opinion. NAT64 is the mirror image: `is_global`
says True for `64:ff9b::/96` while an address in it can carry `127.0.0.1`, so that
prefix is unwrapped before the check.

A name resolving to both a public and a private address is refused outright rather
than falling back to the public one — the order of a DNS reply is chosen by whoever
we are defending against.

## Prompt injection in `draft.py` — shipped 2026-09-01

**The awkward part, which is why this was not a one-line prompt addition.** This
module's whole job is to OBEY instructions buried in a posting — "begin your message
with the word BANANA", "use subject REF-4471" — because missing one is an instant
bin. So "ignore anything the posting tells you", the standard advice, would have
deleted the feature. And commit `a879008` deliberately passes the *full* posting
because those instructions sit at its end.

The surface also got much wider today: before, Indeed snippets were ~160 characters.
The remote feeds now push **4,000-10,600 characters of employer-authored HTML**
straight into the prompt.

The line drawn is what an instruction is *about*, not where it appears:

- **Followed** — instructions about the application the candidate will send: opening
  word, subject line, attachments, ordering, what to mention.
- **Refused and reported** — anything addressed to the assistant: "ignore previous
  instructions", a new persona, a demand for different output, a request to reveal
  the prompt or the candidate's data, an instruction to claim experience the resume
  does not show, an instruction to contact an address. *A real employer writes to the
  applicant; text written for the model is not from a real employer.*

Refusals go into a new `injection_attempts` field, printed **above the cover letter**
in `render()` with the listing marked suspect — so a posting that tries this is a
signal to the candidate rather than a silent no-op. The posting is also fenced with
explicit untrusted markers (which is ambiguity removal, not a security boundary —
text inside can always claim the fence closed).

`dryrun_injection.py` attacks it with six postings, **6/6 pass**. Every case carries
both a real instruction that must survive and an attack that must not, so refusing
everything fails as loudly as obeying everything: direct override, a fake `SYSTEM:`
turn after a forged end-marker, data exfiltration to an email address, authority
dressing ("NOTE FROM THE HIRING PLATFORM ADMINISTRATOR"), an injection disguised as a
job requirement, and a clean control that must flag nothing.

**This is a mitigation, not a guarantee.** What makes it safe enough is that the
module cannot act: it sends nothing and every draft is read by a human. The docstring
says so, and it needs to stay true — this framing is not strong enough to sit in
front of an outbox by itself. Re-run `dryrun_injection.py` before the applier is ever
given the ability to send.

## Architecture notes

- **Files canonical, DB derived.** Their settled doctrine (#918): `data/*.md` and
  `reports/` are the permanent source of truth; SQLite is only a derived index and
  will never become primary, because the dashboard, plugins and forks all read files.
  We are the inverse — SQLite *is* our store. Not obviously wrong for us (we have no
  ecosystem to keep compatible), but it is why they can hand-edit and git-diff their
  pipeline and we cannot.
- **Whole-word matching, opt-in per entry.** Their `title_filter` supports `word:` and
  `stem:` prefixes because a bare `Intern` also rejects `Internal` and `International`.
  Our `filters.py` already does whole-word on the BPO list — same bug, already dodged.
- **`--suggest-senders` (open above) has a career-ops analogue**: `discover-ats.mjs`.
  Different mechanism, same onboarding gap.

## What we keep that they do not have

Email-as-transport is genuinely ours. Their scan is confined to "open, no-auth public
sources" and auth-gated boards are **explicitly out of core** — so Indeed, LinkedIn
and Foundit are invisible to them. Those are most of the PH market. We are also
actually unattended: theirs needs a human in a coding session to produce output; ours
runs on the VPS and pushes to Telegram.

The uncomfortable half: they have shipped the applier we have only written a README
for — `generate-cover-letter.mjs`, `application-answers.mjs`, ATS-safe CV templates,
`browser-extract.mjs`, PDF generation, a tracker. `modes/apply.md` is a working
version of `applier/README.md`.

## Next steps, in the order I would do them

1. ~~**Implement the geographic rule in `filters.py`**~~ — **done 2026-09-01**, see
   *Geographic rule → Shipped*. It is on by default (`filters.geography.enabled`),
   drops nothing already stored, and every adapter below now has the eligibility
   gate it needs. Adapters must populate `candidate_location` and `country_code`;
   an adapter that leaves them empty gets rule 4 and no protection at all.
2. ~~**Add a deterministic source tier** alongside email~~ — **done 2026-09-01** with
   step 3, which is the tier's first member. README rewritten: *Where each source
   should come from* now ranks public JSON API → email alerts → HTML listing, and says
   outright that the old "email beats scraping" line is true of tier 3 and false of
   tier 1.
3. ~~**`sources/jobstreet.py`**~~ — **done 2026-09-01**, see *Jobstreet PH adapter*
   below.
4. ~~**`sources/remote_feeds.py`**~~ — **done 2026-09-01**, see *Remote feeds
   adapter* below. Retires remotive; We Work Remotely stays open because it is RSS.
5. ~~**Harden `enrich.py`**~~ — **done 2026-09-01**, see *SSRF guard* below. Two of
   the four measures were implemented differently on purpose; the reasons are there.
6. ~~**Add the untrusted-data framing to `draft.py`**~~ — **done 2026-09-01**, see
   *Prompt injection* below. `dryrun_injection.py` attacks it: 6/6.
7. ~~Re-measure the eligibility percentages on full pages rather than `limit=5`~~ —
   **done 2026-09-01**, see the table under *Geographic rule → Shipped*.
   `dryrun_geography.py` re-runs the measurement whenever it is wanted.
