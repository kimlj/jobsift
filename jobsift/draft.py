"""Draft an application for one scored job: cover letter + answers, for review.

Never sends anything. Output is text you read, edit and use yourself.

Two rules govern the whole module:

* **Read the entire posting.** Employers routinely bury a compliance instruction
  in the last lines — "start your message with the word PURPLE", "put REF-2026 in
  the subject" — precisely to catch people who skimmed or mass-applied. Missing
  one is an instant bin. The posting text is therefore never truncated on the way
  in, and the model is told to sweep the whole thing, end included.

* **Never invent a fact about the candidate.** The letter may draw only on the
  resume; question answers may draw only on profile.yaml. Anything not covered
  comes back blank and flagged, because a visible gap is recoverable and a
  confident fabrication is not.
"""

from __future__ import annotations

import json
import logging

from .utils import strip_tracking_params

logger = logging.getLogger(__name__)

# Below this, a description is a truncated alert snippet rather than a real
# posting — Indeed caps its emails around 160 characters — which means any
# instruction at the end of the real posting is invisible to us.
SNIPPET_CHARS = 400

SYSTEM = """You prepare a job application for a candidate to review. You never send anything.

READ THE ENTIRE POSTING BEFORE ANSWERING, INCLUDING THE FINAL LINES.
Employers hide compliance instructions at the very end of a posting to filter out
applicants who skim - "begin your message with the word BANANA", "use subject line
REF-4471", "tell us your favourite tool in the first sentence", "do not attach a CV".
Missing one gets the application discarded. Sweep the whole text and report every such
instruction verbatim in special_instructions, however trivial it looks. If there are
none, return an empty list.

COVER LETTER
Ground every claim in the candidate's resume below. Name the actual projects and what
was built with which technology - specifics beat adjectives. State plainly why this
candidate fits this posting, and do not pad with enthusiasm.
- Never claim a technology, employer, credential or duration the resume does not show.
- Do not invent metrics. Use a number only if the resume states it.
- If the posting names a stack the candidate lacks, do not bluff: either omit it or
  name the nearest genuine experience.
- 150-250 words, plain prose, no markdown, ready to paste into an email or a form.
- Follow any special_instructions you found (opening word, phrasing) inside the letter.
- End on your closing sentence. Do NOT write a sign-off, a name, or contact details:
  no "Yours truly", no "Best regards", no email address, no links. Those are appended
  afterwards from the candidate's own file, so anything you add here is a duplicate.

EMPLOYER QUESTIONS
Extract every question the posting asks - screening questions, "answer these in your
email", dropdown-style questions. For each, answer ONLY from the candidate profile.
- answer: your answer, or "" when the profile does not cover it.
- source: the profile field you used, or "" when you left it blank.
- needs_input: true when the candidate must supply or confirm this themselves.
Salary expectations, start dates and on-site willingness are decisions, not facts:
answer them from the profile if stated, and always set needs_input true so they are
confirmed before sending.

HOW TO APPLY
apply_method: "email" if the posting says to email someone, "platform" for an in-site
apply button, "external" for a company site or ATS link, "unknown" if unclear.
apply_email: the address if one is given, otherwise "".

Return raw JSON only, no markdown:
{"apply_method": "", "apply_email": "", "special_instructions": [], "cover_letter": "",
 "questions": [{"question": "", "answer": "", "source": "", "needs_input": false}],
 "missing_from_profile": []}"""


# A sign-off exists so somebody can reach you without scrolling - not to reprint
# the resume header that is already attached. So the default is short, and the
# omissions are deliberate:
#   email    - the message is being sent FROM it, and a form has its own field
#   github   - a portfolio site links to it; two links compete for one click
#   linkedin - on the resume, and rarely the thing a hiring manager opens first
# Override per-candidate with `signoff_fields:` in profile.yaml, e.g.
#   signoff_fields: [phone, portfolio, linkedin]
# Listing a field that is blank in the profile still prints nothing.
DEFAULT_SIGNOFF_FIELDS = ["phone", "portfolio"]

SIGNOFF_LABELS = {
    "email": "",
    "phone": "",
    "portfolio": "Portfolio: ",
    "github": "GitHub: ",
    "linkedin": "LinkedIn: ",
}


def signoff(profile: dict) -> str:
    """Build the closing from profile.yaml instead of asking the model for it.

    A sign-off is the one part of a letter with nothing to compose: the same name
    and the same links every time. Left to the model it came out differently each
    run and sometimes not at all - and a hallucinated portfolio URL is worse than
    a missing one, because it looks right and 404s for the employer rather than
    for you.

    Only fields actually present are printed, so a blank `github:` produces
    nothing rather than a dangling label.
    """
    name = str(profile.get("name") or "").strip()
    lines = ["Yours truly,", name] if name else []

    # `or` would be wrong here: an explicit `signoff_fields: []` means "name only"
    # and must not fall back to the default the way a missing key does.
    fields = profile.get("signoff_fields")
    if fields is None:
        fields = DEFAULT_SIGNOFF_FIELDS
    details = [
        f"{SIGNOFF_LABELS.get(field, '')}{str(profile.get(field)).strip()}"
        for field in fields
        if str(profile.get(field) or "").strip()
    ]

    if details:
        if lines:
            lines.append("")
        lines.extend(details)
    return "\n".join(lines)


