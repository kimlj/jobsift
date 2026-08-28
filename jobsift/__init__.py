"""jobsift — a self-hosted job-alert email pipeline.

Reads job-alert emails from a Gmail inbox, extracts the listings, enriches them
from the linked page, scores them against your resume, dedups, archives to a
Google Sheet, and pings Telegram for the high scorers.
"""

__version__ = "0.1.0"
