"""The residential worker: onlinejobs.ph from a home connection, the rest on a server.

onlinejobs.ph answers a datacenter IP with a Cloudflare 403 and serves the same
request from a home connection, and it is the best source this pipeline has.
Everything else - the inbox, the remote feeds, scoring, the sheet, Telegram,
drafting - works from a server and is better there, because a server does not
sleep. So the work is split along the one thing that differs, and drawn so the
rule jobsift cannot break still holds: ONE database, written by ONE process.

  worker (the laptop)  reads onlinejobs.ph listing pages, asks the core which
                       listings it already has, reads detail pages for the rest
                       and delivers them. Never opens the database, never calls a
                       model, never touches the sheet. It also pushes the files the
                       core drafts from - the evidence brief, career.yaml, the
                       resume and the profile - whenever they change, so the laptop
                       stays their source of truth and no GitHub token has to live
                       on the server.
  core (the server)    runs the normal pipeline. Delivered batches land in an inbox
                       directory that the pipeline's own loop reads as one more
                       source, so the process writing the database is still the
                       only one.

The transport is SSH, with a key the core pins to one command (`--worker-serve`).
Through it the laptop can ask "which of these do you have", hand over a batch, or
replace one of four named files - and nothing else. No shell on a machine that
also runs other people's services, and no new port on it.

Delivery is at-least-once. A batch that could not be sent waits in the worker's
outbox and goes first on the next pass; one that arrives twice costs nothing,
because the core dedups every job on arrival exactly as it dedups email.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from .utils import job_key

logger = logging.getLogger(__name__)

PROTOCOL = 1
DEFAULT_INBOX = "./data/inbox"
MAX_BATCH_BYTES = 20_000_000
MAX_FILE_BYTES = 5_000_000
DONE_KEEP_DAYS = 14

# A batch name becomes a filename on the core, so it may not carry a path, and
# may not start with a dot, which is how an in-progress write is named.
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}")

# The only files a worker may replace on the core, by name. Each maps to a path
# the core's own config decides; the worker never names a path.
PUTTABLE = ("brief", "career", "resume", "profile")

# Batches the pass in progress has read. Filed as done only when that pass
# finishes, so one that dies half way re-reads them rather than losing them.
_in_flight: list[Path] = []


class TransportError(RuntimeError):
    """The core could not be reached, or refused the request."""


# ── the wire format: a header line, then one job per line ──────────────────────

def make_batch(jobs: list[dict], source: str = "onlinejobs_ph") -> str:
    header = {"jobsift_worker": PROTOCOL, "source": source, "jobs": len(jobs),
              "made_at": datetime.now().isoformat(timespec="seconds")}
    return "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in [header, *jobs])


def parse_batch(text: str) -> tuple[dict, list[dict]]:
    """(header, jobs), or ValueError for anything that is not a batch this version reads."""
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        raise ValueError("empty batch")
    header = json.loads(lines[0])
    if not isinstance(header, dict) or header.get("jobsift_worker") != PROTOCOL:
        raise ValueError(f"not a protocol {PROTOCOL} batch")
    jobs = [job for job in (json.loads(line) for line in lines[1:]) if isinstance(job, dict)]
    return header, jobs


def _text(data: bytes, limit: int = MAX_BATCH_BYTES) -> str:
    if len(data) > limit:
        raise ValueError(f"request larger than {limit} bytes")
    return data.decode("utf-8")


# ── the core's side ────────────────────────────────────────────────────────────

def _queued(inbox_dir: str) -> list[dict]:
    """Jobs delivered but not yet processed: waiting in the inbox, or mid-pass."""
    inbox = Path(inbox_dir)
    jobs: list[dict] = []
    for path in [*inbox.glob("*.jsonl"), *(inbox / "processing").glob("*.jsonl")]:
        try:
            jobs.extend(parse_batch(path.read_text(encoding="utf-8"))[1])
        except (OSError, ValueError):
            continue
    return jobs


def known(text: str, paths: dict) -> set[str]:
    """URLs among the worker's listings that the core already has, or soon will.

    "Has" is seen_jobs, the same question the pipeline asks when a job arrives,
    with the key computed here so the core stays its only authority. "Will" is
    anything delivered and not yet processed: without it, a worker pass that runs
    before the core's next pass would read the same detail pages again.
    """
    _, stubs = parse_batch(text)
    wanted = {job_key(stub): stub.get("url") for stub in stubs if stub.get("url")}
    found: set[str] = set()

    database = Path(paths["database"])
    if database.is_file() and wanted:
        # Read-only: the daemon owns this file, and this process only looks.
        conn = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
        try:
            keys = list(wanted)
            for start in range(0, len(keys), 500):
                chunk = keys[start:start + 500]
                rows = conn.execute(
                    f"SELECT key FROM seen_jobs WHERE key IN ({','.join('?' * len(chunk))})",
                    chunk)
                found.update(wanted[row[0]] for row in rows)
        finally:
            conn.close()

    for job in _queued(paths["inbox"]):
        if (url := wanted.get(job_key(job))) is not None:
            found.add(url)
    return found


def receive(name: str, text: str, paths: dict) -> str:
    """Accept one batch into the inbox. Validated before anything is written."""
    _, jobs = parse_batch(text)
    inbox = Path(paths["inbox"])
    inbox.mkdir(parents=True, exist_ok=True)
    filename = f"{name}.jsonl"
    # A resend after a dropped connection must not become a second batch.
    if any((folder / filename).exists()
           for folder in (inbox, inbox / "processing", inbox / "done")):
        return f"already have {filename}"
    part = inbox / f".{filename}.part"
    part.write_text(text, encoding="utf-8")
    # Renamed into place, so the daemon never reads a batch half written.
    os.replace(part, inbox / filename)
    return f"received {len(jobs)} job(s) as {filename}"


def put(which: str, data: bytes, paths: dict) -> str:
    """Replace one of the files the core drafts from. Refused if it would break drafting."""
    if len(data) > MAX_FILE_BYTES:
        raise ValueError(f"{which} is larger than {MAX_FILE_BYTES} bytes")
    if which in ("career", "profile"):
        import yaml

        try:
            parsed = yaml.safe_load(data.decode("utf-8"))
        except (UnicodeDecodeError, yaml.YAMLError) as exc:
            raise ValueError(f"{which} is not valid YAML ({exc})") from exc
        if not isinstance(parsed, dict):
            raise ValueError(f"{which} must be a YAML mapping")
    else:
        text = data.decode("utf-8")
        if not text.strip():
            raise ValueError(f"{which} is empty")
        if which == "brief" and not text.startswith("# Career evidence brief"):
            raise ValueError("that is not an evidence brief")
    target = Path(paths[which])
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(f".{target.name}.part")
    part.write_bytes(data)
    os.replace(part, target)
    return f"replaced {target.name} ({len(data)} bytes)"


def serve(command: str, data: bytes, paths: dict) -> tuple[int, str]:
    """Answer one worker request on the core: (exit code, output).

    `command` is what the worker asked for, which a pinned SSH key delivers in
    SSH_ORIGINAL_COMMAND. There are exactly three, and everything else is
    refused rather than interpreted:
      seen                 which of these listings do you have?
      deliver <name>       here is a batch of new jobs
      put <brief|career|resume|profile>   here is a newer copy of that file
    """
    parts = (command or "").split()
    op, rest = (parts[0], parts[1:]) if parts else ("", [])
    try:
        if op == "seen" and not rest:
            return 0, "".join(f"{url}\n" for url in sorted(known(_text(data), paths)))
        if op == "deliver" and len(rest) == 1 and _NAME.fullmatch(rest[0]):
            return 0, receive(rest[0], _text(data), paths) + "\n"
        if op == "put" and len(rest) == 1 and rest[0] in PUTTABLE:
            return 0, put(rest[0], data, paths) + "\n"
    except (ValueError, OSError) as exc:
        return 2, f"refused: {exc}\n"
    return 2, f"refused: {command!r} is not a worker request\n"


def serve_stdio(command: str, paths: dict) -> int:
    """serve() over this process's stdin and stdout, for `--worker-serve`."""
    data = sys.stdin.buffer.read(MAX_BATCH_BYTES + 1)
    code, out = serve(command, data, paths)
    sys.stdout.write(out)
    sys.stdout.flush()
    return code


