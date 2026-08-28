"""Stage 3 — score a job against the resume.

skills (0-30) + experience (0-30) come from the LLM; salary (0-40) is computed
in code. Total is out of 100.
"""

from __future__ import annotations

import logging
import re

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


def _compute_salary_score(job: dict) -> int:
    """Port of the salary heuristic: 40 pts max, 10 pts default when unknown.

    PHP roles are judged against a 70k baseline; USD hourly against $15/hr,
    USD fixed/Upwork against $500.
    """
    salary_str = (job.get("salary") or "").replace(",", "")
    if not salary_str.strip():
        return 10

    url = (job.get("url") or "")
    is_upwork = "upwork.com" in url
    nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", salary_str)]
    if not nums:
        return 10

    salary_value = max(nums)
    salary_min = 0
    low = salary_str.lower()

    if re.search(r"php", low):
        salary_min = 70000
    elif "$" in salary_str:
        if "fixed" in low or ("hr" not in low and "hour" not in low and "-" not in salary_str and is_upwork):
            salary_min = 500  # fixed-price
        else:
            salary_min = 15   # hourly
    else:
        return 10

    if salary_min <= 0 or salary_value <= 0:
        return 10
    if salary_value >= salary_min * 2:
        return 40
    if salary_value >= salary_min:
        return 20 + round((salary_value - salary_min) / salary_min * 20)
    return round(salary_value / salary_min * 20)


def score_job(llm, model: str, job: dict, resume: str) -> dict:
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
    salary_score = _compute_salary_score(job)
    total = skills_score + experience_score + salary_score

    return {
        "skills_score": skills_score,
        "experience_score": experience_score,
        "salary_score": salary_score,
        "total": total,
        "reasoning": data.get("reasoning", "") or "",
        "matching_skills": data.get("matching_skills", []) or [],
        "missing_skills": data.get("missing_skills", []) or [],
    }
