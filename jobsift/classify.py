"""Decide whether an email is a job alert and which source it's from."""

from __future__ import annotations

# A known_senders label that means "never a job alert". It is how a sender that
# jobsift added by itself (senders.learn) is taken back: the key stays, so the
# next daily check sees it as decided and does not add it again.
IGNORE = "ignore"


def classify(sender: str, subject: str, known_senders: dict, keywords: list[str]) -> tuple[bool, str]:
    """Return (is_job_email, source_label).

    The longest matching key wins, so `jobs-noreply@linkedin.com: linkedin`
    beside `linkedin.com: ignore` lets that one address through and nothing else
    from the domain. An ignored sender is not let back in by a subject keyword.
    """
    sender = (sender or "").lower()
    matches = [key for key in known_senders if key.lower() in sender]
    if matches:
        label = known_senders[max(matches, key=len)]
        if str(label).lower() == IGNORE:
            return False, IGNORE
        return True, label

    subject = (subject or "").lower()
    if any(kw.lower() in subject for kw in keywords):
        return True, "other"

    return False, "unknown"
