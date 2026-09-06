"""Does the confirmation detector fire on receipts and stay quiet on everything else?

No network and no API key: the cases below are the real templates, copied out of
a live inbox, plus the near-misses that share their senders. Run it and read the
output — the decoys matter more than the hits, because the cost of a wrong tick
is a job silently marked applied that never was.

    python dryrun_applied.py
"""

from jobsift.applied import detect, match_to_jobs

# (from, subject, body, expected (title, company) or None)
CASES = [
    (
        "indeedapply@indeed.com",
        "Indeed Application: Full-Stack Developer",
        "Your application has been submitted. Good luck!",
        ("Full-Stack Developer", ""),
    ),
    (
        "noreply@e.jobstreet.com",
        "Your application was successfully submitted",
        "Hi Kim,\nYour application for Full Stack AI Developer was successfully submitted "
        "to Lyfe\nLLC.\nEach employer's recruitment process is different.\n\n"
        "Similar jobs you might like\n\nFull Stack Developer\nSwift-up",
        ("Full Stack AI Developer", "Lyfe LLC"),
    ),
    (
        # Three full stops inside the employer's own name, and the sentence ends
        # the message. Terminating on the first one gives "KPMG R".
        "noreply@e.jobstreet.com",
        "Your application was successfully submitted",
        "Hi Kim,\nYour application for Full Stack Developer was successfully submitted "
        "to KPMG\nR.G. Manabat & Co..",
        ("Full Stack Developer", "KPMG R.G. Manabat & Co"),
    ),
    (
        # A title carrying the board's own decoration, wrapped mid-subject.
        "noreply@e.jobstreet.com",
        "KMC Solutions has viewed your application for Sr. Full Stack Engineer | 1x a Week Onsite",
        "You're getting noticed!",
        ("Sr. Full Stack Engineer | 1x a Week Onsite", "KMC Solutions"),
    ),
    # --- must not match: same senders, ordinary mail ---
    ("noreply@e.jobstreet.com", "Ebit Co., Ltd is still accepting applications -", "", None),
    ("noreply@e.jobstreet.com", "Kim, new activity in jobs you applied for", "", None),
    ("donotreply@jobalert.indeed.com", "Packaged/SaaS Application Engineer at Accenture. 10 more", "", None),
    ("donotreply@upwork.com", "New job: Build full-stack web application", "", None),
]

print("detector\n" + "-" * 78)
failures = 0
for sender, subject, body, expected in CASES:
    found = detect({"from": sender, "subject": subject, "text": body})
    actual = (found.title, found.company) if found else None
    if actual != expected:
        failures += 1
    print(f"  {'ok ' if actual == expected else 'FAIL'}  {subject[:52]:<52} -> {actual}")

# The ambiguity rule: a receipt naming no company must not pick between two jobs
# that share a title. Indeed never names one, and "Full-Stack Developer" is not rare.
print("\nmatching\n" + "-" * 78)
records = [
    {"job_title": "Full-Stack Developer", "company": "Acme", "url": "https://a/1"},
    {"job_title": "Full-Stack Developer", "company": "Globex", "url": "https://b/2"},
    {"job_title": "Backend Engineer", "company": "Initech", "url": "https://c/3"},
]
for label, confirmations, expect_matched in [
    ("title-only, two candidates", [detect({"from": "indeedapply@indeed.com",
        "subject": "Indeed Application: Full-Stack Developer", "text": ""})], 0),
    ("title-only, one candidate", [detect({"from": "indeedapply@indeed.com",
        "subject": "Indeed Application: Backend Engineer", "text": ""})], 1),
    ("title + company", [detect({"from": "noreply@e.jobstreet.com",
        "subject": "Globex has viewed your application for Full-Stack Developer",
        "text": ""})], 1),
]:
    matched, unmatched = match_to_jobs(confirmations, records)
    ok = len(matched) == expect_matched
    if not ok:
        failures += 1
    print(f"  {'ok ' if ok else 'FAIL'}  {label:<28} matched {len(matched)}, "
          f"unmatched {len(unmatched)}  {list(matched)}")

print("\n" + ("-" * 78) + f"\n{'all cases behaved' if not failures else f'{failures} FAILED'}")
