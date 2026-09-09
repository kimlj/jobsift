"""Keep profile.yaml's Claude Code figures true, by reading them rather than typing them.

The numbers were hand-maintained, and they were wrong twice in one afternoon: an
application quoted 743 hours on a day the portfolio said 772. That is the failure
mode of any figure that exists in two places, one computed and one typed.

So this reads the computed one. `scripts/sync-claude-usage.mjs` in the `port` repo
derives the figures from the session transcripts under ~/.claude/projects and writes
`assets/claude-usage.json` into its own clone under LOCALAPPDATA, on a schedule
(the `kimlj.dev build activity` task). That file is the source of truth for
kimlj.dev, so making it the source of truth here means the resume and the site can
no longer disagree.

Two things are counted here instead of read, because the usage JSON has no opinion
on them: authored Skills and subagents, which are files on disk under ~/.claude.

One thing is deliberately NOT computed: `mcp_servers_authored`. It is a hand-set
zero, and it stays hand-set. Its whole job is to stop a draft claiming something
Kim has not done, and a count derived from an empty directory would flip to a wrong
answer the moment a directory moved.

Nothing here fails a run. A missing usage file means the figures in profile.yaml
stand as they are, which is the same position the program was in before.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# Where sync-claude-usage.mjs leaves its output. It clones to LOCALAPPDATA
# precisely so no checkout anyone is using gets committed into, so this is a
# stable location rather than an incidental one.
USAGE_JSON = "kimlj-port-usage-sync/repo/assets/claude-usage.json"

# Ships with Claude Code rather than being authored. Counting it would inflate the
# one number here that is a claim about work Kim did.
BUNDLED_SKILLS = {"skill-creator"}


def usage_path() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    return Path(base) / USAGE_JSON


def claude_dir() -> Path:
    return Path.home() / ".claude"


def count_authored() -> dict:
    """Skills and subagents that are files on this machine, not entries in the JSON."""
    skills_dir, agents_dir = claude_dir() / "skills", claude_dir() / "agents"
    out: dict[str, int] = {}

    if skills_dir.is_dir():
        skills = [d for d in skills_dir.iterdir()
                  if d.is_dir() and d.name not in BUNDLED_SKILLS]
        out["authored_skills"] = len(skills)
        # Line count is the honest measure of "20 Skills" being substantial rather
        # than twenty stubs, and it is the figure an interviewer can ask about.
        out["authored_skill_lines"] = sum(
            len(f.read_text(encoding="utf-8", errors="replace").splitlines())
            for d in skills for f in d.rglob("*.md") if f.is_file()
        )
    if agents_dir.is_dir():
        out["authored_subagents"] = len(list(agents_dir.glob("*.md")))
    return out


def read_usage(path: Path | None = None) -> dict:
    """The fields worth quoting, flattened. {} when the file is not there yet."""
    path = path or usage_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.info("usage: no %s yet; leaving profile figures alone", path)
        return {}
    except Exception as exc:
        logger.warning("usage: could not read %s (%s)", path, exc)
        return {}

    hours = raw.get("hours") or {}
    prompts = raw.get("prompts") or {}
    if not hours.get("total") or not prompts.get("total"):
        # A half-written file during a sync. Better to keep yesterday's true
        # numbers than to write today's empty ones.
        logger.warning("usage: %s has no totals; leaving profile figures alone", path)
        return {}

    active_days = prompts.get("activeDays") or 0
    data = {
        "as_of": str(raw.get("generatedAt") or "")[:10],
        "hours": hours.get("total"),
        "sessions": hours.get("sessions"),
        "prompts": prompts.get("total"),
        "projects": prompts.get("projects"),
        "active_days": active_days,
        "first_day": prompts.get("firstDay"),
        "last_day": prompts.get("lastDay"),
        "longest_session_hours": hours.get("longestSessionHours"),
    }
    if hours.get("total") and active_days:
        data["hours_per_active_day"] = round(hours["total"] / active_days, 1)

    # Token figures cover a SHORTER window than the prompt figures, because Claude
    # Code prunes older transcripts. The window travels with the number here so a
    # draft cannot quote "45.8M tokens" as though it covered the whole period.
    totals = raw.get("totals") or {}
    out_tokens, token_days = totals.get("out"), raw.get("activeDays")
    if out_tokens:
        window = f"{raw.get('firstDay', '?')} to {raw.get('lastDay', '?')}"
        data["output_tokens"] = f"{out_tokens / 1e6:.1f}M over {window}"
        if token_days:
            data["output_tokens_per_active_day"] = f"{out_tokens / token_days / 1e6:.1f}M"

    models = raw.get("models") or {}
    total_model_tokens = sum(v for v in models.values() if isinstance(v, (int, float)))
    top = max((m for m in models.items() if isinstance(m[1], (int, float))),
              key=lambda kv: kv[1], default=None)
    if top and total_model_tokens:
        data["top_model_share"] = f"{top[0]} {top[1] / total_model_tokens * 100:.0f}%"

    data.update(count_authored())
    return data


# Written above the block so the next person to open profile.yaml knows not to
# hand-edit it, and knows what to run instead.
HEADER = """claude_code_usage:
  # GENERATED - do not hand-edit. `python -m jobsift --sync-usage` rewrites this
  # block from assets/claude-usage.json in the kimlj-port-usage-sync clone, which
  # the `kimlj.dev build activity` scheduled task refreshes from the session
  # transcripts. Hand-maintaining these put 743 hours on an application the day
  # the site said 772. Everything below `# hand-set` is kept as written.
