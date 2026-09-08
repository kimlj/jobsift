# Decisions, and the bugs behind them

Things that cost real debugging time, or that look wrong until you know why. Each
one is here because the obvious alternative was tried first and failed.

The counterpart to this file is `CONTRIBUTING.md`, which says how to change the
code. This one says what not to undo.

---

## The Google Sheet

**Rows are written at a computed range, never with `append_row`.** The tick-box
validation covers every cell in column A, which is enough to make the append API
treat the whole grid as the table: 167 backfilled rows landed at row 1001, under
a thousand blank ones, while the API reported them as present. Counting values
found them; only their row numbers showed the bug. `_next_row` reads the last
`job_title` instead.

**Row 1 is compared to `HEADERS`, not tested for emptiness.** A freshly created
worksheet does not reliably read back as empty in gspread 6, so
`if not get_all_values()` skipped the header and one tab had none at all.

**A column is never added by rewriting row 1.** The data under it does not move,
so new labels land over old values. `_migrate_headers` inserts and moves columns
*with their data* first, and only then writes the header row - by which point
writing it changes nothing. It refuses outright when it finds a column this
version does not know, because that column is not necessarily wrong and cannot
be placed.

**`--resync-sheet` exists because a column can change meaning without changing
name.** `scored_on` lost its character counts; the monthly figure moved to a live
exchange rate; the title became the link. None of that reaches rows already
written. A new user never notices - their first write is already current - and
the person who has been running it for a month sees a sheet half in each version.

**`draft_status` is derived every pass, not accumulated.** Every status used to
be written by whichever pass was running, assuming the write would land and
nothing would change after. Both fail: a pass killed between `queued` and the
draft leaves a queue that does not exist, and a tick removed later leaves a
request nobody made. Three rows were wrong this way, one for a draft that had
already been written.

**`applied` and `draft` are the user's columns. The program only reads them.**
Nothing clears a tick, ever - including after a draft is delivered. What stops a
re-draft every five minutes is the Drafts tab: a url already in it is answered.
`applied` is never *un*ticked either: the boards confirm applications, not their
absence.

**Draft requests are served at the start of a pass.** A pass spends minutes in
the scrape sources - onlinejobs.ph is paced to its `robots.txt` Crawl-delay,
about five seconds a page - and a tick sitting behind all of that is
indistinguishable from a tick that did nothing.

**The job title carries the link; the url column is hidden, not deleted.** It is
still the key every lookup matches rows on, and the values API returns a hidden
column just the same.

`utils.hyperlink` used to hold the label fixed because titles arrive from the
open internet and a cell beginning with `=` is executed. That is true, and the
conclusion was too broad: the danger is a title passed through as a cell's *whole
contents*, not a title used as a label inside a formula this file builds. Inside
a string literal, doubling `"` is the escape both Sheets and Excel use, so a
title carrying quotes ends the literal and starts another containing itself. A
title of `=cmd|calc!A1` comes out as a harmless link caption.

**`_a1()`, not `chr(ord("A") + i)`.** That stops working at column Z and `HEADERS`
is 27 long.

---

## Money

**The USD rate is fetched, not typed.** It was 58 for months; the live rate was
62.67. Eight percent, on a multiplier that decides what clears `min_salary_php`,
what the scorer sees, and how the whole sheet sorts.

`fx.py` holds the fetch, not `filters.py`. Filters is the one module with no
network and no I/O, which is what lets two dry-run scripts run for free and makes
the parser testable at all.

**`set_usd_rate()` exists because rebinding the constant does nothing.**
`CURRENCY_TO_PHP` is built at import and captures `USD_TO_PHP` by value. The
first live run logged 62.67 and computed the entire column at 58.

**A stale rate beats no rate.** Cache, then config, then the built-in. A currency
API being down is not a reason to stop reading the inbox. The result is
range-checked: a provider changing shape and returning a different number would
be worse than yesterday's rate.

**`peso`, `pesos` and `piso` resolve to PHP.** `90000-150000 Peso` had no code and
no symbol, fell through to the source default of USD, tripped the annual
heuristic, and came out at ₱435,000 a month instead of ₱90,000. This is the
opposite call to the `TRY` / `COP` exclusion in the same file: those are ordinary
English words and this one is not.

**The normalised figure is a number with a display format, not text.**
`"₱80,000"` as a string sorts 100,000 before 80,000 and cannot be summed. The
as-written column is forced left because Sheets right-aligns a bare `1600` - it
reads as a number - which made the column ragged for no visible reason.

