# Job-alert sources

The discovery workflow is only as good as the alerts feeding it. **Point every alert
at the single Gmail address the workflow's Gmail Trigger watches** (or forward other
inboxes into it) — otherwise the watcher never sees them.

Set each alert to **daily or instant** frequency and use consistent search terms
(e.g. `remote full stack developer`, `React Node remote`, `AI engineer remote`).

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

The *Classify email* node has a `knownSenders` map (sender domain → source name). When
you add a board, add its domain so tagging + per-source stats stay accurate. If a
sender isn't listed, the node falls back to matching job keywords in the subject —
works, but less precise.

Not yet in the map (add these): `foundit.*`, `kalibrr.com`, `bossjob.ph`,
`remotestaff.*`, `otta.com` / `welcometothejungle.com`, `himalayas.app`,
`workingnomads.com`, `jora.com`, `adzuna.*`, `talent.com`, `jooble.org`.

## Noise control

More sources = more volume, but the workflow already **dedups by `title::company`**
(30-day memory) and **scores every job**, so raise the Telegram threshold (currently
`>= 60`) if alerts get chatty.