"""

# Lines the sync must not touch, because nothing computes them. `mcp_servers_authored`
# is the important one: it exists to stop an overclaim, so it must not be derivable.
HAND_SET = """  # hand-set
  mcp_servers_authored: 0   # Kim USES MCP servers; he has not written one. Never claim otherwise.
  source: "local Claude Code session transcripts, published at https://kimlj.dev"
  # What this is NOT: time spent in one tool on this machine, not a claim about
  # years of professional AI work. Say it as what it is. Hours come from prompt
  # timestamps - consecutive prompts form a session that closes after 15 minutes
  # of silence - so an editor left open counts as nothing.
"""


def render_block(data: dict) -> str:
    lines = [HEADER.rstrip("\n")]
    for key, value in data.items():
        if isinstance(value, str):
            lines.append(f'  {key}: "{value}"')
        else:
            lines.append(f"  {key}: {value}")
    lines.append(HAND_SET.rstrip("\n"))
    return "\n".join(lines) + "\n"


def sync_profile(profile_path: str, path: Path | None = None) -> bool:
    """Rewrite the claude_code_usage block in place. True if the file changed.

    A targeted text replacement rather than a YAML round-trip: profile.yaml is
    mostly comments, and every one of them is doing work - why a role year differs
    from a skill year, which figures an interviewer will push on. PyYAML would
    load and dump this file without a single one of them.
    """
    data = read_usage(path)
    if not data:
        return False

    try:
        original = Path(profile_path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return False

    lines = original.split("\n")
    try:
        start = next(i for i, ln in enumerate(lines)
                     if ln.startswith("claude_code_usage:"))
    except StopIteration:
        logger.warning("usage: no claude_code_usage block in %s", profile_path)
        return False

    # The block runs to the next line that starts a new top-level key. Indented
    # lines, comment lines and blanks all belong to it.
    end = start + 1
    while end < len(lines):
        line = lines[end]
        if line and not line[0].isspace() and not line.lstrip().startswith("#"):
            break
        end += 1
    # Blank lines before the next key are separators, not part of the block.
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1

    updated = "\n".join(lines[:start] + render_block(data).split("\n")[:-1] + lines[end:])
    if updated == original:
        return False

    Path(profile_path).write_text(updated, encoding="utf-8")
    logger.info("usage: profile refreshed to %sh / %s prompts (as of %s)",
                data.get("hours"), data.get("prompts"), data.get("as_of"))
    return True
