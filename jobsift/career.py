"""The hand-written half of the evidence index, and the brief both halves become.

evidence.py counts. It cannot know that `Multiwordle` is WordWarz.io, that
shift-ops-console runs on a simulated workforce, or that "570 players" is a
lifetime total which never travels without the daily count. Those facts lived in
a chat assistant's memory, which the drafter here cannot read and no other tool
can see, so every tailored application re-derived them by researching GitHub
again: about twenty tool calls a posting, to rediscover the same projects.

career.yaml holds them instead, under the rule evidence.py already keeps: every
statement names where it can be checked. The build refuses to trust a line whose
source does not exist, so a curated fact cannot quietly outlive the file that
proved it. Rules - "if you say X you must also say Y", "never say Z" - are
checked in code against a finished draft, because a rule a model is asked to
remember is a rule it drops the first time a sentence has to be shortened.

The brief is the one document everything reads: the drafter and verifier in
agents.py, and the tailoring skill, which used to rebuild this corpus per posting.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path

from .evidence import summarise

logger = logging.getLogger(__name__)

KINDS = ("production", "shipped", "in_development", "demonstrator", "hobby", "coursework")
STATUSES = ("shown", "partial", "todo", "absent")


def load(path: str) -> dict:
    """career.yaml, or {} when there is not one. Never raises."""
    import yaml

    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as exc:
        logger.warning("career: could not read %s (%s)", path, exc)
        return {}


def _items(value) -> list[dict]:
    """Facts and cautions as dicts. A bare string has no source, and says so."""
    out = []
    for item in value or []:
        out.append(item if isinstance(item, dict) else {"text": str(item), "source": ""})
    return out


def _repo_roots(evidence: dict) -> dict[str, Path]:
    """Every counted repo, reachable by its GitHub name and by its folder name."""
    roots: dict[str, Path] = {}
    for name, facts in (evidence.get("repos") or {}).items():
        path = Path(str(facts.get("path") or ""))
        roots[name.lower()] = path
        roots.setdefault(path.name.lower(), path)
    return roots


def check_source(source: str, roots: dict[str, Path], base: Path) -> str:
    """"" when `source` resolves, otherwise why it does not.

    Accepted forms, tried in this order:
      https://...             a public page. Not fetched: it is what an employer
                              checks, and a build should not need the network.
      owner:YYYY-MM-DD        the owner said so, on that date. For what no file holds.
      <repo>/<path>[@<ref>]   a file in a counted repo, by GitHub or folder name.
                              @ref checks a branch that is not checked out.
      <path>                  a file or folder, relative to jobsift or absolute (~ ok).
    A #fragment is ignored, so `profile.yaml#browser_automation` checks the file.
    """
    source = str(source or "").strip()
    if not source:
        return "no source given"
    if source.startswith(("http://", "https://")):
        return ""
    if source.startswith("owner:"):
        stamp = source[len("owner:"):].strip()
        return ("" if re.fullmatch(r"\d{4}-\d{2}-\d{2}", stamp)
                else f"an owner source needs a date, got {stamp!r}")

    source = source.split("#", 1)[0]
    ref = ""
    if "@" in source:
        source, ref = source.rsplit("@", 1)
    head, _, rest = source.replace("\\", "/").partition("/")
    repo = roots.get(head.lower())
    if repo is not None and rest:
        if ref:
            found = subprocess.run(
                ["git", "-C", str(repo), "cat-file", "-e", f"{ref}:{rest}"],
                capture_output=True, stdin=subprocess.DEVNULL, timeout=20, check=False,
            ).returncode == 0
            return "" if found else f"{rest} is not on {ref} in {repo.name}"
        return "" if (repo / rest).exists() else f"{rest} does not exist in {repo}"

    path = Path(os.path.expanduser(source))
    if not path.is_absolute():
        path = base / path
    return "" if path.exists() else f"{source} not found"


def validate(career: dict, evidence: dict, base: Path) -> list[str]:
    """Every problem that should stop a curated line being trusted, as readable lines."""
    roots = _repo_roots(evidence)
    counted = {name.lower() for name in evidence.get("repos") or {}}
    problems: list[str] = []

    def need(where: str, source) -> None:
        if why := check_source(str(source or ""), roots, base):
            problems.append(f"{where}: {why}")

    seen: set[str] = set()
    for project in career.get("projects") or []:
        pid = str(project.get("id") or project.get("name") or "?")
        if pid in seen:
            problems.append(f"project {pid}: duplicate id")
        seen.add(pid)
        if project.get("kind") and project["kind"] not in KINDS:
            problems.append(f"project {pid}: kind {project['kind']!r} is not one of "
                            + ", ".join(KINDS))
        for repo in project.get("repos") or []:
            if str(repo).lower() not in counted:
                problems.append(f"project {pid}: repo {repo} is not in the counted index")
        for number, fact in enumerate(_items(project.get("facts")), 1):
            need(f"project {pid} fact {number}", fact.get("source"))
        for number, caution in enumerate(_items(project.get("cautions")), 1):
            need(f"project {pid} caution {number}", caution.get("source"))
        for story in project.get("stories") or []:
            need(f"project {pid} story", story)

    for capability in career.get("capabilities") or []:
        name = str(capability.get("name") or "?")
        status = capability.get("status")
        if status not in STATUSES:
            problems.append(f"capability {name}: status {status!r} is not one of "
                            + ", ".join(STATUSES))
        # An absence needs no source - there is nothing to point at - but a claim
        # that something exists does.
        if status in ("shown", "partial"):
            need(f"capability {name}", capability.get("source"))

    for rule in career.get("rules") or []:
        if not (rule.get("forbid") or (rule.get("when") and rule.get("require"))):
            problems.append(f"rule {rule.get('id', '?')}: needs `forbid`, "
                            "or both `when` and `require`")
    return problems


def _mentions(text: str, term: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(term)}(?!\w)", text, re.I) is not None


def check_text(text: str, rules: list[dict]) -> list[dict]:
    """Rule violations in one finished document. Pure string tests, no model.

    Deliberately blunt. A rule fires on the words, not the meaning, so "not a
    physician" still trips `forbid: [physician]` - which is the right outcome,
    because that sentence should not be in an application either.
    """
    hits = []
    for rule in rules or []:
        rid = str(rule.get("id") or "rule")
        why = str(rule.get("why") or "")
        for term in rule.get("forbid") or []:
            if _mentions(text, str(term)):
                hits.append({"rule": rid, "problem": f"says {term!r}", "why": why})
        said = [str(t) for t in rule.get("when") or [] if _mentions(text, str(t))]
        required = [str(t) for t in rule.get("require") or []]
        if said and not any(_mentions(text, t) for t in required):
            hits.append({"rule": rid, "why": why,
                         "problem": f"says {said[0]!r} without any of {required}"})
    return hits


def _tokens(label: str) -> set[str]:
    """"Solana · web3.js" -> {"solana", "web3.js"}; "React Native" -> {"react-native"}."""
    return {part.strip().lower().replace(" ", "-")
            for part in re.split(r"[·/]", label) if part.strip()}


def _shown_by(item: str, repos: dict, capabilities: list[dict]) -> list[str]:
    """Where the counts, or a curated capability, show `item`. [] when nothing does.

    A portfolio's "also familiar" list is kept by hand and lags the work. This
    one listed React Native, Playwright and Solana as not shown while counted
    repos depended on all three, and a brief that repeated it would have told the
    drafter to deny real work - the exact failure the index exists to stop.
    """
    tokens = _tokens(item)
    where = sorted(
        name for name, facts in repos.items()
        if any(dep == t or dep.endswith("/" + t) or dep.startswith(f"@{t}/")
               for dep in [*(facts.get("dependencies") or []),
                           *(c.lower().replace(" ", "-") for c in facts.get("ci") or [])]
               for t in tokens))
    for capability in capabilities:
        if (capability.get("status") in ("shown", "partial")
                and tokens & _tokens(str(capability.get("name") or ""))):
            where.append(f"see capability {capability['name']}")
    return where


def _pr_line(detail: dict) -> str:
    """"5 files, +312/-40 lines: 4 TypeScript, 2 test files; merged by x; reviewed by y"."""
    if not detail:
        return ""
    size = f"{detail['files_changed']} files" if detail.get("files_changed") else ""
    if "additions" in detail:
        size += f"{', ' if size else ''}+{detail['additions']}/-{detail['deletions']} lines"
    kinds = [f"{count} {language}"
             for language, count in (detail.get("files_by_language") or {}).items()]
    if tests := detail.get("test_files"):
        kinds.append(f"{tests} test file{'s' if tests != 1 else ''}")
    if images := detail.get("snapshot_images"):
        kinds.append(f"{images} snapshot image{'s' if images != 1 else ''}")
    parts = [size + (": " + ", ".join(kinds) if kinds else "")]
    if detail.get("merged_by"):
        parts.append(f"merged by {detail['merged_by']}")
    if detail.get("reviewed_by"):
        parts.append("reviewed by " + ", ".join(detail["reviewed_by"]))
    if detail.get("paths"):
        parts.append("touched " + ", ".join(detail["paths"][:5]))
    return "; ".join(p for p in parts if p)


def _line(item: dict) -> str:
    source = str(item.get("source") or "")
    return f"- {str(item.get('text') or '').strip()}" + (f"  [{source}]" if source else "")


def build_brief(career: dict, evidence: dict, base: Path) -> tuple[str, list[str]]:
    """The brief, and the problems found while building it.

    A curated line whose source fails is LEFT OUT of the brief, not flagged inside
    it. A drafter shown "this may be false" beside a flattering sentence uses the
    sentence.
    """
    problems = validate(career, evidence, base)
    roots = _repo_roots(evidence)
    repos = evidence.get("repos") or {}
    by_lower = {name.lower(): name for name in repos}

    def trusted(item: dict) -> bool:
        return not check_source(str(item.get("source") or ""), roots, base)

    out = [
        "# Career evidence brief",
        "",
        f"Built {datetime.now():%Y-%m-%d %H:%M} from career.yaml and {len(repos)} "
        f"counted repos (counted {evidence.get('generated_at', 'never')}).",
        "Trusted in full: every line was counted from files, or written by the "
        "candidate with a source that was checked when this was built. Anything "
        "about the candidate that is not here, in resume.txt or in profile.yaml "
        "may not be claimed. Where a portfolio or hand-written figure disagrees "
        "with a count, the count wins: it was taken from the files at build time.",
        f"Counts cover {evidence.get('counts_cover', 'every tracked file')}.",
    ]
    capabilities = career.get("capabilities") or []

    curated: set[str] = set()
    projects = career.get("projects") or []
    if projects:
        out += ["", "## Projects"]
    for project in projects:
        kind = f" ({project['kind']})" if project.get("kind") else ""
        out += ["", f"### {project.get('name') or project.get('id')}{kind}"]
        about = [str(project[k]) for k in ("role",) if project.get(k)]
        if urls := project.get("urls"):
            about.append("live: " + ", ".join(urls))
        if names := project.get("repos"):
            about.append("repos: " + ", ".join(names))
        if about:
            out.append(" | ".join(about))
        counted = {}
        for repo in project.get("repos") or []:
            if name := by_lower.get(str(repo).lower()):
                counted[name] = repos[name]
                curated.add(name)
        if counted:
            out.append("Counted:")
            out += [f"  {line}" for line in summarise(counted).splitlines()]
        out += [_line(f) for f in _items(project.get("facts")) if trusted(f)]
        cautions = [c for c in _items(project.get("cautions")) if trusted(c)]
        if cautions:
            out.append("Cautions, binding on every draft:")
            out += [_line(c) for c in cautions]
        stories = [s for s in project.get("stories") or []
                   if not check_source(str(s), roots, base)]
        if stories:
            out.append("Real incidents to tell (read them; never invent one): "
                       + ", ".join(stories))

    others = {name: facts for name, facts in repos.items() if name not in curated}
    if others:
        out += ["", "## Other repos, counted but not described",
                "True as counted. What each one is, and whether it is worth "
                "mentioning, has not been written down, so claim only the counts."]
        out += summarise(others).splitlines()

    if not_counted := evidence.get("not_counted"):
        out += ["", "## On disk but NOT the candidate's work",
                "Found, but the commit history does not show the candidate writing it. "
                "Never cite: " + "; ".join(f"{name} ({why})" for name, why in not_counted.items())]

    ledger = evidence.get("portfolio_skills") or {}
    if used := ledger.get("used_in"):
        out += ["", "## Skills ledger (portfolio, provenance-checked)"]
        out += [f"- {tool}: {', '.join(where)}" for tool, where in used.items()]
    if familiar := ledger.get("familiar_not_shown"):
        shown = {item: _shown_by(item, repos, capabilities) for item in familiar}
        if unshown := [item for item, where in shown.items() if not where]:
            out += ["", "Listed on the portfolio as familiar but not shown, and no "
                    "counted repo shows them either. Never present these as "
                    "experience: " + ", ".join(unshown)]
        if behind := {item: where for item, where in shown.items() if where}:
            out += ["", "Listed on the portfolio as not shown, but the counts DO show "
                    "them. The page is behind the work, so cite the repo: "
                    + "; ".join(f"{item} ({', '.join(where)})" for item, where in behind.items())]

    if capabilities:
        out += ["", "## Capabilities a quick search gets wrong"]
    for capability in capabilities:
        status = str(capability.get("status") or "")
        if status in ("shown", "partial") and not trusted(capability):
            continue
        head = f"- {capability.get('name')}: {status.upper()}"
        if capability.get("checked"):
            head += f" (checked {capability['checked']})"
        parts = [head]
        if capability.get("where"):
            parts.append(str(capability["where"]).rstrip("."))
        if capability.get("limit"):
            parts.append("Limit: " + str(capability["limit"]).rstrip("."))
        if status == "absent":
            parts.append(f"Bridge: {str(capability['bridge']).rstrip('.')}"
                         if capability.get("bridge") else "No honest bridge: say so plainly")
        line = ". ".join(parts) + "."
        if capability.get("source"):
            line += f"  [{capability['source']}]"
        out.append(line)

    if prs := evidence.get("open_source"):
        out += ["", "## Pull requests merged upstream"]
        for pr in prs:
            # Zero is printed, not hidden: a PR into a small private site and one
            # into a 27,000-star library must not read the same.
            stars = (f", {pr['repo_stars']:,} stars" if pr.get("repo_stars") is not None else "")
            out.append(f"- {pr['repo']} #{pr['number']} (merged {pr['merged']}{stars}): "
                       f"{pr['title']}  [{pr['url']}]")
            if detail := _pr_line(pr.get("detail") or {}):
                out.append(f"  {detail}")

    if rules := career.get("rules"):
        out += ["", "## Rules checked in code against every draft"]
        for rule in rules:
            if rule.get("forbid"):
                test = "never write " + ", ".join(repr(t) for t in rule["forbid"])
            else:
                test = (f"writing {' or '.join(repr(t) for t in rule.get('when') or [])} "
                        f"requires {' or '.join(repr(t) for t in rule.get('require') or [])}")
            out.append(f"- {rule.get('id')}: {test}. {rule.get('why', '')}".rstrip())

    return "\n".join(out) + "\n", problems


def write_brief(career_path: str, evidence: dict, destination: str,
                base: Path | None = None) -> list[str]:
    """Build the brief and write it. Returns the problems, for the caller to print."""
    text, problems = build_brief(load(career_path), evidence, base or Path.cwd())
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    logger.info("career: wrote %s (%d chars, %d problem(s))", destination, len(text), len(problems))
    return problems


def github_age_hours(payload: dict, now: datetime | None = None) -> float | None:
    """Hours since the index last read GitHub; None when it never has."""
    try:
        checked = datetime.fromisoformat(str(payload.get("github_checked_at")))
    except ValueError:
        return None
    return ((now or datetime.now()) - checked).total_seconds() / 3600


def github_due(payload: dict, settings: dict, every_hours: float = 24,
               now: datetime | None = None) -> bool:
    """Whether GitHub should be read again. Never, for someone with no GitHub."""
    if not settings.get("github_user"):
        return False
    age = github_age_hours(payload, now)
    return age is None or age >= every_hours


def index_state(settings: dict, career_path: str, brief_path: str,
                github_every_hours: float = 24) -> dict:
    """What is out of date, without rebuilding anything.

    `local` lists what an offline rebuild would fix - new commits, an edited
    career.yaml, no brief at all. GitHub is reported separately because it
    changes on a different clock and costs a network round to check.
    """
    from .evidence import load_evidence, stale_repos

    payload = load_evidence(str(settings.get("out") or "./data/evidence.yaml"))
    brief = Path(brief_path)
    local: list[str] = []
    if not payload or not brief.is_file():
        local.append("no index has been built")
    else:
        local += [f"new commits in {name}" for name in stale_repos(payload)]
        career = Path(career_path)
        if career.is_file() and career.stat().st_mtime > brief.stat().st_mtime:
            local.append(f"{career_path} was edited")
    return {"local": local,
            "github_age_hours": github_age_hours(payload),
            "github_due": github_due(payload, settings, github_every_hours)}


def refresh(settings: dict, career_path: str, brief_path: str, online: bool = True,
            github_every_hours: float = 24) -> dict:
    """Rebuild the index if something changed, and only then. Never raises.

    Nobody has to remember to run this. jobsift's loop calls it hourly and every
    draft calls it first, because a draft is the moment a stale brief costs
    something. A local change rebuilds offline, and only the repos that got
    commits are counted again. GitHub - new repos, pushes to repos that live only
    there, merged PRs - is read at most once every `github_every_hours`, and even
    then only a mirror GitHub reports a push for is pulled. After a night asleep
    the first refresh therefore touches what changed, not the whole corpus.

    Returns {"action": "none" | "offline" | "online" | "failed", "why": [...]}.
    A failure leaves the last brief in place and says so; it never takes the
    pipeline or a draft down with it.
    """
    from .evidence import write_evidence

    try:
        if not (settings.get("roots") or settings.get("repos")):
            # Nothing to count on this machine: a core that drafts from the brief
            # a residential worker pushes. Rebuilding here would replace that
            # brief with one built from no repos at all.
            return {"action": "none", "why": []}
        state = index_state(settings, career_path, brief_path, github_every_hours)
        fetch = bool(online and state["github_due"])
        if not (state["local"] or fetch):
            return {"action": "none", "why": []}
        why = list(state["local"])
        if fetch:
            age = state["github_age_hours"]
            why.append("GitHub never checked" if age is None
                       else f"GitHub last checked {age:.0f}h ago")
        payload = write_evidence(settings, fetch=fetch)
        write_brief(career_path, payload, brief_path)
        logger.info("career: index refreshed %s (%s)",
                    "with GitHub" if fetch else "offline", "; ".join(why))
        return {"action": "online" if fetch else "offline", "why": why}
    except Exception as exc:
        logger.warning("career: index refresh failed (%s); the last brief stays in use", exc)
        return {"action": "failed", "why": [str(exc)]}