# Closings the model reaches for anyway, despite being told not to. Matched on a
# line of its own, so a letter that happens to contain the word "regards" mid
# sentence is untouched.
_CLOSINGS = (
    "yours truly", "yours sincerely", "sincerely", "best regards", "kind regards",
    "warm regards", "regards", "best", "thank you", "thanks", "respectfully",
)


def _with_signoff(letter: str, profile: dict) -> str:
    """Attach the built sign-off, replacing any the model wrote despite the prompt.

    Told not to sign off, a model usually complies - but "usually" is the problem
    with prompt-only rules, and the failure here is a letter carrying two closings.
    So anything from a trailing closing line onward is dropped before appending.
    Only the tail is examined: the cut is anchored to a closing that is the whole
    line, in the last few lines, so body prose cannot be truncated.
    """
    block = signoff(profile)
    if not letter:
        return block
    if not block:
        return letter

    lines = letter.rstrip().split("\n")
    for i in range(max(0, len(lines) - 4), len(lines)):
        candidate = lines[i].strip().rstrip(",.").lower()
        if candidate in _CLOSINGS:
            lines = lines[:i]
            break

    return "\n".join(lines).rstrip() + "\n\n" + block


def draft_application(llm, model: str, job: dict, resume: str, profile: dict) -> dict:
    """Produce a reviewable draft. Returns {} when the model gives nothing usable."""
    description = str(job.get("description_summary") or job.get("description") or "")
    posting = "\n".join(
        [
            f"Title: {job.get('job_title') or job.get('title') or ''}",
            f"Company: {job.get('company') or ''}",
            f"Location: {job.get('location') or ''}",
            f"Salary: {job.get('salary') or ''}",
            f"Job type: {job.get('job_type') or ''}",
            f"Skills listed: {job.get('skills_required') or ''}",
            f"URL: {job.get('url') or ''}",
            "",
            "Posting text:",
            description,
            str(job.get("requirements") or ""),
        ]
    )

    user = (
        f"CANDIDATE RESUME:\n{resume}\n\n"
        "CANDIDATE PROFILE (the only source for question answers):\n"
        f"{json.dumps(profile, indent=2, default=str)}\n\n"
        f"JOB POSTING:\n{posting}"
    )

    data = llm.complete_json(model, SYSTEM, user, max_tokens=6000)
    if not isinstance(data, dict):
        return {}

    questions = [q for q in (data.get("questions") or []) if isinstance(q, dict)]
    return {
        "apply_method": str(data.get("apply_method") or "unknown"),
        "apply_email": str(data.get("apply_email") or ""),
        "special_instructions": [str(s) for s in (data.get("special_instructions") or [])],
        "cover_letter": _with_signoff(str(data.get("cover_letter") or "").strip(), profile),
        "questions": questions,
        "missing_from_profile": [str(s) for s in (data.get("missing_from_profile") or [])],
        "_posting_chars": len(description),
    }


def render(job: dict, result: dict) -> str:
    """Format a draft for reading in a terminal."""
    out: list[str] = []
    title = job.get("job_title") or job.get("title") or "?"
    out.append("=" * 72)
    out.append(f"{title} @ {job.get('company') or '?'}")
    out.append(f"score {job.get('score')}/100 | {job.get('salary') or 'no salary listed'}")
    if job.get("url"):
        # Rows stored before tracking-param stripping landed still hold raw links.
        out.append(strip_tracking_params(str(job["url"])))
    out.append("=" * 72)

    line = f"\nAPPLY VIA: {result.get('apply_method', 'unknown')}"
    if result.get("apply_email"):
        line += f" -> {result['apply_email']}"
    out.append(line)

    if result.get("_posting_chars", 0) < SNIPPET_CHARS:
        out.append(
            f"\n!! WARNING: only a truncated alert snippet was available "
            f"({result.get('_posting_chars', 0)} chars)."
            "\n   Any instruction at the END of the real posting is INVISIBLE here."
            "\n   Open the link and re-read the posting before sending."
        )

    instructions = result.get("special_instructions") or []
    if instructions:
        out.append("\n!! SPECIAL INSTRUCTIONS FROM THE POSTING - follow exactly:")
        for item in instructions:
            out.append(f"   * {item}")

    out.append("\n" + "-" * 72)
    out.append("COVER LETTER")
    out.append("-" * 72)
    out.append(result.get("cover_letter") or "(none generated)")

    questions = result.get("questions") or []
    if questions:
        out.append("\n" + "-" * 72)
        out.append("EMPLOYER QUESTIONS")
        out.append("-" * 72)
        for i, question in enumerate(questions, 1):
            out.append(f"\n{i}. {question.get('question', '')}")
            answer = (question.get("answer") or "").strip()
            if answer:
                source = question.get("source")
                out.append(f"   -> {answer}" + (f"   [profile: {source}]" if source else ""))
            else:
                out.append("   -> [BLANK - not in profile, you must answer this]")
            if question.get("needs_input"):
                out.append("   ** confirm this yourself before sending **")

    missing = result.get("missing_from_profile") or []
    if missing:
        out.append("\n" + "-" * 72)
        out.append("NEEDS YOUR INPUT")
        out.append("-" * 72)
        for item in missing:
            out.append(f"   * {item}")

    out.append("\n" + "=" * 72)
    out.append("Nothing was sent. Review, edit, then apply yourself.")
    return "\n".join(out)
