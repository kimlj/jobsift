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

It also FINDS the repos now, rather than being handed a list. The list was five
paths long and the corpus is thirty-odd: a draft called blockchain a gap while
nine Solana repos sat on GitHub and in folders whose names matched none of them
(`Documents/ore-cli` is `kimlj/ore-cli-codex`). So identity comes from the origin
URL, never the folder; a repo that exists only on GitHub is cloned into a local
mirror and counted by the same code as the rest; and two things already checked by
somebody are read in beside the counts - the portfolio's Skills ledger, and pull
requests merged upstream. It is still facts only. What a repo MEANS, and which
claims need a caveat, is hand-written in career.yaml and handled by career.py.
"""

from __future__ import annotations

import html
import json
import logging
import os
import re
import subprocess
from datetime import datetime
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
    ".php": "PHP",
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

# Folders that hold tests in other people's projects, for sorting a merged PR's
# files. By folder rather than by substring, or src/util/latestValue.ts is a test.
TEST_DIRS = {"test", "tests", "__tests__", "spec", "specs", "e2e", "__snapshots__"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}

# The dependencies an employer asks about by name; the whole tree is noise. Read
# from EVERY manifest in a repo, not only the root one: casinore.io keeps Privy in
# a sub-package, and a root-only scan once reported it absent.
INTERESTING_JS = (
    "fastify", "express", "next", "react", "react-native", "expo", "socket.io",
    "ws", "@supabase/supabase-js", "@anthropic-ai/sdk", "openai", "zod",
    "exceljs", "googleapis", "prisma", "drizzle-orm", "vite", "tailwindcss",
    "@capacitor/core", "better-sqlite3", "@playwright/test", "vitest", "jest",
    "@solana/web3.js", "@solana/wallet-adapter-react", "@privy-io/react-auth",
    "@privy-io/node", "@privy-io/server-auth", "stripe", "@sentry/node",
    "@sentry/react", "zustand", "react-native-reanimated",
)
INTERESTING_PY = (
    "fastapi", "flask", "django", "anthropic", "openai", "httpx",
    "gspread", "pydantic", "sqlalchemy", "playwright", "pytest",
)
MANIFESTS = {"package.json", "requirements.txt", "pyproject.toml"}

_GITHUB_URL = re.compile(r"github\.com[:/]+([^/\s]+)/([^/\s]+?)(?:\.git)?/?$", re.I)


def _run(cmd: list[str], timeout: int = 60) -> str:
    """stdout of `cmd`, or "" when it fails. Never raises: a missing tool is data."""
    try:
        return subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, check=False, stdin=subprocess.DEVNULL,
        ).stdout.strip()
    except Exception as exc:
        logger.info("evidence: %s failed (%s)", cmd[0], exc)
        return ""


def _git(repo: Path, *args: str) -> str:
    return _run(["git", "-C", str(repo), *args], timeout=20)


def _walk(repo: Path):
    """Every file somebody put in `repo`, skipping other people's code.

    The files git tracks, when the repo has any. That is what was written and
    committed here, and it leaves out on its own everything .gitignore does:
    build output, local data, and any other repo cloned inside this one. The last
    case is not hypothetical - jobsift keeps its GitHub mirrors under data/, and a
    plain walk counted their Expo and Next.js code as jobsift's.

    A repo with no commits falls back to walking the disk, pruning nested repos
    the same way, and pruning while walking rather than after: `rglob` descends
    into node_modules first and discards it second.
    """
    tracked = _git(repo, "ls-files", "-z")
    if tracked:
        for rel in tracked.split("\0"):
            if rel and not any(part in SKIP_DIRS for part in rel.split("/")):
                path = repo / rel
                if path.is_file():
                    yield path
        return
    for root, dirs, files in os.walk(repo):
        dirs[:] = [d for d in dirs
                   if d not in SKIP_DIRS and not (Path(root) / d / ".git").exists()]
        for name in files:
            yield Path(root) / name


def _stack(manifests: list[Path], added: str | None = None) -> list[str]:
    """Named dependencies, from the manifests rather than from guessing.

    With `added` - the words the candidate's own commits added to a manifest - a
    dependency counts only if the candidate put it there. A starter template or an
    upstream codebase ships its own package.json, and inheriting one is not
    evidence of having used what is in it.
    """
    found: set[str] = set()
    for path in manifests:
        text = path.read_text(encoding="utf-8", errors="replace")
        if path.name == "package.json":
            try:
                data = json.loads(text)
            except ValueError:
                continue
            if not isinstance(data, dict):
                continue
            deps = {**(data.get("dependencies") or {}),
                    **(data.get("devDependencies") or {})}
            found.update(d for d in deps if d in INTERESTING_JS
                         and (added is None or f'"{d}"' in added))
        else:
            lowered = text.lower()
            found.update(name for name in INTERESTING_PY if name in lowered
                         and (added is None or name in added))
    return sorted(found)


def _authored(repo: Path, authors: tuple[str, ...]) -> int | None:
    """Commits whose author matches one of `authors`; None when none are configured.

    A repo started from somebody else's code carries their history in its total,
    and a total quoted on an application is read as the candidate's own work. The
    authored count is the number that is actually about the person.
    """
    if not authors:
        return None
    total = 0
    for line in _git(repo, "shortlog", "-sne", "HEAD").splitlines():
        count, _, who = line.strip().partition("\t")
        if count.isdigit() and any(a.lower() in who.lower() for a in authors):
            total += int(count)
    return total


def _author_args(authors: tuple[str, ...]) -> list[str]:
    """`git log` filters for any of `authors`: plain substrings, any case, OR'd."""
    return ["--fixed-strings", "--regexp-ignore-case",
            *(f"--author={a}" for a in authors)]


