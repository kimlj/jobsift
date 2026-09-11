"""Every offline dryrun, run under pytest so CI can gate on it.

The dryruns stay the source of truth. Each is a script a person runs and reads,
and each exits non-zero when a case it checks goes wrong; this file only runs
them. The ones that need the network or an API key are left out on purpose,
because their output is a measurement to read, not a verdict - see
CONTRIBUTING.md.

    pytest
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

OFFLINE = [
    ("dryrun_dedup.py", []),
    ("dryrun_geography.py", ["--offline"]),
    ("dryrun_applied.py", []),
    ("dryrun_seen_skip.py", []),
    ("dryrun_closed.py", []),
    ("dryrun_evidence.py", []),
]


@pytest.mark.parametrize("script,args", OFFLINE, ids=[name for name, _ in OFFLINE])
def test_dryrun(script: str, args: list[str]) -> None:
    result = subprocess.run(
        [sys.executable, script, *args], cwd=ROOT, capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=300,
        # The scripts print box-drawing characters. A Windows pipe defaults to
        # the ANSI code page and would fail on the first one, not on the code.
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    assert result.returncode == 0, (
        f"{script} exited {result.returncode}\n"
        f"{result.stdout[-4000:]}\n{result.stderr[-2000:]}")