---

## Drafting

**Every draft is a paid model call**, on the scoring model, about $0.03. Nothing
drafts by itself. Three per pass, so a careless tick of forty rows costs three
calls and a log line.

**A draft is refused below 400 characters of posting.** Two ticked rows came back
with an empty salary answer and a letter written from the title, because both
saw about 155 characters - and they look exactly like real drafts in the tab.

The floor is measured, not chosen. Across the stored jobs: onlinejobs.ph carries
a median of 3,013 characters and jobicy 7,016, while indeed carries 155,
jobstreet's search API 182 and linkedin 3. Nothing real sits between those.

**Jobstreet is read through email alerts, not through its API.** Its
`robots.txt` carries `Disallow: /graphql` and `Disallow: /api/jobsearch/` - the
endpoint that returns the full advertisement, and the search endpoint the scrape
source used. Both are off.

That cost a day. The reasoning went: the job page 403s, three REST shapes 404,
therefore the posting is unavailable - which was then correctly identified as too
strong, because the page is a JavaScript app whose own GraphQL call had never
been tried. It answered, anonymously, with 4,302 characters against a
182-character teaser. What nobody did in either round was open `robots.txt`.

**Reachable is not allowed.** An endpoint answering an anonymous request says
nothing about permission; the permission is written down, in one file, at a
fixed path. Check it before deciding an endpoint is available, not after
deciding it is unavailable.

**Storing the fetched page text does not fix most rows, and a commit here once
implied it would.** `enrich` was keeping the page it had already fetched, which
was described as making drafts "better automatically" as new jobs arrived. It
does not, because `enrich` returns early through `_normalize_no_link` for
anything in `skip_link_domains`, and the boards that matter are all on it.

What actually reaches `evidence: full` is the remote JSON feeds, which carry the
whole posting inline and need no fetch at all, and onlinejobs.ph, whose own
scraper reads the page — the reason it is on the skip list. Everything arriving
by email stays at `snippet` or `title`, and no future run changes that.

So for a job worth applying to on those boards, `--draft <id> --posting FILE` is
not a fallback. It is the method.

**Indeed cannot be fixed this way.** No equivalent API, and the way past its
blocking is a headless browser pretending to be a person. Its rows say
`title` in `scored_on` and ask for `--posting`.

**A 403 from a datacenter is not always the same thing as a 403.** Deployed to a
DigitalOcean droplet, onlinejobs.ph, Jobstreet's search and Jobstreet's GraphQL
all returned 403 for requests a residential connection is served normally. The
two are not equivalent:

* Jobstreet **disallows those paths in `robots.txt`**. The 403 restates a policy
  they publish, and routing around it would be evading a decision stated twice.
* onlinejobs.ph **permits** the job-search pages and specifies `Crawl-delay: 5`,
  which this program already honours. There the 403 is a blunt anti-bot layer
  that contradicts the site's own stated policy.

Even in the second case, a residential proxy service is the wrong answer: renting
somebody else's home IPs to appear to be a home user is impersonation, and it is
what gets a block widened for everyone. Egressing through *your own* connection
is defensible; buying a disguise is not.

**Resolved 2026-09-07 by moving the host, not by tunnelling.** Egressing through
your own connection was scoped properly first: Tailscale to link the droplet to a
machine at home, and a per-source proxy so only onlinejobs.ph took that path rather
than a full exit node, which would have sent the LLM and IMAP traffic through a
house too. It works. It was still rejected, because every version of it needs a
machine at home to be switched on, which is the same requirement as simply running
the pipeline there, with a tunnel, a proxy service and a second config on top. When
the workaround's precondition is identical to the simple answer's, the workaround is
not buying anything.

So the whole pipeline runs at home now. The honest cost is that it only runs while
that machine is on.

**The uptime that costs is smaller than it looks, and it is not a hardware
question.** Measured over the 108 stored onlinejobs.ph rows, 41% were posted
between 11pm and 8am — real flow, in the window a laptop is typically shut. The
obvious reading is that a small always-on box would catch them. It would not catch
anything actionable: this program alerts, and a person applies. An alert delivered
at 3am is read at 8am, which is when the same job would surface on a laptop that
starts at 8am. The overnight 41% is the size of the prize an **applier** would
unlock, not a Raspberry Pi. Buy uptime only after something can act on it.