def take_inbox(inbox_dir: str = DEFAULT_INBOX) -> list[dict]:
    """Every job from delivered batches not yet processed. Called by the core's loop.

    A batch moves to processing/ as it is read and to done/ only when the pass
    that read it finishes (finish_inbox). A pass that dies half way therefore
    re-reads it next time instead of losing it, which is harmless because every
    job is deduplicated on arrival.
    """
    inbox = Path(inbox_dir)
    processing = inbox / "processing"
    processing.mkdir(parents=True, exist_ok=True)
    for path in sorted(inbox.glob("*.jsonl")):
        os.replace(path, processing / path.name)

    _in_flight.clear()
    jobs: list[dict] = []
    for path in sorted(processing.glob("*.jsonl")):
        try:
            jobs.extend(parse_batch(path.read_text(encoding="utf-8"))[1])
        except (OSError, ValueError) as exc:
            # Set aside rather than retried forever or deleted unread.
            rejected = inbox / "rejected"
            rejected.mkdir(exist_ok=True)
            os.replace(path, rejected / path.name)
            logger.warning("worker inbox: %s unreadable (%s), moved to rejected/", path.name, exc)
            continue
        _in_flight.append(path)
    if jobs:
        logger.info("worker inbox: %d job(s) from %d batch(es)", len(jobs), len(_in_flight))
    return jobs