def _touched(repo: Path, authors: tuple[str, ...]) -> set[str]:
    """Every path the candidate's own commits added or changed."""
    out = _git(repo, "-c", "core.quotepath=off", "log", *_author_args(authors),
               "--name-only", "--format=", "HEAD")
    return {line.strip() for line in out.splitlines() if line.strip()}


def _added_to_manifests(repo: Path, authors: tuple[str, ...]) -> str:
    """The words the candidate's own commits added to dependency manifests, lowercased.

    A word diff, not a line diff. Adding a dependency after the last entry of a
    JSON object also rewrites the line above it to add a comma, and a line diff
    reports that neighbour as added too - which credits the candidate with
    whatever an upstream author put there. Only the words that changed count.
    """
    out = _git(repo, "log", *_author_args(authors), "-p", "--word-diff=porcelain",
               "--format=", "HEAD", "--",
               "*package.json", "*requirements.txt", "*pyproject.toml")
    return "\n".join(line[1:] for line in out.splitlines()
                     if line.startswith("+") and not line.startswith("+++")).lower()


def scan_repo(path: str | Path, authors: tuple[str, ...] = ()) -> dict:
    """One repo, counted. {} when the path is not a directory."""
    repo = Path(path)
    if not repo.is_dir():
        logger.info("evidence: no repo at %s", repo)
        return {}

    facts: dict = {"name": repo.name, "path": str(repo)}

    # With authors configured, only what the candidate's own commits touched is
    # counted: a clone of somebody's project, or your own work on top of a
    # template, would otherwise lend you every language and package in it. None
    # means count everything, which is all a repo can offer when nobody has said
    # who the candidate is.
    mine: set[str] | None = None
    added: str | None = None

    first = _git(repo, "log", "--reverse", "--format=%ad", "--date=short")
    if first:
        facts["first_commit"] = first.split("\n")[0]
        facts["last_commit"] = _git(repo, "log", "-1", "--format=%ad", "--date=short")
        commits = _git(repo, "rev-list", "--count", "HEAD")
        if commits.isdigit():
            facts["commits"] = int(commits)
        authored = _authored(repo, authors)
        if authored is not None:
            facts["authored_commits"] = authored
            if not authored:
                facts["not_counted"] = "none of its commits are the candidate's"
                return facts
            # The dates are a claim about the person ("worked on this since June"),
            # so they come from the person's commits, not the repo's.
            own = _git(repo, "log", *_author_args(authors), "--reverse",
                       "--format=%ad", "--date=short", "HEAD").splitlines()
            if own:
                facts["first_commit"], facts["last_commit"] = own[0], own[-1]
            mine = _touched(repo, authors)
            added = _added_to_manifests(repo, authors)
    elif authors:
        facts["not_counted"] = "no commits, so nothing in it can be attributed"
        return facts

    def yours(prefix: str) -> bool:
        return mine is None or any(p == prefix or p.startswith(prefix + "/") for p in mine)

    languages: dict[str, int] = {}
    # SQL is kept in two piles, never one. A baseline schema.sql declares the same
    # policies the migrations later alter, so summing them reports 39 policies where
    # 35 were written - an inflated number inside a trusted source, which is the
    # exact failure this module exists to prevent.
    sql_by_scope: dict[str, list[str]] = {"migrations": [], "baseline": []}
    migrations = tests = 0
    platforms: set[str] = set()
    manifests: list[Path] = []

    for file in _walk(repo):
        suffix = file.suffix.lower()
        if file.name in MANIFESTS:
            manifests.append(file)
        if mine is not None and file.relative_to(repo).as_posix() not in mine:
            continue
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
        if (child.is_dir() and (name := PLATFORM_DIRS.get(child.name.lower()))
                and yours(child.name)):
            platforms.add(name)

    if languages:
        facts["files_by_language"] = dict(sorted(
            languages.items(), key=lambda kv: -kv[1]))
    if stack := _stack(manifests, added):
        facts["dependencies"] = stack
    # CI is a file, not a dependency, so a dependency scan never sees it. The
    # portfolio called GitHub Actions unshown while it ran the page's own daily
    # data refresh.
    ci = []
    workflows = repo / ".github" / "workflows"
    if workflows.is_dir() and any(workflows.iterdir()) and yours(".github/workflows"):
        ci.append("GitHub Actions")
    if (repo / "codemagic.yaml").is_file() and yours("codemagic.yaml"):
        ci.append("Codemagic")

    if platforms:
        facts["automation_platforms"] = sorted(platforms)
    if ci:
        facts["ci"] = ci
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