(The posting timestamps are stored, under `timestamp` rather than `posted` —
`_build_record` fills that field from the board's own date, falling back to now.
Searching the stored rows for a key called `posted` finds nothing and invites the
wrong conclusion.)

**`scored_on` is in the sheet because a score and the evidence behind it are read
together or not at all.** 50 of the first 119 rows at 60+ were scored on a
snippet or the title alone, and an 89 from a title looked exactly like an 89 from
three thousand words. The character count was dropped: the column is scanned down
a list, and 182 against 155 is not a distinction anyone acts on.

**Salary answers come from the employer's own band.** `profile.yaml` holds one
expectation for every application, which makes it the figure for an employer who
has said nothing. One who publishes ₱100,000-150,000 has told you their budget,
and answering ₱70,000 into that field gives away the difference *and* prices the
candidate below the level being hired for. The midpoint, not the top: defensible
without negotiating, and it leaves somewhere to go. Computed in Python, so it
cannot drift with the model.

**Cover letters run 150-220 words in 3-5 paragraphs.** A first draft came back at
280 in one block, and every excess word was resume - a list of seven dashboard
surfaces, a technology roll-call. The reader has the resume open beside the
letter. Keep what it cannot carry: a decision, a judgment, where the
responsibility sat.

---

## Application confirmations

**The tick comes from the boards' own receipts, not from their websites.** An
"already applied" badge lives behind a logged-in session, so reading it means
driving a browser with the user's cookies against a page whose markup is not a
contract. The receipts are already in the inbox.

**Rules are written from real messages, and the near-misses are test cases.** A
job alert titled "Application Engineer" and a "still accepting applications"
nudge both reach the detector and both must fail. All four synthetic cases passed
while the first real receipt failed, which is the argument for keeping real
messages in `dryrun_applied.py`.

**Both regex groups are length-capped.** The live template puts
`%%str_to_replace_open_tracking%%` and a tracking URL where the terminator list
expected prose, so the company group could not close, and an open `.+?` on the
title backtracked across the whole message until a *second* copy of the same
sentence let it finish. The receipt was detected and matched nothing, with a
900-character title. A pattern that cannot find its fields should fail, not
stretch until it finds something.

**A title-only receipt matching two stored jobs is reported, not guessed.** Indeed
never names a company and "Full-Stack Developer" is not rare. A tick on the wrong
row hides a job that was never applied to, silently.

**onlinejobs.ph has no rule.** Not because it sends nothing - because the inbox
it was checked against had never applied there, so there is no evidence either
way. Inventing a pattern for mail nobody has seen is how a detector starts
matching the wrong thing.

---

## Two rules that keep catching things

**Verify from the outside, not from the config.** The sheet reported 83 and 141
rows while both tabs looked empty; the API was right about the count and wrong
about what a reader would see. Check what the reader gets.

**When a file says something cannot be done, check the evidence, not the
conclusion.** Twice in one session a comment stated a limit truthfully and drew
too much from it: Jobstreet's posting was reachable, and a job title could safely
label a link. Both were written by someone who had tested the obvious thing and
stopped.

---

## Listings that closed

**A closed posting is detected by its absence from search, not from its own
page.** onlinejobs.ph keeps serving the detail page of a closed job, and to a
logged-out reader that page is identical to an open one: same
`#job-description`, same "Please login or register as jobseeker to apply", HTTP
200 either way. Diffing a closed posting against an open one found 21 meta tags
each, 8 scripts each, the same inline variable names, and no `status`, `active`
or `expired` field anywhere. The only differences were per-job values:
`csrf-token`, the description, `employerId`, `jobId`. The "This job has been
closed" banner renders only for an authenticated session, and this adapter never
authenticates.

**Absence only counts after a SHORT page.** The first live run marked two live
jobs gone. "Software Engineer" and "AI / Full Stack Developer" are generic
enough to fill all 30 slots on page one, so both postings were simply further
down, and page one alone could not see them. A full page means there may be
more; only a page that comes back short proves the result set has ended. A title
that stays full for `DEFAULT_MAX_PAGES` returns None rather than a verdict.
"Web Automation Engineer" returned 4 results, which is why the closed posting it
was measured on gave a real answer.

**`still_listed` returns None, not False, when it cannot tell.** Three states,
because two would force a guess into one of them, and the guess that costs
something is retiring a live job off the shortlist. `mark_delisted` is only ever
called on a definite False.

