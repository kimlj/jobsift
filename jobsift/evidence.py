"""What the repos actually contain, counted rather than remembered.

This exists because of one afternoon. A draft told an employer that Kim had not
authored row-level-security policies. He had: `mdspromonitor` holds 35 of them
across 15 tables. The drafter was not wrong to say it - `resume.txt` is its only
permitted source for claims about the candidate, and the resume did not mention
them. The failure was that nothing connected "what was built" to "what may be
claimed", so the connection ran through his memory, and memory is not a system.

So the repos are counted. Every fact here is derived from files on disk: a
migration is a file, a policy is a `create policy` statement, a commit date comes
from git. Nothing is inferred, and prose is deliberately not read - a README that
says "highly scalable" is an adjective, and an adjective in a trusted source is
how a drafter learns to bluff.

The output is a THIRD trusted input beside resume.txt and profile.yaml, and it
widens what the drafter may say, so it is kept to things that can be checked in
one command. Each entry names its repo precisely so a claim can be opened and
verified before anyone sends it.
"""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# Directories that are somebody else's code, or build output. Scanning them is
# slow and every count taken inside one is a lie about what was written here.
SKIP_DIRS = {
    "node_modules", ".git", "dist", "build", ".venv", "venv", "__pycache__",
    ".next", "out", "coverage", ".cache", "vendor", ".claude", "site-packages",
}

# Source extensions worth counting, and what to call them.
LANGUAGES = {
    ".ts": "TypeScript", ".tsx": "TypeScript", ".js": "JavaScript",
    ".jsx": "JavaScript", ".mjs": "JavaScript", ".py": "Python",
    ".sql": "SQL", ".gs": "Apps Script", ".sh": "Shell", ".ps1": "PowerShell",
    ".kt": "Kotlin", ".java": "Java", ".swift": "Swift", ".css": "CSS",
}

# Countable SQL facts. Each is a statement somebody wrote, not a judgment.
SQL_PATTERNS = {
    "rls_policies": r"create\s+policy",
    "rls_enabled_statements": r"enable\s+row\s+level\s+security",
    "security_definer_functions": r"security\s+definer",
    "indexes": r"create\s+(?:unique\s+)?index",
    "triggers": r"create\s+trigger",
}

# A directory named for an automation platform is evidence that platform was used.
# Postings ask for these as a countable set - "familiarity with n8n, Zapier, Make"
# - so the count is the answer.
PLATFORM_DIRS = {
    "n8n": "n8n", "zapier": "Zapier", "make": "Make",
    "power-automate": "Power Automate", "apps-script": "Google Apps Script",
    "looker": "Looker Studio", "supabase": "Supabase",
}

TEST_HINTS = ("test", "spec", "dryrun")


def _git(repo: Path, *args: str) -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True, text=True, timeout=20, check=False,
        ).stdout.strip()
    except Exception:
        return ""


def _walk(repo: Path):
    """Every source file under `repo`, skipping other people's code."""
    for path in repo.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        yield path


def _stack(repo: Path) -> list[str]:
    """Named dependencies, from the manifests rather than from guessing."""
    found: set[str] = set()

    package = repo / "package.json"
    if package.is_file():
        try:
            import json

            data = json.loads(package.read_text(encoding="utf-8", errors="replace"))
            deps = {**(data.get("dependencies") or {}),
                    **(data.get("devDependencies") or {})}
            # The whole dependency tree is noise; these are the ones an employer
            # asks about by name.
            interesting = (
                "fastify", "express", "next", "react", "react-native", "socket.io",
                "@supabase/supabase-js", "@anthropic-ai/sdk", "openai", "zod",
                "exceljs", "googleapis", "prisma", "drizzle-orm", "vite",
            )
            found.update(d for d in deps if d in interesting)
        except Exception:
            pass

    for manifest in ("requirements.txt", "pyproject.toml"):
        path = repo / manifest
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="replace").lower()
            found.update(
                name for name in
                ("fastapi", "flask", "django", "anthropic", "openai", "httpx",
                 "gspread", "pydantic", "sqlalchemy")
                if name in text
            )
    return sorted(found)