def origin_slug(repo: Path) -> str:
    """`owner/name` from the origin remote, or "" when it is not on GitHub."""
    match = _GITHUB_URL.search(_git(repo, "remote", "get-url", "origin"))
    return f"{match.group(1)}/{match.group(2)}" if match else ""


def _consider(found: dict, path: Path, owner: str, skipped: set[str],
              merges: list[str] | None = None) -> None:
    """Add one clone to `found`, unless it is somebody else's or a staler copy.

    Two clones of one remote are one repo, and the one with more commits wins,
    because the other is an old copy (this machine has three clones of the
    portfolio). A remote owned by somebody else is skipped: counting upstream code
    is a lie about what was written here. A repo with no remote is kept under its
    folder name - whether any of it is the candidate's is scan_repo's question.
    """
    slug = origin_slug(path)
    if slug and owner and slug.split("/")[0].lower() != owner.lower():
        logger.debug("evidence: skipping %s, its origin %s is not %s's", path, slug, owner)
        return
    name = slug.split("/")[1] if slug else path.name
    if name.lower() in skipped or path.name.lower() in skipped:
        return
    count = _git(path, "rev-list", "--count", "HEAD")
    commits = int(count) if count.isdigit() else 0
    roots = _git(path, "rev-list", "--max-parents=0", "HEAD").splitlines()
    entry = {"name": name, "path": path, "slug": slug, "commits": commits,
             "root": roots[0] if roots else ""}
    key = name.lower()

    # The same history under a second name: a copy whose remote was removed, or a
    # folder cloned from another folder. Merged only when one side has no GitHub
    # remote. Two GitHub repos sharing a first commit are two projects started
    # from one template, and merging those would silently lose one of them.
    if entry["root"]:
        for other_key, other in list(found.items()):
            if (other_key != key and other["root"] == entry["root"]
                    and not (other["slug"] and slug)):
                named = other if other["slug"] else entry
                kept = entry if commits > other["commits"] else other
                del found[other_key]
                key = named["name"].lower()
                entry = {**kept, "name": named["name"], "slug": named["slug"]}
                if merges is not None:
                    merges.append(f"{other['path']} and {path}")
                break

    if key not in found or entry["commits"] > found[key]["commits"]:
        found[key] = entry


