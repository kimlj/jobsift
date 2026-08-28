# Applier (planned)

Takes the top-scored jobs from the discovery workflow and helps apply — using
**Claude in Chrome** to drive a real browser: open the application, fill what it can,
draft answers to screening questions, then **pause for you to review and submit**.

## Design principles

- **Human-in-the-loop, always.** v1 pre-fills and drafts; a person clicks submit. No
  silent mass-applying — that's how you get banned and how you send junk.
- **Go where forms are standard.** Best targets, roughly in order of tractability:
  1. **Email-apply jobs** — many remote boards list an apply-to address; the pipeline
     can draft the application email for review.
  2. **Standard ATS** — Greenhouse, Lever, Ashby have clean, consistent forms.
  3. **LinkedIn Easy Apply** — possible but higher ban/ToS risk; treat carefully,
     lower priority.
  4. **Workday** — painful and inconsistent; deprioritize.
- **Resume + profile as config**, not hardcoded, so answers stay consistent.

## Open questions to settle before building

- Where does the applier read scored jobs from — the Google Sheet, or a shared DB?
- How much does it auto-fill vs. ask? (screening questions, salary expectations, etc.)
- Where does the human review happen — a small local UI, or directly in the browser
  tab Claude opens?

Nothing here yet — this is the next phase after discovery is running on the VPS.
