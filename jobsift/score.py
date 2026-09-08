"""Stage 3 — score a job against the resume.

skills (0-30) + experience (0-30) come from the LLM; salary (0-40) is computed
in code. Total is out of 100.
"""

from __future__ import annotations

import logging
import re

from .filters import currency_for, normalize_salary_php
from .utils import SNIPPET_CHARS, evidence_chars

logger = logging.getLogger(__name__)

# Inference is right on a full posting and wrong on a teaser. A posting that
# describes the work supports "they say Django, so they need Python". A hundred
# words of encouragement supports nothing - and asked to infer anyway, the model
# invents a plausible stack, matches the resume against its own invention, and
# returns a confident number built on it.
FULL_POSTING_RULE = (
    "When the job description doesn't explicitly list technologies, infer likely "
    "required skills from the description and match those against the candidate."
)

SNIPPET_RULE = """!! THIS IS A TRUNCATED ALERT SNIPPET, NOT THE FULL POSTING.
Most of what this employer wrote is missing, including - usually - the entire
requirements section. Judge ONLY what is actually written here.
- Do NOT infer a technology stack that is not stated. A snippet that says the
  work needs "problem-solving and determination" names no technology, and no
  skills match can be read from it.
- Where the text does not say, score LOW. An absent requirement is unknown, not
  satisfied, and scoring it as a match invents a fit that nobody claimed.
- A high skills_score must point at words the employer actually used. If you
  cannot quote them, the score is wrong.
- matching_skills may contain only skills the snippet itself names. A technology
  you inferred is not a match and does not belong in that list.

THE TITLE IS WEAK EVIDENCE, NOT NO EVIDENCE
"Full Stack Developer" does tell you the shape of the work, and a candidate who
does exactly that job is a better prospect than one who does not. Do not flatten
these to zero - rank them, but below anything actually evidenced.
- Where the ROLE the title names is work this candidate plainly does, score up
  to 12 of 30 on skills and up to 12 of 30 on experience. Not more: nobody has
  said what the stack is, what seniority they want, or what the work involves.
- Where the title names work the candidate does not do, score near zero.
- Say in `reasoning` that this came from the title, so the number can be read
  for what it is.
This ceiling is the whole point. A job whose posting NAMES a stack the candidate
has must outrank one that merely has a promising title - otherwise a title is
worth as much as a requirement, which is how a bare listing came to score 77."""