def discover(roots: list[str], owner: str = "", skip=(),
             merges: list[str] | None = None) -> dict[str, dict]:
    """Every git repo directly under each root, keyed by what it IS, not where it is."""
    found: dict[str, dict] = {}
    skipped = {str(s).lower() for s in skip}
    for root in roots:
        base = Path(os.path.expanduser(str(root)))
        if not base.is_dir():
            logger.info("evidence: no root at %s", base)
            continue
        for child in sorted(base.iterdir()):
            try:
                is_repo = child.is_dir() and (child / ".git").exists()
            except OSError:
                continue
            if is_repo:
                _consider(found, child, owner, skipped, merges)
    return found


def github_repos(owner: str) -> dict[str, dict]:
    """The owner's repos as GitHub lists them, keyed by name. {} without `gh`."""
    out = _run(["gh", "repo", "list", owner, "--limit", "300", "--json",
                "name,isFork,isPrivate,homepageUrl"])
    try:
        return {r["name"]: r for r in json.loads(out or "[]")}
    except (ValueError, TypeError, KeyError):
        return {}


def mirror(owner: str, name: str, dest: Path) -> Path | None:
    """A local copy of a repo that exists only on GitHub, so it is counted like the rest.

    Refreshed by fast-forward rather than re-cloned. Never pushed to and never
    edited: it is a read-only view that happens to need a working tree.
    """
    target = dest / name
    if (target / ".git").is_dir():
        _run(["git", "-C", str(target), "pull", "--ff-only", "--quiet"], timeout=120)
    else:
        dest.mkdir(parents=True, exist_ok=True)
        _run(["gh", "repo", "clone", f"{owner}/{name}", str(target), "--", "--quiet"],
             timeout=300)
    return target if (target / ".git").is_dir() else None


_LEDGER_ROW = re.compile(
    r'<div class="skill-row"[^>]*>\s*<span class="skill-name">(.*?)</span>'
    r'\s*<span class="skill-where">(.*?)</div>', re.S)
_LEDGER_WHERE = re.compile(r'data-proj="[^"]*">([^<]*)')
_FAMILIAR = re.compile(r'<div class="skill-aside-list">(.*?)</div>', re.S)


def read_ledger(path: str) -> dict:
    """The portfolio's Skills section: each tool, and the work it was used in.

    Already provenance-checked - four technologies failed that check and came off
    the page - so this is the shortlist of what may be claimed. Its "also familiar"
    list is the opposite: stacks the work does NOT show, which a draft must never
    present as experience.
    """
    if not path:
        return {}
    try:
        text = Path(os.path.expanduser(path)).read_text(encoding="utf-8")
    except OSError as exc:
        logger.info("evidence: no portfolio ledger at %s (%s)", path, exc)
        return {}
    used = {
        html.unescape(name).strip(): [html.unescape(w).strip()
                                      for w in _LEDGER_WHERE.findall(where) if w.strip()]
        for name, where in _LEDGER_ROW.findall(text)
    }
    out: dict = {}
    if used:
        out["used_in"] = used
    if match := _FAMILIAR.search(text):
        familiar = [html.unescape(s).strip()
                    for s in re.findall(r"<span>([^<]*)</span>", match.group(1))]
        if familiar:
            out["familiar_not_shown"] = familiar
    return out