def scan_repo(path: str | Path) -> dict:
    """One repo, counted. {} when the path is not a directory."""
    repo = Path(path)
    if not repo.is_dir():
        logger.info("evidence: no repo at %s", repo)
        return {}

    facts: dict = {"name": repo.name, "path": str(repo)}

    first = _git(repo, "log", "--reverse", "--format=%ad", "--date=short")
    if first:
        facts["first_commit"] = first.split("\n")[0]
        facts["last_commit"] = _git(repo, "log", "-1", "--format=%ad", "--date=short")
        commits = _git(repo, "rev-list", "--count", "HEAD")
        if commits.isdigit():
            facts["commits"] = int(commits)

    languages: dict[str, int] = {}
    # SQL is kept in two piles, never one. A baseline schema.sql declares the same
    # policies the migrations later alter, so summing them reports 39 policies where
    # 35 were written - an inflated number inside a trusted source, which is the
    # exact failure this module exists to prevent.
    sql_by_scope: dict[str, list[str]] = {"migrations": [], "baseline": []}
    migrations = tests = 0
    platforms: set[str] = set()

    for file in _walk(repo):
        suffix = file.suffix.lower()
        if language := LANGUAGES.get(suffix):
            languages[language] = languages.get(language, 0) + 1
        if suffix == ".sql":
            in_migrations = "migration" in str(file).lower()
            sql_by_scope["migrations" if in_migrations else "baseline"].append(
                file.read_text(encoding="utf-8", errors="replace").lower())
            migrations += 1 if in_migrations else 0
        if any(hint in file.name.lower() for hint in TEST_HINTS) and suffix in LANGUAGES:
            tests += 1

    for child in repo.iterdir():
        if child.is_dir() and (name := PLATFORM_DIRS.get(child.name.lower())):
            platforms.add(name)

    if languages:
        facts["files_by_language"] = dict(sorted(
            languages.items(), key=lambda kv: -kv[1]))
    if stack := _stack(repo):
        facts["dependencies"] = stack
    if platforms:
        facts["automation_platforms"] = sorted(platforms)
    if migrations:
        facts["sql_migrations"] = migrations
    if tests:
        facts["test_files"] = tests

    for scope, texts in sql_by_scope.items():
        if not texts:
            continue
        blob = "\n".join(texts)
        counts = {
            label: len(re.findall(pattern, blob))
            for label, pattern in SQL_PATTERNS.items()
        }
        # `enable row level security` is per statement; the DISTINCT tables it was
        # enabled on is the number worth quoting, so count those separately.
        tables = set(re.findall(
            r"alter\s+table\s+(?:only\s+)?([\w.\"]+)\s+enable\s+row\s+level\s+security",
            blob))
        if tables:
            counts["rls_enabled_tables"] = len(tables)
        if counts := {k: v for k, v in counts.items() if v}:
            facts.setdefault("sql", {})[scope] = counts

    return facts


def build_index(repos: list[str]) -> dict:
    out = {}
    for path in repos:
        facts = scan_repo(path)
        if facts:
            out[facts["name"]] = facts
    return out


def write_index(repos: list[str], destination: str) -> dict:
    """Scan and write. Returns the index so a caller can report on it."""
    import yaml
    from datetime import datetime

    index = build_index(repos)
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "note": (
            "GENERATED by `python -m jobsift --index-repos`. Every number here was "
            "counted from files on disk. Do not hand-edit: the point is that it "
            "cannot drift from the code the way a resume does."
        ),
        "repos": index,
    }
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
                    encoding="utf-8")
    logger.info("evidence: indexed %d repo(s) into %s", len(index), destination)
    return index


def load_index(destination: str) -> dict:
    """The stored index, or {} when there is not one. Never raises."""
    import yaml

    try:
        data = yaml.safe_load(Path(destination).read_text(encoding="utf-8")) or {}
        return data.get("repos") or {}
    except FileNotFoundError:
        return {}
    except Exception as exc:
        logger.warning("evidence: could not read %s (%s)", destination, exc)
        return {}


def summarise(index: dict) -> str:
    """A compact block for a prompt. One repo per paragraph, facts only."""
    lines: list[str] = []
    for name, facts in index.items():
        bits: list[str] = []
        if facts.get("commits"):
            bits.append(f"{facts['commits']} commits "
                        f"{facts.get('first_commit', '?')} to {facts.get('last_commit', '?')}")
        if languages := facts.get("files_by_language"):
            bits.append("files: " + ", ".join(f"{v} {k}" for k, v in languages.items()))
        if deps := facts.get("dependencies"):
            bits.append("dependencies: " + ", ".join(deps))
        if platforms := facts.get("automation_platforms"):
            bits.append("automation platforms: " + ", ".join(platforms))
        if facts.get("sql_migrations"):
            bits.append(f"{facts['sql_migrations']} SQL migrations")
        for scope, counts in (facts.get("sql") or {}).items():
            bits.append(f"in {scope}: " + ", ".join(
                f"{v} {k.replace('_', ' ')}" for k, v in counts.items()))
        if facts.get("test_files"):
            bits.append(f"{facts['test_files']} test files")
        if bits:
            lines.append(f"{name}: " + "; ".join(bits))
    return "\n".join(lines)
