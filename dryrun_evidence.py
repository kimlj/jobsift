"""Dry run of the evidence index - offline, no LLM, writes only to a temp dir.

The index widens what a draft may claim, so its failures are the expensive kind:
a second clone of one repo double-counts it, a folder name taken for identity
hides a project, upstream code gets counted as the candidate's own, and a curated
fact whose file was deleted keeps being trusted. Each of those is built here on
purpose, in throwaway repos, and must be caught.

    .venv\\Scripts\\python.exe dryrun_evidence.py
"""

from __future__ import annotations

import copy
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from jobsift import career, evidence

FAILED = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global FAILED
    FAILED += 0 if ok else 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  ({detail})" if detail and not ok else ""))


def git(repo: Path, *args: str, env: dict | None = None) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                   env={**os.environ, **(env or {})})


def make_repo(path: Path, origin: str, files: dict[str, str],
              commits: list[str] = ("Kim <kim@example.com>",)) -> Path:
    path.mkdir(parents=True)
    git(path, "init", "-q")
    for rel, text in files.items():
        (path / rel).parent.mkdir(parents=True, exist_ok=True)
        (path / rel).write_text(text, encoding="utf-8")
    git(path, "add", "-A")
    for number, author in enumerate(commits):
        git(path, "-c", "user.name=Test", "-c", "user.email=test@example.com",
            "commit", "-q", "--allow-empty", "-m", f"commit {number}", "--author", author)
    if origin:
        git(path, "remote", "add", "origin", origin)
    return path


def add_commit(path: Path, author: str, files: dict[str, str]) -> None:
    for rel, text in files.items():
        (path / rel).write_text(text, encoding="utf-8")
    git(path, "add", "-A")
    git(path, "-c", "user.name=Test", "-c", "user.email=test@example.com",
        "commit", "-q", "-m", "more", "--author", author)


