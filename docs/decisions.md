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
