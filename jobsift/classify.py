"""Decide whether an email is a job alert and which source it's from."""

from __future__ import annotations


def classify(sender: str, subject: str, known_senders: dict, keywords: list[str]) -> tuple[bool, str]:
    """Return (is_job_email, source_label)."""
    sender = (sender or "").lower()
    for domain, label in known_senders.items():
        if domain.lower() in sender:
            return True, label

    subject = (subject or "").lower()
    if any(kw.lower() in subject for kw in keywords):
        return True, "other"

    return False, "unknown"