LEDGER = """<section id="skills"><div class="skill-ledger">
<div class="skill-row" data-projs="a b"><span class="skill-name">TypeScript</span><span class="skill-where"><span data-proj="a">Shop</span><span data-proj="b">Miner &mdash; bot</span></span></div>
</div><div class="skill-aside-list"><span>Svelte</span><span>tRPC</span><span>Zustand</span><span>GitHub Actions</span></div></section>"""

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp) / "home"
    root.mkdir()
    make_repo(root / "shop", "https://github.com/kim/Shop.git", {
        "web/package.json": '{"dependencies": {"@privy-io/react-auth": "1", "left-pad": "1", "zustand": "4"}}',
        "api/server.ts": "export {}",
        "node_modules/pkg/index.js": "module.exports = 1",
        "supabase/migrations/001.sql": "create policy a on t; create policy b on t;",
        "README.md": "shop",
        ".github/workflows/ci.yml": "on: push",
    }, commits=["Kim <kim@example.com>"] * 3)
    # Another repo cloned INSIDE this one, the way jobsift keeps its mirrors in data/.
    make_repo(root / "shop" / "data" / "mirror", "https://github.com/kim/Other",
              {"a.ts": "", "b.ts": "", "c.tsx": ""})
    make_repo(root / "shop-old", "git@github.com:kim/Shop.git", {"a.ts": ""})
    make_repo(root / "ore-cli", "https://github.com/kim/ore-cli-codex", {"x.ts": ""},
              commits=["Upstream <up@example.com>"] * 2 + ["Kim <kim@example.com>"] * 3)
    make_repo(root / "somebody-lib", "https://github.com/someone/lib.git", {"y.js": ""})
    make_repo(root / "scratch", "", {"z.py": ""})
    # Somebody else's code with the remote gone, and a folder that was never committed.
    make_repo(root / "tutorial", "", {"t.ts": ""}, commits=["Someone <someone@example.com>"])
    make_repo(root / "empty", "", {"e.ts": ""}, commits=[])
    # Somebody else's project re-pushed under your account, with one commit of yours on
    # top. Multi-line JSON on purpose: adding zod rewrites the prisma line for its comma.
    mixed = make_repo(root / "mixed", "https://github.com/kim/mixed", {
        "a.ts": "", "b.ts": "",
        "package.json": '{\n  "dependencies": {\n    "prisma": "5"\n  }\n}\n',
    }, commits=["Upstream <up@example.com>"])
    add_commit(mixed, "Kim <kim@example.com>", {
        "c.ts": "", "package.json": '{\n  "dependencies": {\n    "prisma": "5",\n    "zod": "3"\n  }\n}\n'})
    # A copy of one of your repos made from another folder, so it has no GitHub remote.
    subprocess.run(["git", "clone", "-q", str(root / "shop"), str(root / "shop-copy")],
                   check=True, capture_output=True)
    (Path(tmp) / "index.html").write_text(LEDGER, encoding="utf-8")

    settings = {"roots": [str(root)], "github_user": "kim", "authors": ["kim@example.com"],
                "mirror_dir": str(Path(tmp) / "mirrors"),
                "portfolio_html": str(Path(tmp) / "index.html")}
    payload = evidence.index_all(settings, fetch=False)
    repos = payload["repos"]

    print("── discovery ──")
    check("two clones of one remote count once",
          set(repos) == {"ore-cli-codex", "scratch", "Shop", "mixed"}, str(sorted(repos)))
    check("the clone with more commits wins", repos.get("Shop", {}).get("commits") == 3)
    check("identity comes from the remote, not the folder",
          repos.get("ore-cli-codex", {}).get("folder") == "ore-cli")
    check("somebody else's repo is not counted", "lib" not in repos and "somebody-lib" not in repos)
    check("a repo with no remote is kept and marked", repos.get("scratch", {}).get("remote") == "none")

    print("── authorship ──")
    skipped_repos = payload.get("not_counted", {})
    check("a repo with none of your commits is not counted",
          "tutorial" in skipped_repos and "tutorial" not in repos, str(skipped_repos))
    check("a repo with no commits is not counted", "empty" in skipped_repos, str(skipped_repos))
    mixed_facts = repos.get("mixed", {})
    check("only files your commits touched are counted",
          mixed_facts.get("files_by_language") == {"TypeScript": 1}, str(mixed_facts.get("files_by_language")))
    check("only dependencies your commits added are counted, despite the comma",
          mixed_facts.get("dependencies") == ["zod"], str(mixed_facts.get("dependencies")))
    check("a copy with no remote folds into the repo it came from",
          "shop-copy" not in repos and bool(payload.get("merged_clones")), str(payload.get("merged_clones")))
    brief_all, _ = career.build_brief({}, payload, Path(tmp))
    check("the brief tells the drafter never to cite them", "Never cite: " in brief_all
          and "tutorial" in brief_all.split("Never cite: ")[1])

    print("── counting ──")
    shop = repos.get("Shop", {})
    check("a sub-package manifest is read", "@privy-io/react-auth" in shop.get("dependencies", []),
          str(shop.get("dependencies")))
    check("uninteresting dependencies stay out", "left-pad" not in shop.get("dependencies", []))
    check("node_modules is not counted", shop.get("files_by_language", {}).get("JavaScript") is None,
          str(shop.get("files_by_language")))
    check("a repo cloned inside it is not counted as its code",
          shop.get("files_by_language", {}).get("TypeScript") == 1, str(shop.get("files_by_language")))
    check("policies counted from migrations", shop.get("sql", {}).get("migrations", {}).get("rls_policies") == 2)
    ore = repos.get("ore-cli-codex", {})
    check("authored commits exclude upstream history",
          ore.get("commits") == 5 and ore.get("authored_commits") == 3, str(ore))

    kept = evidence.index_all({**settings, "skip": ["skipme"]}, fetch=False, previous={
        "open_source": [{"repo": "someone/skipme", "number": 1}, {"repo": "big/lib", "number": 2}]})
    check("a merged PR into a skipped repo is left out",
          [p["repo"] for p in kept.get("open_source") or []] == ["big/lib"], str(kept.get("open_source")))

    print("── merged PRs ──")
    files = evidence.pr_files(["src/cartesian/XAxis.tsx", "test/cartesian/XAxis.spec.tsx",
                               "test/__snapshots__/area-chromium.png", "src/util/latestValue.ts",
                               "CHANGELOG.md"])
    check("a PR's files sort into languages, tests and snapshots",
          files.get("files_by_language") == {"TypeScript": 3} and files.get("test_files") == 1
          and files.get("snapshot_images") == 1 and files.get("files_changed") == 5, str(files))
    check("a PR's paths lead with source, not whatever sorts first",
          files.get("paths", [])[:2] == ["src/cartesian/XAxis.tsx", "src/util/latestValue.ts"]
          and files.get("paths", [])[-1] == "CHANGELOG.md", str(files.get("paths")))
    with_detail = {**payload, "open_source": [{
        "repo": "big/lib", "number": 7, "title": "fix: a thing", "merged": "2026-07-20",
        "url": "https://github.com/big/lib/pull/7", "repo_stars": 27000,
        "detail": {**files, "additions": 120, "deletions": 14,
                   "merged_by": "maint", "reviewed_by": ["maint", "other"]}}]}
    brief_pr, _ = career.build_brief({}, with_detail, Path(tmp))
    check("the brief says what a PR changed and who reviewed it",
          "5 files, +120/-14 lines: 3 TypeScript, 1 test file, 1 snapshot image" in brief_pr
          and "reviewed by maint, other" in brief_pr, brief_pr[-500:])

    print("── portfolio ledger ──")
    ledger = payload.get("portfolio_skills", {})
    check("tools map to the work they were used in",
          ledger.get("used_in", {}).get("TypeScript") == ["Shop", "Miner — bot"], str(ledger))
    check("the not-shown list is kept apart",
          ledger.get("familiar_not_shown") == ["Svelte", "tRPC", "Zustand", "GitHub Actions"])
    brief, _ = career.build_brief({}, payload, Path(tmp))
    check("a 'not shown' entry the counts DO show is corrected, not repeated",
          "Zustand (Shop)" in brief and "experience: Svelte, tRPC\n" in brief, brief[-400:])
    check("CI counts as shown, though no dependency names it",
          shop.get("ci") == ["GitHub Actions"] and "GitHub Actions (Shop)" in brief, str(shop.get("ci")))

    print("── curated sources ──")
    curated = {
        "projects": [{
            "id": "shop", "name": "Acme Shop", "kind": "production", "repos": ["Shop", "Gone"],
            "facts": [
                {"text": "TRUSTED-FACT", "source": "Shop/README.md"},
                {"text": "FOLDER-ALIAS-FACT", "source": "ore-cli/x.ts"},
                {"text": "DELETED-FILE-FACT", "source": "Shop/docs/missing.md"},
                {"text": "OWNER-FACT", "source": "owner:2026-09-10"},
                {"text": "UNDATED-OWNER-FACT", "source": "owner:yesterday"},
                "BARE-STRING-FACT",
            ],
        }],
        "capabilities": [{"name": "Laravel", "status": "absent"},
                         {"name": "Vue", "status": "shown"}],
        "rules": [{"id": "broken", "when": ["x"]}],
    }
    problems = career.validate(curated, payload, Path(tmp))
    joined = "\n".join(problems)
    check("a missing file is reported", "missing.md" in joined, joined)
    check("an undated owner source is reported", "yesterday" in joined)
    check("a fact with no source is reported", "fact 6: no source given" in joined)
    check("a repo that was never counted is reported", "repo Gone" in joined)
    check("a claimed capability needs a source", "capability Vue" in joined)
    check("an absence needs no source", "capability Laravel" not in joined)
    check("a half-written rule is reported", "rule broken" in joined)
    brief, _ = career.build_brief(curated, payload, Path(tmp))
    check("trusted facts reach the brief",
          all(t in brief for t in ("TRUSTED-FACT", "FOLDER-ALIAS-FACT", "OWNER-FACT")))
    check("failing facts are left out of the brief",
          not any(t in brief for t in ("DELETED-FILE-FACT", "UNDATED-OWNER-FACT", "BARE-STRING-FACT")))
    check("uncurated repos are still listed as counts", "## Other repos" in brief and "scratch" in brief)

    print("── rules ──")
    rules = [{"id": "rate", "when": ["570"], "require": ["daily"]},
             {"id": "name", "forbid": ["Multiwordle"]}]
    check("a lifetime total without its rate fires",
          [h["rule"] for h in career.check_text("570 players reached a game", rules)] == ["rate"])
    check("with its rate it does not", not career.check_text("570 players, 40 to 70 daily", rules))
    check("a forbidden name fires", career.check_text("I built multiwordle", rules)[0]["rule"] == "name")
    check("a number inside a bigger number does not", not career.check_text("5700 players", rules))

    print("── refresh ──")
    refresh_settings = {**settings, "out": str(Path(tmp) / "evidence.yaml")}
    career_file, brief_file = Path(tmp) / "career.yaml", Path(tmp) / "brief.md"
    career_file.write_text("projects: []\n", encoding="utf-8")
    first = career.refresh(refresh_settings, str(career_file), str(brief_file), online=False)
    check("with no index, a refresh builds one",
          first["action"] == "offline" and brief_file.is_file(), str(first))
    second = career.refresh(refresh_settings, str(career_file), str(brief_file), online=False)
    check("with nothing changed, a refresh does nothing", second["action"] == "none", str(second))
    later = brief_file.stat().st_mtime + 10
    os.utime(career_file, (later, later))
    third = career.refresh(refresh_settings, str(career_file), str(brief_file), online=False)
    check("an edited career.yaml rebuilds it",
          third["action"] == "offline" and "edited" in " ".join(third["why"]), str(third))
    now = datetime.now()
    ago = lambda hours: {"github_checked_at": (now - timedelta(hours=hours)).isoformat(timespec="seconds")}
    check("GitHub is due after a day and not before",
          career.github_due(ago(25), {"github_user": "kim"}, 24, now)
          and not career.github_due(ago(23), {"github_user": "kim"}, 24, now))
    check("GitHub is due if never read, and never due without a GitHub user",
          career.github_due({}, {"github_user": "kim"}) and not career.github_due({}, {}))

    print("── staleness ──")
    check("a fresh index is current", evidence.stale_repos(payload) == [], str(evidence.stale_repos(payload)))
    git(root / "scratch", "-c", "user.name=T", "-c", "user.email=t@example.com",
        "commit", "-q", "--allow-empty", "-m", "later",
        env={"GIT_COMMITTER_DATE": "2099-01-01T00:00:00+00:00"})
    check("a commit after the count makes it stale", evidence.stale_repos(payload) == ["scratch"])

    print("── incremental ──")
    before = copy.deepcopy(payload)
    before["repos"]["ore-cli-codex"]["test_files"] = 999      # a marker no real count writes
    again = evidence.index_all(settings, fetch=False, previous=before)
    check("a repo still on the counted commit is not counted again",
          again["repos"]["ore-cli-codex"].get("test_files") == 999)
    add_commit(root / "ore-cli", "Kim <kim@example.com>", {"y.ts": ""})
    again = evidence.index_all(settings, fetch=False, previous=before)
    check("a repo with a new commit is",
          again["repos"]["ore-cli-codex"].get("test_files") != 999
          and again["repos"]["ore-cli-codex"].get("files_by_language") == {"TypeScript": 1},
          str(again["repos"]["ore-cli-codex"]))
    old_rules = copy.deepcopy(payload)
    old_rules["count_version"] = -1
    old_rules["repos"]["Shop"]["test_files"] = 999
    check("a change to what gets counted counts every repo again",
          evidence.index_all(settings, fetch=False, previous=old_rules)["repos"]["Shop"]
          .get("test_files") != 999)
    other_author = copy.deepcopy(payload)
    other_author["authors"] = ["someone-else@example.com"]
    other_author["repos"]["Shop"]["test_files"] = 999
    check("so does a change to whose commits count",
          evidence.index_all(settings, fetch=False, previous=other_author)["repos"]["Shop"]
          .get("test_files") != 999)

print()
print("all checks passed" if not FAILED else f"{FAILED} check(s) FAILED")
sys.exit(1 if FAILED else 0)