# The system prompt is deliberately free of anything that varies per job - the
# evidence rule rides along with the job in the user turn instead. That keeps
# this byte-identical across every call in a run, which is what prompt caching
# needs: it matches on the prefix, so a single per-job character in here would
# write a new cache entry every time and cost MORE than not caching.
#
# The resume is the bulk of it (~2,690 of the ~2,780 input tokens), and it is
# the same resume all run, which is what makes caching worth doing at all.
SYSTEM_TEMPLATE = """You are a job-resume matcher. You score how well a job listing matches the candidate's resume.

Each job says which EVIDENCE MODE applies. Both rules are below; follow the one
the job names, because it decides how much you are entitled to conclude.

=== EVIDENCE MODE: full ===
{full_rule}

=== EVIDENCE MODE: snippet ===
{snippet_rule}

Candidate Profile:
{resume}

PARTIAL MATCHES ARE PARTIAL, NOT ZERO
When the listing names several technologies and the candidate has some of them,
score the overlap. "PHP and React.js" against a candidate who has React is a
half match, not a miss - say so, and put the gap in missing_skills rather than
letting it drag the whole score down.
- Weight by how central each named technology is to the role, not by counting.
- A gap in ONE language, where the candidate has the rest of the stack and
  years of neighbouring work, is a learning curve and should cost a few points,
  not the category. An experienced React/Node engineer picks up PHP.
- Transferable fundamentals count when the technologies are genuinely adjacent
  (one backend language for another, one component framework for another). They
  do not count across a real divide - embedded firmware is not web work, and
  saying so is the point of the score.
- This is about technologies the employer ACTUALLY NAMED. It is not licence to
  assume a stack that was never stated.

SENIORITY IS A CLAIM ABOUT CAPABILITY, NOT A COUNTER
Judge the candidate against what the role needs DONE, not against the word in
its title. Job titles are unpriced on some boards - the same page can carry a
"Senior" role at $4/hour and another at ten times that.
- A modest years gap where the stack matches and the work is comparable costs a
  few points, not the category. Somebody who has owned a production system
  end-to-end has done the job a "Senior" title is asking for, whatever their
  years column says.
- Sole ownership of something real outweighs time served. Building and running a
  system alone is the thing seniority is usually a proxy for.
- The limit, and it matters: a requirement the resume cannot evidence AT ALL is
  still a gap. Leading a team of eight, a decade in a regulated domain, or a
  scale the candidate has never worked at are not covered by transferable
  fundamentals - put them in missing_skills and score them honestly. Softening
  a real gap produces the same fiction as inventing a match.

Score TWO categories only:
1. skills_score (0-30): how well the candidate's skills match what the job needs (0 = none, 30 = perfect).
2. experience_score (0-30): does the candidate meet the years/seniority and stack requirements (0 = unqualified, 30 = exceeds).

Location and salary are NOT scoring factors here.

Also report degree_required, judging ONLY what the listing actually says:
- "required" : a degree is stated as a hard requirement.
- "preferred": a degree is mentioned as preferred/advantageous, OR is offered
               with an alternative ("degree or equivalent experience").
- "none"     : the listing clearly asks for no degree.
- "unknown"  : the text does not say. Use this whenever you are unsure — the
               listing may be a truncated snippet. Never guess "required" from
               the seniority of the role.

Also report employer_name, taking ONLY what the listing itself states:
- The employer's own name where the posting gives it: "Company: Archer Wealth",
  "About Mogul:", "We are House of Nannies".
- "" everywhere else. A description of the business is not a name - "a
  veteran-owned pest control company operating in Florida, Georgia and Alabama"
  identifies nobody, and neither does "a Sydney-based private credit lender".
- NEVER infer a name from the product, the industry, the domain or the job
  title. A guessed employer is worse than a blank one: this is the field
  somebody searches on to find every posting by one company, and a plausible
  wrong answer is the one nobody checks.

Keep `reasoning` under 400 characters - one or two sentences naming the decisive requirement and whether the resume meets it. It is a note to a human, not an essay, and a long one truncates the JSON.

Return raw JSON only, no markdown:
{{"skills_score": 0, "experience_score": 0, "reasoning": "", "matching_skills": [], "missing_skills": [], "degree_required": "unknown", "employer_name": ""}}"""


def _compute_salary_score(job: dict, baseline: float = 70000.0) -> int:
    """Salary component: 40 pts max, 10 pts when the listing states no salary.

    Judged against `baseline` PHP/month (config: salary_baseline_php). The figure comes from the shared
    normalizer so an annual range, a peso symbol, a weekly rate or a "25k" style
    range are all read correctly — the previous version took max(numbers) and
    compared it to a monthly baseline, which scored "PHP 180,000 - 350,000 a
    year" (15k/month) as if it paid 350k a month.
    """
    value = normalize_salary_php(
        job.get("salary") or "", job_type=job.get("job_type") or "",
        default_currency=currency_for(job),
    )
    if value is None or value <= 0:
        return 10

    if value >= baseline * 2:
        return 40
    if value >= baseline:
        return 20 + round((value - baseline) / baseline * 20)
    return round(value / baseline * 20)


def priority_bonus(job: dict, keywords: list[str], bonus: int) -> tuple[int, list[str]]:
    """Extra points for the kind of work you actively want.

    The LLM scores fit against the resume, which rewards a job you *can* do —
    not necessarily one you *want*. This is the deliberate thumb on the scale.
    Matched whole-word against title, skills and description.
    """
    if not keywords or not bonus:
        return 0, []
    haystack = " ".join(
        [
            job.get("title") or "",
            " ".join(job.get("skills_required") or []),
            job.get("description_summary") or job.get("description") or "",
        ]
    ).lower()
    hits = [
        kw
        for kw in keywords
        if kw and re.search(rf"(?<!\w){re.escape(str(kw).lower().strip())}(?!\w)", haystack)
    ]
    return (int(bonus) if hits else 0), hits