def _testish(path: Path) -> bool:
    folders = {part.lower() for part in path.parts[:-1]}
    name = path.name.lower()
    return (any(f in TEST_DIRS or f.endswith("snapshots") for f in folders)
            or ".test." in name or ".spec." in name or name.startswith("test_"))


def pr_files(paths: list[str]) -> dict:
    """A merged PR's changed paths, sorted into what an employer asks about.

    The paths kept are ranked source first, then tests, then snapshots, then the
    rest, because they are talking points. GitHub lists them alphabetically, which
    put a changeset file and three snapshot lists ahead of the XAxis.tsx a recharts
    PR was actually about.
    """
    languages: dict[str, int] = {}
    tests = snapshots = 0
    rank: dict[str, int] = {}
    for path in paths:
        p = Path(path)
        suffix = p.suffix.lower()
        testish = _testish(p)
        if suffix in IMAGE_SUFFIXES and testish:
            snapshots += 1
            rank[path] = 2
        elif language := LANGUAGES.get(suffix):
            languages[language] = languages.get(language, 0) + 1
            tests += 1 if testish else 0
            rank[path] = 1 if testish else 0
        else:
            rank[path] = 3
    out: dict = {"files_changed": len(paths),
                 "paths": sorted(paths, key=lambda p: rank[p])[:12]}
    if languages:
        out["files_by_language"] = dict(sorted(languages.items(), key=lambda kv: -kv[1]))
    if tests:
        out["test_files"] = tests
    if snapshots:
        out["snapshot_images"] = snapshots
    return out


def _pr_detail(repo: str, number, owner: str) -> dict:
    """What one merged PR changed, and who let it in, from GitHub's own record.

    From the PR rather than a local clone: the PR is what was merged, while a
    clone can be sitting on a branch that never was. Paths are kept and contents
    are not - "touched the XAxis tests" is a talking point, the diff is not. The
    reviewers are the evidence for "works with other engineers", which a solo
    career otherwise has no way to show.
    """
    meta = _run(["gh", "api", f"repos/{repo}/pulls/{number}", "--jq",
                 '[.additions, .deletions, .commits, (.merged_by.login // "")] | @tsv'])
    files = _run(["gh", "api", "--paginate", f"repos/{repo}/pulls/{number}/files",
                  "--jq", ".[] | .filename"])
    reviews = _run(["gh", "api", "--paginate", f"repos/{repo}/pulls/{number}/reviews",
                    "--jq", ".[] | .user.login"])
    detail: dict = {}
    fields = meta.split("\t")
    if len(fields) == 4 and fields[0].isdigit() and fields[1].isdigit():
        detail["additions"], detail["deletions"] = int(fields[0]), int(fields[1])
        if fields[2].isdigit():
            detail["commits"] = int(fields[2])
        if fields[3]:
            detail["merged_by"] = fields[3]
    if paths := [line.strip() for line in files.splitlines() if line.strip()]:
        detail.update(pr_files(paths))
    reviewers = sorted({r for r in reviews.splitlines()
                        if r and r.lower() != owner.lower() and not r.endswith("[bot]")})
    if reviewers:
        detail["reviewed_by"] = reviewers
    return detail


def merged_prs(owner: str, previous: list[dict] | None = None,
               skip: set[str] | None = None) -> list[dict]:
    """Pull requests the owner got merged into other people's projects.

    Through the search API, because `gh search prs` returned nothing for every
    query on the day this was checked, which reads as "no contributions" rather
    than as a broken endpoint. Each carries the upstream's star count: "merged into
    recharts" means nothing to a reader who does not know what recharts is.
    """
    out = _run(["gh", "api", "-X", "GET", "search/issues",
                "-f", f"q=author:{owner} is:pr is:merged -user:{owner}",
                "-f", "per_page=100"])
    try:
        items = json.loads(out or "{}").get("items") or []
    except (ValueError, AttributeError):
        return []
    # A merged PR never changes, so its detail is fetched once and then carried.
    known = {(p.get("repo"), p.get("number")): p.get("detail")
             for p in previous or [] if p.get("detail")}
    stars: dict[str, int | None] = {}
    prs = []
    for item in items:
        repo = str(item.get("repository_url") or "").split("/repos/", 1)[-1]
        if repo.split("/")[-1].lower() in (skip or set()):
            continue
        if repo not in stars:
            count = _run(["gh", "api", f"repos/{repo}", "--jq", ".stargazers_count"])
            stars[repo] = int(count) if count.isdigit() else None
        merged = ((item.get("pull_request") or {}).get("merged_at")
                  or item.get("closed_at") or "")
        number = item.get("number")
        detail = known.get((repo, number)) or _pr_detail(repo, number, owner)
        prs.append({"repo": repo, "number": number,
                    "title": item.get("title"), "merged": merged[:10],
                    "url": item.get("html_url"), "repo_stars": stars[repo],
                    **({"detail": detail} if detail else {})})
    return sorted(prs, key=lambda p: (p["repo"], p["merged"]))


