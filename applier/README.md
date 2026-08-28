# Applier (planned)

Takes the top-scored jobs from the discovery pipeline and helps apply. **v1 pre-fills
and drafts; a person clicks submit.** No silent mass-applying — that is how you get
banned and how you send junk.

## Apply paths, and how they actually differ

Four paths, and they are not equally automatable. Ranked by tractability:

### 1. Email-apply — the best target
The posting says "email us at X with answers to these questions". No platform sits in
the middle, so there is no ToS to violate and no bot detection to trip. The whole task
is: read the posting, extract the questions, draft answers from `resume.txt`, show the
draft for review. This is the highest value for the least risk and should be built first.

**Screening questions are the real work here.** Many postings carry an attention check —
"start your message with the word X", "put SPECIFIC-PHRASE in the subject line". They
exist precisely to filter out mass applicants. A template misses them and gets binned;
a model reading the actual posting catches them. This is the single strongest argument
for doing this with an LLM rather than a form-filler.

### 2. Standard ATS — Greenhouse, Lever, Ashby
Clean, consistent forms, and a public READ API per board (verified: Greenhouse and
Ashby both return job lists unauthenticated). So the *posting* — full description,
screening questions — is fetchable without a browser, which is exactly what Indeed
denies us. Submission still needs a real browser, but drafting does not.

Indeed's "apply on company site" links usually land here, so this path matters more
than its share of listings suggests.

### 3. Indeed Apply ("Easily apply") — highest risk
The multi-step form with Continue buttons, backed by the resume already on the Indeed
profile. Automatable in principle with a logged-in browser, but:
  * it requires an authenticated session, so a ban costs the account and its saved jobs
  * Indeed actively blocks automation — plain HTTP gets 401 on /viewjob and 403 with a
    captcha on rc/clk links (measured)
  * it is the path where "apply to everything" is most tempting and most damaging
Treat as review-and-submit-by-hand, with the applier only drafting the answers.

### 4. Workday and bespoke company portals — deprioritise
Inconsistent, heavily scripted, often account-gated. Poor effort-to-return.

## The architectural constraint

Discovery reads email over IMAP and never needs a browser. The applier does, because
**the thing we most need to read is the thing HTTP cannot reach**: Indeed returns
401/403 to plain requests, so the full description, the apply path, and the screening
questions are all invisible to the current pipeline. A logged-in browser session (Claude
in Chrome) sees them normally.

Consequence: the applier is a *separate* stage with a *different* transport. It should
not be wired into the poll loop. Discovery stays cheap, headless and unattended;
applying is a deliberate, human-present action over a handful of jobs.

## A cheap win available right now

Indeed alert emails carry an "Easily apply" marker in the body, and `extract.py` is
currently told to ignore it as metadata. Capturing it as a boolean would let a job be
tagged with its apply path *at discovery time*, before any browser opens — so the
applier can sort by what is actually tractable instead of opening each one to find out.
Worth doing when the applier work starts.

## Design principles

- **Human-in-the-loop, always.** Draft and pre-fill; a person reviews and submits.
- **Never fabricate.** Answers come from `resume.txt` and the posting. If a question
  needs a fact that is not in the profile, leave it blank and flag it rather than
  inventing a number, a date, or an employer.
- **Resume and profile as config**, not hardcoded, so answers stay consistent.
- **Rate-limit yourself.** A handful of considered applications beats a hundred that
  read as spam — to the employer and to the platform's abuse detection alike.

## Open questions to settle before building

- Where does the applier read scored jobs from — SQLite, or the Google Sheet?
- Where does the human review happen — a small local UI, or the browser tab directly?
- How are drafted answers stored so a follow-up email can stay consistent with them?
