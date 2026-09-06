# Job-alert sources

Discovery is only as good as the alerts feeding it. **Point every alert at the
single Gmail address configured in `.env`** (or forward other inboxes into it) —
otherwise `jobsift/gmail.py` never sees them.

Set each alert to **daily or instant** frequency and use consistent search terms
(e.g. `remote full stack developer`, `React Node remote`, `AI engineer remote`).

## Do not subscribe to a board that has an adapter

Five of the boards below are now read directly and should **not** be subscribed to
by email, because the adapter gets strictly more than an alert does:

| Board | Adapter | What email would lose |
|---|---|---|
| Jobstreet PH | `sources/jobstreet.py` (SEEK search API) | Its alert emails carry a teaser, and the job page is 403 behind Cloudflare — email-only scoring worked from whatever the alert happened to quote. The API hands over salary in pesos-per-month, work arrangement and country already structured. |
| Remotive, Working Nomads, Himalayas, Jobicy | `sources/remote_feeds.py` | These feeds include the **whole posting**, so there is no page to follow and no LLM extract call at all. |

`onlinejobs.ph` is in the PH list below for completeness only — it emits no job
alert emails whatsoever, which is why it has an HTML reader (`sources/onlinejobs.py`)
rather than a subscription. All adapters are **off by default**; enable them under
`scrape_sources` in `config.yaml`.

## Already subscribed

Indeed · LinkedIn · Foundit · Jobstreet

## Recommended additions (full-stack / AI + remote/PH profile)

### Remote tech — best signal
- **Otta / Welcome to the Jungle** — tailored tech-job emails, high quality
- **Wellfound** (AngelList) — startup roles, job-match emails
- **We Work Remotely**, **RemoteOK**, **Remotive**, **Himalayas**, **Working Nomads**
  (Working Nomads is email-first by design)

### Philippines / SEA
- **Kalibrr** — daily alerts, big in PH
- **BossJob** — PH-focused, AI matching
- **Remote Staff** (remotestaff.ph), **OnlineJobs.ph**, **VirtualStaff.ph**
- **Jora** — SEEK's SEA aggregator, good PH coverage

### Aggregators — most coverage per signup
- **Google for Jobs** — search jobs on Google → "Turn on job alerts" (broadest net)
- **Adzuna**, **Talent.com**, **Jooble**

### Freelance
- **Upwork** saved-search alerts · **Contra** · **Freelancer.com**

**Starter set:** Otta + Wellfound + We Work Remotely + Kalibrr + BossJob + Google for Jobs.

## Teach the classifier new senders

`config.yaml` has a `known_senders` map (sender domain → source name), read by
`jobsift/classify.py`. When you add a board, add its domain so tagging and
per-source stats stay accurate. If a sender isn't listed, `classify` falls back to
matching job keywords in the subject — that works, but it is less precise, and it
is also why `safefetch` exists: keyword-only matching means *anyone* who can put
mail in the inbox can hand this program a URL to fetch.

(The wording here used to describe the *Classify email* node of the original n8n
workflow this replaced. The running program is the Python package.)

Mapped today: `indeed.com`, `jobs-noreply@linkedin.com`, `foundit.com`,
`jobstreet.com`, `kalibrr.com`, `bossjob.ph`, `remotive.com`, `weworkremotely.com`,
`workingnomads.com`, `onlinejobs.ph`, `virtualstaff.ph`.

Still unmapped if you subscribe to them: `remotestaff.*`, `otta.com` /
`welcometothejungle.com`, `jora.com`, `adzuna.*`, `talent.com`, `jooble.org`.

Note that `jobstreet.com`, `remotive.com` and `workingnomads.com` are mapped only
so that mail already arriving is tagged correctly — per the table at the top, do
not create *new* subscriptions for boards that have an adapter.

## Noise control

More sources = more volume, but every job is **deduped and scored** before it can
reach you, so raise the Telegram threshold in `config.yaml` if alerts get chatty.

Dedup is `jobsift/utils.py:job_key`, which normalises title and company first —
plain `title::company` treated "Senior Python Developer" at "Acme Inc." and
"Senior Python Developer" at "Acme, Inc" as two different jobs, and one board
reposting the same opening with a reworded title as two more.

Volume is also cut *before* an LLM sees anything by the hard filters in
`jobsift/filters.py` — geography, salary floor and ceiling, posting age, title
exclusions. Adding a source costs far less than the raw job count suggests.