def score_job(
    llm,
    model: str,
    job: dict,
    resume: str,
    baseline: float = 70000.0,
    priority_keywords: list | None = None,
    priority_points: int = 0,
) -> dict:
    available = evidence_chars(job)
    thin = available < SNIPPET_CHARS
    # Both rules live in the cached system prompt; the job names which one to
    # apply. Sending the rule text itself with each job would put ~640 tokens
    # outside the cache and pay full price for them on every call.
    system = SYSTEM_TEMPLATE.format(
        resume=resume, full_rule=FULL_POSTING_RULE, snippet_rule=SNIPPET_RULE,
    )
    user = (
        f"EVIDENCE MODE: {'snippet' if thin else 'full'}"
        f" ({available} chars of posting text available)\n\n"
        "Score this job:\n"
        f"Title: {job.get('title', '')}\n"
        f"Company: {job.get('company', '')}\n"
        f"Location: {job.get('location', '')}\n"
        f"Salary: {job.get('salary', '')}\n"
        f"Description: {job.get('description_summary') or job.get('description', '')}\n"
        f"Skills required: {job.get('skills_required', [])}\n"
        f"Requirements: {job.get('requirements', [])}"
    )

    # 800 was not enough once the prompt started asking for reasoning about what
    # the posting does and does not say: the JSON was cut mid-`reasoning`, failed
    # to parse, and every field fell back to its default. That is the dangerous
    # shape of failure here - `.get("skills_score", 0)` turns a truncated reply
    # into a confident 0/30, which is indistinguishable from "assessed as no
    # match". One job scored 33 instead of 60 that way.
    # 1500 was still short on a 5,000-character posting: the reply was cut
    # mid-`reasoning`, failed to parse, and a job that had genuinely scored
    # 22 and 21 was recorded as 0 and 0. Output is the expensive half of a
    # cached call, so the prompt also asks for a shorter `reasoning` - this
    # ceiling is the backstop, not the plan.
    data = llm.complete_json(model, system, user, max_tokens=2500, cache_system=True)

    # An empty dict means the call failed or the reply would not parse. Scoring
    # it as zeros silently buries a job that was never actually judged, so say so.
    if not data:
        logger.warning(
            "score: no usable response for %s @ %s - scored as unjudged, not as a poor match",
            job.get("title"), job.get("company"),
        )

    skills_score = int(data.get("skills_score", 0) or 0)
    experience_score = int(data.get("experience_score", 0) or 0)
    salary_score = _compute_salary_score(job, baseline)
    bonus, hits = priority_bonus(job, priority_keywords or [], priority_points)
    # Capped so a bonus cannot push a job past a perfect score.
    total = min(100, skills_score + experience_score + salary_score + bonus)

    degree = str(data.get("degree_required", "unknown") or "unknown").strip().lower()
    if degree not in ("required", "preferred", "none", "unknown"):
        degree = "unknown"

    if thin:
        logger.info(
            "score: %s @ %s judged on %d chars - snippet, not a posting",
            job.get("title"), job.get("company"), available,
        )

    return {
        # Carried so the number can be read for what it is. A 63 from a full
        # posting and a 63 from a teaser are not the same claim, and until this
        # was recorded nothing downstream could tell them apart.
        "evidence": "snippet" if thin else "full",
        "evidence_chars": available,
        # Distinguishes "judged and found wanting" from "never judged". Without
        # it a failed call reads as a legitimate low score forever after.
        "scored": bool(data),
        "degree_required": degree,
        # Read here rather than in `enrich` because enrich never runs for the
        # source that needs it: onlinejobs.ph sits in skip_link_domains (its own
        # adapter already fetched the page, paced to the site's Crawl-delay), so
        # this is the only call that ever sees those postings' text. Costs no
        # extra call anywhere - the scorer is already reading the whole posting.
        "employer_name": str(data.get("employer_name") or "").strip(),
        "priority_bonus": bonus,
        "priority_hits": hits,
        "skills_score": skills_score,
        "experience_score": experience_score,
        "salary_score": salary_score,
        "total": total,
        "reasoning": data.get("reasoning", "") or "",
        "matching_skills": data.get("matching_skills", []) or [],
        "missing_skills": data.get("missing_skills", []) or [],
    }