def index_all(settings: dict, fetch: bool = True, previous: dict | None = None) -> dict:
    """The whole counted half of the index, as one payload.

    `settings` is config.yaml's `evidence:` block. `roots` are searched; `repos`
    are added as given, for anything outside a root. With `fetch`, repos that exist
    only on GitHub are mirrored and merged upstream PRs are read. Without it the
    network is never touched: mirrors already on disk are still counted, and what
    only GitHub knows (forks, visibility, PRs) is carried over from `previous`.
    """
    owner = str(settings.get("github_user") or "")
    authors = tuple(str(a) for a in settings.get("authors") or ())
    skipped = {str(s).lower() for s in settings.get("skip") or ()}
    mirror_dir = Path(os.path.expanduser(str(settings.get("mirror_dir") or "./data/mirrors")))
    previous = previous or {}
    online = bool(fetch and owner)

    merges: list[str] = []
    found = discover(list(settings.get("roots") or []), owner, skipped, merges)
    for extra in settings.get("repos") or []:
        path = Path(os.path.expanduser(str(extra)))
        if (path / ".git").exists():
            _consider(found, path, owner, skipped, merges)

    github = github_repos(owner) if online else {}
    by_lower = {name.lower(): meta for name, meta in github.items()}
    forks = ({n for n, m in by_lower.items() if m.get("isFork")} if github
             else {str(f).lower() for f in previous.get("github_forks") or ()})

    if online:
        for name, meta in github.items():
            key = name.lower()
            if not meta.get("isFork") and key not in found and key not in skipped:
                mirror(owner, name, mirror_dir)
    if mirror_dir.is_dir():
        for child in sorted(mirror_dir.iterdir()):
            if (child / ".git").is_dir() and child.name.lower() not in found:
                _consider(found, child, owner, skipped, merges)

    previous_repos = previous.get("repos") or {}
    repos: dict[str, dict] = {}
    not_counted: dict[str, str] = {}
    for key, entry in sorted(found.items()):
        if key in forks:
            continue
        facts = scan_repo(entry["path"], authors)
        if not facts:
            continue
        name = entry["name"]
        if reason := facts.get("not_counted"):
            not_counted[name] = reason
            continue
        facts["name"] = name
        if entry["path"].name != name:
            facts["folder"] = entry["path"].name
        if entry["path"].parent.resolve() == mirror_dir.resolve():
            facts["mirrored_from_github"] = True
        if not entry["slug"]:
            facts["remote"] = "none"
        if meta := by_lower.get(key):
            facts["github"] = {"visibility": "private" if meta.get("isPrivate") else "public"}
            if meta.get("homepageUrl"):
                facts["github"]["homepage"] = meta["homepageUrl"]
        elif carried := (previous_repos.get(name) or {}).get("github"):
            facts["github"] = carried
        repos[name] = facts

    payload: dict = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "note": (
            "GENERATED by `python -m jobsift --index-repos`. Every number here was "
            "counted from files on disk. Do not hand-edit: the point is that it "
            "cannot drift from the code the way a resume does."
        ),
        "fetched_from_github": online and bool(github),
        "counts_cover": ("only the files and dependencies the candidate's own commits added"
                         if authors else "every tracked file, because no `authors` are configured"),
        "repos": repos,
    }
    if not_counted:
        payload["not_counted"] = not_counted
    if merges:
        payload["merged_clones"] = merges
    if forks:
        payload["github_forks"] = sorted(forks)
    if ledger := read_ledger(str(settings.get("portfolio_html") or "")):
        payload["portfolio_skills"] = ledger
    # A merged PR does not un-merge, so an empty answer is far likelier to be the
    # API failing than the history changing. Keep the last good list.
    carried = list(previous.get("open_source") or [])
    prs = (merged_prs(owner, carried, skipped) if online else []) or carried
    # A repo in `skip` is left out entirely, and so is a PR into it: a fix for a
    # relative's site is real work, but not an upstream contribution to cite.
    prs = [p for p in prs if str(p.get("repo") or "").split("/")[-1].lower() not in skipped]
    if prs:
        payload["open_source"] = prs
    return payload


