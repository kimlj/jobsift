# The evidence index

A draft may only say about you what jobsift can check. On its own that means
`resume.txt` and `profile.yaml`, and a resume always lags the work: a letter once
told an employer the candidate had never written a row-level-security policy
while 35 of them sat in a repo on the same machine. The evidence index is the
rest of what you have built, in a form a draft is allowed to cite.

It also saves money. Without it, anything that tailors an application has to
research your GitHub again for every posting. One run of that measured about
twenty tool calls spent rediscovering the same projects.

## What it produces

| File | Written by | What it is |
|---|---|---|
| `data/evidence.yaml` | the build | Every repo you own, counted from its files: commits (yours separately from anyone else's), languages, the dependencies employers ask about, SQL migrations and policies, test files, CI. Plus your portfolio's skills list, if you have one, and pull requests you got merged into other people's projects: what each changed (files, lines, languages, tests), who merged it and who reviewed it. A merged PR never changes, so that detail is fetched once and kept for offline builds |
| `data/mirrors/` | the build | Clones of repos that exist only on GitHub, so they are counted by the same code as the rest. Read-only; never pushed |
| `career.yaml` | **you** | What the counts cannot know: which repo is which product, facts with a source for each, cautions, gaps with an honest bridge, and rules a draft must pass. Gitignored |
| `data/career-brief.md` | the build | Both of the above as one document of a few thousand words. The drafter, the verifier and any tailoring tool read this and nothing else about your work |

All of it is local. Nothing is uploaded anywhere except the brief itself, which
goes to your LLM provider as part of a draft, the same as your resume does.

## Setting it up after you fork

About fifteen minutes, most of it step 6.

**1. Install jobsift** as in the README quick start.

**2. Sign in to the GitHub CLI.** Optional, but without it only repos already on
your disk are counted, and merged pull requests to other projects are not found.

```bash
gh auth login
```

**3. Point `config.yaml` at your work.** The `evidence:` block:

```yaml
evidence:
  enabled: true
  github_user: yourhandle
  roots:                       # every git repo directly inside these is found
    - ~/code
    - ~/Documents
  authors: [you@example.com]   # your git author names or emails
```

Folder names do not matter: each repo is named by its GitHub remote, two clones
of one repo count once, and repos whose remote belongs to someone else are
skipped. To see which identities you have committed under:

```bash
git -C ~/code/some-repo shortlog -sne HEAD
```

**4. Build it.**

```bash
python -m jobsift --index-repos
```

It prints one line per repo, then two lists worth reading: curated lines whose
source it could not find (left out of the brief until fixed), and rules your
`resume.txt` breaks. The first run is the slow one, because it clones the
repos that are only on GitHub.

**5. Read `data/career-brief.md`.** Two sections are there to be acted on:

- *Other repos, counted but not described*: work the brief knows exists but
  cannot explain. Anything worth mentioning to an employer needs a line in
  `career.yaml`.
- *Listed on the portfolio as not shown, but the counts DO show them*: your own
  site is underselling you. Fix the site.

**6. Write `career.yaml`.**

```bash
cp career.example.yaml career.yaml
```

One entry per project worth claiming. The rule is that every fact names where it
can be checked, and the build tests that the source exists:

| Source | Means |
|---|---|
| `https://...` | a public page an employer can open |
| `owner:2026-09-10` | you said so, on that date. For what no file records |
| `<repo>/<path>` | a file in a counted repo, by its GitHub or folder name |
| `<repo>/<path>@<branch>` | the same, on a branch that is not checked out |
| `resume.txt`, `~/some/path` | any file on disk |

A line whose source does not resolve is left out of the brief rather than
trusted. Rebuild after editing: `python -m jobsift --index-repos --offline`.

**7. Update `resume.txt` yourself.** The index never edits it. The resume is the
document you stand behind in an interview, so what goes into it is your call.
Use the brief to see what it is missing, then check it against your rules:

```bash
python -m jobsift --check-draft resume.txt
```

**8. Keep it current.** Before drafting:

```bash
python -m jobsift --index-status   # "current", or STALE plus what changed; exits 1 when stale
```

A commit to any counted repo, or an edit to `career.yaml`, makes the brief stale.

## Commands

| Command | Does |
|---|---|
| `--index-repos` | Find and count every repo, merge `career.yaml`, write the brief |
| `--index-repos --offline` | The same without touching GitHub. Mirrors already on disk are still counted; merged PRs are carried over from the last build |
| `--index-status` | Say whether the brief is current. Exit 1 when it is not |
| `--check-draft FILE...` | Run `career.yaml`'s rules over finished documents (`.txt`, `.md`, `.pdf`). Exit 1 on any break. No model involved |

## What counts as yours

Having a repo on your disk is not evidence that you wrote it. Commit history is,
so with `authors` set, that is what decides:

| Case | What happens |
|---|---|
| On disk and on GitHub | Counted once, named by its GitHub remote |
| Two copies on disk | Counted once; the copy with more commits wins |
| Only on GitHub | Cloned into `data/mirrors`, then counted like the rest |
| On disk, no remote, your commits | Counted, under its folder name |
| A copy of one of your repos with its remote removed | Folded into the original, matched by their shared first commit, and reported |
| A clone of somebody else's repo | Skipped: its remote belongs to someone else |
| A repo you forked on GitHub | Skipped. Your part of it shows as the pull request you got merged |
| Somebody else's project you got a pull request merged into | Listed under *Pull requests merged upstream*, from GitHub's own record, with the project's star count. Never under "never cite", which holds only repos with none of your commits |
| Somebody's code with the remote removed, or re-pushed under your account | Only what your own commits touched is counted |
| No commits of yours at all, or no commits | Not counted, and listed in the brief under "never cite" |

"What your commits touched" means: languages and test files from the files your
commits added or changed, dependencies your commits added to a manifest, and
first and last dates from your own commits. A starter template ships its own
`package.json`; inheriting it is not using what is in it.

Two GitHub repos sharing a first commit are never merged. That is two projects
started from one template, and merging them would silently lose one.

Without `authors`, every tracked file counts, and the build says so.

## What is trusted, and what is not

Trusted: a count taken from files, and a curated line whose source was checked
at build time. When a hand-written figure disagrees with a count, the brief says
the count wins.

Not trusted, and deliberately not read: README prose ("highly scalable" is an
adjective, and an adjective in a trusted source is how a drafter learns to bluff),
and any "also familiar" list, which is tested against the counts before the brief
repeats it.

Rules are string tests run in code, per document: `forbid` a word, or require a
companion word `when` another appears. A rule a model is asked to remember gets
dropped the first time a sentence has to be shortened; a string test does not.

## Planned: one command

Not built yet. Steps 2 to 6 above are mechanical enough to be one guided command:

```bash
python -m jobsift --setup-evidence
```

1. **Check the tools.** `git` present; `gh auth status`. Not signed in: offer
   `gh auth login`, or continue in local-only mode.
2. **Find your identity.** GitHub login from `gh api user`; author identities
   from `git config user.email` and the shortlogs of the repos it finds. Show
   them; you confirm.
3. **Propose roots.** Look through your home folder and the usual places (`~/code`,
   `~/projects`, `~/src`, `~/Documents`) for folders holding git repos. Show each
   with its repo count; you pick.
4. **Write the `evidence:` block** into `config.yaml`, touching nothing else.
5. **Build**, exactly as `--index-repos`.
6. **Start `career.yaml`** with one entry per repo that looks like a project
   (enough of your own commits, or a homepage on GitHub): name, repos, live URL
   filled in; facts empty. It may *propose* facts from a repo's own docs, each
   with its source path, but proposals are marked `draft: true` and stay out of
   the brief until you remove the mark. Nothing reaches a letter unreviewed.
7. **Report what the resume is missing**: technologies the counts show that
   `resume.txt` never mentions, and rules it breaks. A report, not an edit.
8. **Print the next step**: the sections of the brief to read first.

It would produce the same four files as the manual route, plus that report.
