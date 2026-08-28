"""Stage 3 — score a job against the resume.

skills (0-30) + experience (0-30) come from the LLM; salary (0-40) is computed
in code. Total is out of 100.
"""

from __future__ import annotations

import logging
import re

from .filters import normalize_salary_php

logger = logging.getLogger(__name__)

SYSTEM_TEMPLATE = """You are a job-resume matcher. You score how well a job listing matches the candidate's resume.

When the job description doesn't explicitly list technologies, infer likely required skills from the description and match those against the candidate.

Candidate Profile:
{resume}

Score TWO categories only:
1. skills_score (0-30): how well the candidate's skills match what the job needs (0 = none, 30 = perfect).
2. experience_score (0-30): does the candidate meet the years/seniority and stack requirements (0 = unqualified, 30 = exceeds).

Location and salary are NOT scoring factors here.

Return raw JSON only, no markdown:
{{"skills_score": 0, "experience_score": 0, "reasoning": "", "matching_skills": [], "missing_skills": []}}"""


def _compute_salary_score(job: dict, baseline: float = 70000.0) -> int:
    """Salary component: 40 pts max, 10 pts when the listing states no salary.

    Judged against `baseline` PHP/month (config: salary_baseline_php). The figure comes from the shared
    normalizer so an annual range, a peso symbol, a weekly rate or a "25k" style
    range are all read correctly — the previous version took max(numbers) and
    compared it to a monthly baseline, which scored "PHP 180,000 - 350,000 a
    year" (15k/month) as if it paid 350k a month.
    """
    value = normalize_salary_php(
        job.get("salary") or "", job_type=job.get("job_type") or ""
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
    system = SYSTEM_TEMPLATE.format(resume=resume)
    user = (
        "Score this job:\n"
        f"Title: {job.get('title', '')}\n"
        f"Company: {job.get('company', '')}\n"
        f"Location: {job.get('location', '')}\n"
        f"Salary: {job.get('salary', '')}\n"
        f"Description: {job.get('description_summary') or job.get('description', '')}\n"
        f"Skills required: {job.get('skills_required', [])}\n"
        f"Requirements: {job.get('requirements', [])}"
    )

    data = llm.complete_json(model, system, user, max_tokens=800)
    skills_score = int(data.get("skills_score", 0) or 0)
    experience_score = int(data.get("experience_score", 0) or 0)
    salary_score = _compute_salary_score(job, baseline)
    bonus, hits = priority_bonus(job, priority_keywords or [], priority_points)
    # Capped so a bonus cannot push a job past a perfect score.
    total = min(100, skills_score + experience_score + salary_score + bonus)

    return {
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