def write_evidence(settings: dict, fetch: bool = True) -> dict:
    """Count everything and write it. Returns the payload so a caller can report on it."""
    import yaml

    destination = Path(str(settings.get("out") or "./data/evidence.yaml"))
    payload = index_all(settings, fetch, load_evidence(str(destination)))
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
                           encoding="utf-8")
    logger.info("evidence: indexed %d repo(s) into %s", len(payload["repos"]), destination)
    return payload


def load_evidence(destination: str) -> dict:
    """The stored payload, or {} when there is not one. Never raises."""
    import yaml

    try:
        data = yaml.safe_load(Path(destination).read_text(encoding="utf-8")) or {}
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as exc:
        logger.warning("evidence: could not read %s (%s)", destination, exc)
        return {}


def load_index(destination: str) -> dict:
    """The stored repos, or {} when there is not one. Never raises."""
    return load_evidence(destination).get("repos") or {}


def stale_repos(evidence: dict) -> list[str]:
    """Repos with commits made after the count. Empty means the index is current.

    The check the tailoring skill runs instead of deciding for itself whether to go
    researching: one git call per repo, no model, and a yes-or-no answer.

    Commit times are compared as Unix seconds (%ct), never parsed from text. The
    ISO form (%cI) failed on CI's first run under Python 3.10 and nowhere else:
    git 2.40 here writes a UTC commit as "+00:00", the runner's git 2.55 wrote
    something 3.10's fromisoformat rejects (by elimination, "Z", which 3.11+
    accepts), and the error was swallowed, so a stale index read as current.
    """
    try:
        built = datetime.fromisoformat(str(evidence.get("generated_at"))).timestamp()
    except ValueError:
        return ["(the index has never been built)"]
    out = []
    for name, facts in (evidence.get("repos") or {}).items():
        if not facts.get("path"):
            continue
        stamp = _git(Path(facts["path"]), "log", "-1", "--format=%ct")
        if stamp.isdigit() and int(stamp) > built:
            out.append(name)
    return out


def summarise(index: dict) -> str:
    """A compact block for a prompt. One repo per paragraph, facts only."""
    lines: list[str] = []
    for name, facts in index.items():
        bits: list[str] = []
        if facts.get("commits"):
            authored = facts.get("authored_commits")
            mine = (f", {authored} by the candidate"
                    if authored is not None and authored != facts["commits"] else "")
            bits.append(f"{facts['commits']} commits{mine} "
                        f"{facts.get('first_commit', '?')} to {facts.get('last_commit', '?')}")
        if languages := facts.get("files_by_language"):
            bits.append("files: " + ", ".join(f"{v} {k}" for k, v in languages.items()))
        if deps := facts.get("dependencies"):
            bits.append("dependencies: " + ", ".join(deps))
        if platforms := facts.get("automation_platforms"):
            bits.append("automation platforms: " + ", ".join(platforms))
        if ci := facts.get("ci"):
            bits.append("CI: " + ", ".join(ci))
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
