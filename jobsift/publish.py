"""Publish a rendered resume at a link that never changes, for boards that take no file.

onlinejobs.ph accepts no upload anywhere, so the link in the message IS the
resume. The skill used to do this by hand every time: a random suffix, a copy
into the portfolio's r/ folder, commit, rebase, push, a hash check against the
live file, a row in the log of who got which link. Six steps with no judgment
in any of them, and a dead link on the one application that argues for shipped
work is the worst available failure. So it is one command:

    python -m jobsift --publish COMPANY

The suffix is random so an employer holding one link cannot guess another and
learn where else the application went. A published file is never renamed or
removed: an employer may open the link months later.
"""

from __future__ import annotations

import hashlib
import secrets
import shutil
import subprocess
import time
from datetime import date
from pathlib import Path

from .render import LINK_PLACEHOLDER, file_prefix, load_spec

LOG_HEADER = ("# Per-application resume links\n\n"
              "| Sent | Employer | URL | Built from |\n|---|---|---|---|\n")


class PublishError(Exception):
    pass


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def wait_live(url: str, expected: str, fetch, wait_seconds: float, poll_every: float) -> bool:
    """True once the file at `url` is byte-for-byte the one pushed. A static host
    serves the previous deploy, or a 404, for a minute or so after a push."""
    deadline = time.monotonic() + wait_seconds
    while True:
        try:
            response = fetch(url)
            if response.status_code == 200 and _sha(response.content) == expected:
                return True
        except Exception:
            pass
        if time.monotonic() >= deadline:
            return False
        time.sleep(poll_every)


def publish(spec_file, profile: dict, settings: dict, *, run=subprocess.run, fetch=None,
            today: str | None = None, wait_seconds: float = 300, poll_every: float = 10) -> dict:
    spec = load_spec(spec_file)
    company = spec["company"]
    repo = Path(str(settings.get("publish_repo") or "")).expanduser()
    base = str(settings.get("publish_url") or "").rstrip("/")
    if not str(settings.get("publish_repo") or "") or not (repo / ".git").exists():
        raise PublishError("render.publish_repo in config.yaml must be a git repo whose r/ "
                           "folder is served on the web.")
    if not base:
        raise PublishError("render.publish_url in config.yaml must say where r/ is served, "
                           "e.g. https://www.example.dev/r")
    pdf = Path(settings["output_dir"]).expanduser() / f"{file_prefix(profile)}_Resume_{company}.pdf"
    if not pdf.exists():
        raise PublishError(f"No {pdf.name} yet: run --render {company} first.")

    def git(*args):
        done = run(["git", "-C", str(repo), *args], capture_output=True, text=True)
        if done.returncode != 0:
            raise PublishError(f"git {' '.join(args)} failed: {(done.stderr or done.stdout).strip()}")
        return done.stdout

    name = f"{company.lower()}-{secrets.token_hex(4)}.pdf"
    log_rel = str(settings.get("publish_log") or "docs/resume-links.md")
    # Up to date first, carrying any unrelated edits in the repo around the pull.
    git("pull", "--rebase", "--autostash", "-q")
    (repo / "r").mkdir(exist_ok=True)
    shutil.copyfile(pdf, repo / "r" / name)
    log = repo / log_rel
    log.parent.mkdir(parents=True, exist_ok=True)
    text = log.read_text(encoding="utf-8") if log.exists() else LOG_HEADER
    employer = str(spec.get("employer") or company).replace("|", "/")
    row = f"| {today or date.today().isoformat()} | {employer} | `/r/{name}` | `{pdf.name}` |"
    log.write_text(text.rstrip("\n") + "\n" + row + "\n", encoding="utf-8")
    # Only these two paths: the repo may hold other work in progress.
    git("add", "--", f"r/{name}", log_rel)
    git("commit", "-q", "-m", f"Publish the {company} resume variant", "--", f"r/{name}", log_rel)
    git("push", "-q")

    url = f"{base}/{name}"
    if fetch is None:
        import httpx

        fetch = lambda u: httpx.get(u, follow_redirects=True, timeout=15)
    verified = wait_live(url, _sha(pdf.read_bytes()), fetch, wait_seconds, poll_every)

    # The link goes where it is used: the message, and the spec for a re-render.
    filled = []
    message = Path(settings["output_dir"]).expanduser() / f"{file_prefix(profile)}_Message_{company}.txt"
    if message.exists():
        body = message.read_text(encoding="utf-8")
        if LINK_PLACEHOLDER in body:
            message.write_text(body.replace(LINK_PLACEHOLDER, f"Resume: {url}"), encoding="utf-8")
            filled.append(str(message))
    if spec["board"] == "onlinejobs":
        from .onboard import SetupError, _read, _write, set_yaml_value

        path = Path(spec_file)
        try:
            _write(path, set_yaml_value(_read(path), ("message", "link"), url, insert=True))
            filled.append(str(path))
        except SetupError:
            pass
    return {"url": url, "verified": verified, "row": row, "filled": filled}