def finish_inbox(inbox_dir: str = DEFAULT_INBOX, keep_days: int = DONE_KEEP_DAYS) -> int:
    """File the batches this pass read as done. Anything it did not read stays put."""
    inbox = Path(inbox_dir)
    done = inbox / "done"
    done.mkdir(parents=True, exist_ok=True)
    moved = 0
    for path in _in_flight:
        if path.exists():
            os.replace(path, done / path.name)
            moved += 1
    _in_flight.clear()
    # Kept a while so a late resend is recognised, then dropped.
    cutoff = time.time() - keep_days * 86400
    for path in done.glob("*.jsonl"):
        if path.stat().st_mtime < cutoff:
            path.unlink()
    return moved


# ── transports ─────────────────────────────────────────────────────────────────

class SSHTransport:
    """Requests to the core over SSH, one command each.

    BatchMode, because this runs unattended and a password prompt would hang the
    worker forever. accept-new pins the core's host key on first contact and
    refuses a changed one afterwards. The key should be one the core pins to
    --worker-serve (docs/residential-worker.md), so that what it can do there is
    exactly what serve() allows and no more.
    """

    def __init__(self, host: str, key: str = "", ssh: str = "ssh", timeout: int = 300):
        self.host, self.key, self.ssh, self.timeout = host, key, ssh, timeout

    def call(self, request: str, data: bytes = b"") -> str:
        cmd = [self.ssh, "-o", "BatchMode=yes", "-o", "ConnectTimeout=20",
               "-o", "StrictHostKeyChecking=accept-new", "-o", "ServerAliveInterval=30"]
        if self.key:
            cmd += ["-i", os.path.expanduser(self.key)]
        cmd += [self.host, request]
        try:
            done = subprocess.run(cmd, input=data, capture_output=True, timeout=self.timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise TransportError(str(exc)) from exc
        out = done.stdout.decode("utf-8", "replace")
        if done.returncode != 0:
            detail = (out.strip() or done.stderr.decode("utf-8", "replace").strip()
                      or f"ssh exited {done.returncode}")
            raise TransportError(detail.splitlines()[-1][:300])
        return out


class LocalTransport:
    """The core's side called in-process: for the dryrun, or both ends on one machine."""

    def __init__(self, paths: dict):
        self.paths = paths

    def call(self, request: str, data: bytes = b"") -> str:
        code, out = serve(request, data, self.paths)
        if code:
            raise TransportError(out.strip())
        return out


# ── the worker's side ──────────────────────────────────────────────────────────

def _stub(job: dict) -> dict:
    """What `seen` needs to compute the core's dedup key, and nothing more."""
    return {k: job.get(k) or "" for k in ("title", "company", "url")}


def _deliver(transport, path: Path) -> int:
    """Send one outbox batch and remove it once the core has it. Returns its job count."""
    text = path.read_text(encoding="utf-8")
    transport.call(f"deliver {path.stem}", text.encode("utf-8"))
    path.unlink()
    return len(parse_batch(text)[1])


def flush_outbox(transport, outbox: Path) -> int:
    """Deliver whatever earlier passes could not, oldest first. Stops at the first failure."""
    return sum(_deliver(transport, path) for path in sorted(outbox.glob("*.jsonl")))


def run_pass(settings: dict, transport, outbox_dir) -> dict:
    """One worker pass: scrape, ask the core, read only what is new, deliver.

    If the core cannot be asked, the pass stops before any detail page is read.
    Reading all of them instead would cost about half an hour at the site's
    Crawl-delay to queue jobs the core mostly has, and the board keeps listings
    up for months, so the next pass loses nothing by waiting.
    """
    from .sources.onlinejobs import fetch_jobs

    outbox = Path(outbox_dir)
    outbox.mkdir(parents=True, exist_ok=True)
    summary = {"found": 0, "known": 0, "delivered": 0, "queued": 0, "error": ""}

    try:
        summary["delivered"] += flush_outbox(transport, outbox)
    except TransportError as exc:
        summary["error"] = f"core unreachable, outbox kept: {exc}"
        return summary

    on_core: set[str] = set()

    def seen_batch(pending: list[dict]) -> set[str]:
        answer = transport.call("seen", make_batch([_stub(j) for j in pending]).encode("utf-8"))
        on_core.update(line.strip() for line in answer.splitlines() if line.strip())
        return on_core

    try:
        jobs = fetch_jobs(settings, seen_batch=seen_batch)
    except TransportError as exc:
        summary["error"] = f"could not ask the core what it has: {exc}"
        return summary

    fresh = [j for j in jobs if not j.get("url") or j["url"] not in on_core]
    summary["found"], summary["known"] = len(jobs), len(jobs) - len(fresh)
    if not fresh:
        return summary

    # Written to the outbox first, so a failure anywhere after this loses nothing.
    path = outbox / f"olj-{datetime.now():%Y%m%d-%H%M%S}.jsonl"
    path.write_text(make_batch(fresh), encoding="utf-8")
    try:
        summary["delivered"] += _deliver(transport, path)
    except TransportError as exc:
        summary["queued"] = len(fresh)
        summary["error"] = f"delivery failed, kept in the outbox: {exc}"
    return summary


def push_files(transport, files: dict, state_path) -> list[str]:
    """Send each of the core's drafting files that changed since it was last sent."""
    state_file = Path(state_path)
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    sent = []
    for which, path in files.items():
        source = Path(path)
        if which not in PUTTABLE or not source.is_file():
            continue
        data = source.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if state.get(which) == digest:
            continue
        try:
            transport.call(f"put {which}", data)
        except TransportError as exc:
            logger.warning("worker: could not push %s (%s); the next pass tries again", which, exc)
            continue
        state[which] = digest
        sent.append(which)
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return sent


def run(config, profile_path: str, once: bool = False) -> None:
    """The worker's loop, in place of the pipeline on a machine with worker.enabled."""
    settings = config.worker
    if not settings.get("ssh"):
        raise SystemExit("worker.enabled is true but worker.ssh is empty: "
                         "say which host the core runs on, as user@host.")
    transport = SSHTransport(settings["ssh"], settings.get("ssh_key") or "",
                             settings.get("ssh_command") or "ssh")
    outbox = Path(settings.get("outbox") or "./data/outbox")
    olj = dict((config.scrape_sources or {}).get("onlinejobs_ph") or {})
    interval = float(olj.get("interval_seconds") or config.scrape_interval_seconds or 1200)
    evidence = config.evidence or {}
    files = {"brief": evidence.get("brief") or "./data/career-brief.md",
             "career": evidence.get("career") or "./career.yaml",
             "resume": config.resume_path, "profile": profile_path}

    while True:
        started = time.time()
        try:
            if evidence.get("enabled") and evidence.get("auto_refresh", True):
                from . import career

                career.refresh(evidence, files["career"], files["brief"],
                               github_every_hours=float(evidence.get("github_every_hours") or 24))
            if sent := push_files(transport, files, outbox / "pushed.json"):
                logger.info("worker: pushed %s to the core", ", ".join(sent))
            s = run_pass(olj, transport, outbox)
            logger.info("worker pass: %d listing(s), %d already on the core, %d delivered, "
                        "%d waiting in the outbox%s", s["found"], s["known"], s["delivered"],
                        s["queued"], f" - {s['error']}" if s["error"] else "")
        except Exception:
            logger.exception("worker pass failed; the next one is in %.0fs", interval)
        if once:
            return
        time.sleep(max(0.0, interval - (time.time() - started)))