**A delisted row is moved to the Closed tab, never deleted.** It is a real job
that was really scored, and the `applied` and `draft` ticks are the user's, so
the whole row travels with them. Two things the move has to get right: it reads
with `value_render_option="FORMULA"`, because the job title carries the link and
the display value is only a caption; and it deletes from the bottom up, because
removing a row renumbers everything under it.

**The moved row's `status` cell is restated as `delisted`.** It was written at
ingest and still said `new`. A row filed under Closed that calls itself new is
the sheet disagreeing with itself.

---

## Naming the employer

**`employer_name` is read by the SCORER, not by `enrich`.** Enrich is the
extraction stage and looks like the right home, but it never runs for the source
that needs this: onlinejobs.ph is in `skip_link_domains`, because its own adapter
has already fetched the detail page paced to the site's Crawl-delay, and a second
undelayed request for the same page is the thing that pacing exists to prevent.
So `enrich_job` short-circuits, and the scoring call is the only one that ever
sees those postings' text. Putting it there costs no extra call anywhere.

**It is a new column, not a better `company`.** `company` is load-bearing:
`job_key` falls back to the URL slug when it is empty, which is what keeps two
same-titled onlinejobs postings from collapsing into one entry. Writing a name
into it would re-key every stored row and re-alert the lot - the exact failure
`SCHEMA_VERSION` exists to prevent. The two columns also answer different
questions: what the board handed over, and what the posting called itself.

**The model is told to abstain, and does.** Measured on five postings: Mogul,
Archer Wealth and AI L3 Tech came back named; "Founding Full-Stack Developer",
which says "I'm building Scout", came back empty because Scout is the product;
and "veteran-owned, multi-state pest control company (Florida, Georgia,
Alabama)" came back empty because that describes a business without identifying
one. A guessed employer is worse than a blank one - this is the field somebody
searches to find every posting by one company, and a plausible wrong answer is
the one nobody checks.

**`employer_id` is taken from the page instead of the prose.** The detail page
carries no structured employer name - no JSON-LD, no meta tag, nothing in the
markup - but it does assign `employerId` in an inline script (794885 for Archer
Wealth, 918549 for Mogul). That is a stable identity the text cannot give: two
postings by one employer share it whether or not either writes the company down.
Database and CSV only; it earns no sheet column.

---

## Application receipts

**The receipt does not come from the board you applied through.** Two
applications made from Working Nomads listings were confirmed by Greenhouse and
by JazzHR, because Working Nomads hands off to whatever ATS the employer uses
and only ever mails a newsletter itself. `CONFIRMATION_SENDERS` could never have
caught either, and no list can: there are dozens of ATS vendors and employers
also write from their own domains.

**So the ATS rules carry no host and match on phrasing alone.** That is only
safe because of a property `detect` already had: every pattern demands a phrase
that only a confirmation contains. "X is still accepting applications" and a job
titled "Application Engineer" both fail. A rule built on keywords could not be
turned loose on a whole mailbox; one built on "we received your application for
X role at Y" can. Measured over two days of a live inbox - 135 messages, most of
them job alerts - the sender-agnostic rules fired six times and every one was a
real confirmation.

**`match_to_jobs` is where the safety actually lives.** A phrase that matched on
any sender only ticks something if the employer it names is already a stored
job, and only if exactly one of them is. The same "only when unambiguous" rule
that already guarded title-only confirmations now guards company-only ones.

**Company-only confirmations had to become legal.** JazzHR's subject is "Kim,
we've received your resume" and names no job anywhere; the body says only
"Thank you for your interest in joining Bamboo Works." `detect` used to end with
`if not title: continue`, which would have thrown that away after matching it.

**Short company names need the sender's domain to agree.** Of 190 stored
companies, 17 normalise to a single word of six characters or less - "atos",
"quora", "sgs" - and those turn up in mail having nothing to do with an
application. A longer or multi-word name is distinctive enough to stand alone.

**`--scan-applied` searches by phrase as well as by sender.** Its 90-day reach
over All Mail is why the sender list still exists at all: it keeps the download
to a few dozen messages. A host-less rule has no sender to search on, so the
phrase is what finds it. These only decide what is DOWNLOADED - `detect` still
has to match and `match_to_jobs` still has to find the job - so a loose phrase
costs bandwidth, not a wrong tick.
