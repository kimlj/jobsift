"""Does --setup write what it was told, where it was told, and nothing else?

No network and no account. Every check the wizard makes is faked, and each
conversation is a script of (the prompt must say this, the person types that),
so a prompt that changes or goes missing fails here rather than in front of a
new user. What is attacked:

  * config.yaml is edited line by line: set six values in the real
    config.example.yaml, and every other value must parse exactly as before and
    every comment must still be there - including `enabled:`, which appears in a
    dozen blocks and must change only in the one named;
  * .env is edited in place, comments kept, and a value with a space or a quote
    must read back the same through python-dotenv;
  * a whole first run, from an empty folder to a config load_config accepts, where
    a rejected key is said out loud, retried, and never saved;
  * a second run where every answer is Enter changes not one byte;
  * stopping halfway keeps the steps already finished;
  * no secret is ever printed.

    python dryrun_setup.py
"""

import copy
import os
import re
import tempfile
from pathlib import Path
from types import SimpleNamespace

import yaml
from dotenv import dotenv_values

from jobsift.onboard import (Checks, SetupError, parse_sheet_id, run, set_env_values,
                             set_yaml_value)

ROOT = Path(__file__).resolve().parent
SECRET_VARS = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GMAIL_ADDRESS", "GMAIL_APP_PASSWORD",
               "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")
failures = 0


def check(label, ok, detail=""):
    global failures
    if not ok:
        failures += 1
    print(f"  {'ok  ' if ok else 'FAIL'}  {label:<52} {str(detail)[:60]}")


def dig(data, path):
    for part in path:
        data = data[part]
    return data


# ── config.yaml ──────────────────────────────────────────────────────────────
print("editing config.yaml\n" + "-" * 78)
original_text = (ROOT / "config.example.yaml").read_text(encoding="utf-8")
original = yaml.safe_load(original_text)
SHEET_ID = "1AbCdEfGhIjKlMnOpQrStUvWxYz_-0123456789"
edits = [
    (("llm_provider",), "openai"),
    (("telegram_enabled",), False),
    (("google_sheet", "enabled"), True),
    (("google_sheet", "sheet_id"), SHEET_ID),
    (("google_sheet", "service_account_file"), "./keys/robot key.json"),
    (("worker", "ssh"), "kim@203.0.113.7"),
    (("filters", "skip_senior"), True),
]
text = original_text
for path, value in edits:
    text = set_yaml_value(text, path, value)
edited = yaml.safe_load(text)
expected = copy.deepcopy(original)
for path, value in edits:
    dig(expected, path[:-1])[path[-1]] = value

check("every value reads back as set", all(dig(edited, p) == v for p, v in edits))
check("nothing else changed, every other enabled: too", edited == expected)
check("same number of lines", len(text.splitlines()) == len(original_text.splitlines()))
comments = [line for line in original_text.splitlines() if line.lstrip().startswith("#")]
check("every comment line survives",
      comments == [line for line in text.splitlines() if line.lstrip().startswith("#")])
ssh_line = next(line for line in text.splitlines() if line.strip().startswith("ssh: "))
check("the comment after a value survives", "# user@host of the core" in ssh_line, ssh_line)

for path in [("google_sheet", "worksheet_closed"), ("nosuchblock", "enabled")]:
    try:
        set_yaml_value(original_text, path, "x")
        check(f"{'.'.join(path)} is refused, not appended", False)
    except SetupError:
        check(f"{'.'.join(path)} is refused, not appended", True)

# A config written before the presets has no switches. Setup adds them rather
# than refusing, and only when asked to.
older = re.sub(r"(?m)^\s+skip_(junior|senior|assistant_roles|call_centres):.*\n", "", original_text)
added = yaml.safe_load(set_yaml_value(older, ("filters", "skip_senior"), True, insert=True))
before_filters = yaml.safe_load(older)["filters"]
check("insert adds a switch an older config lacks",
      added["filters"].get("skip_senior") is True
      and {k: v for k, v in added["filters"].items() if k != "skip_senior"} == before_filters)
check("and a missing top-level key goes at the end",
      yaml.safe_load(set_yaml_value("a: 1\n", ("b",), 2, insert=True)) == {"a": 1, "b": 2})

crlf = set_yaml_value(original_text.replace("\n", "\r\n"), ("llm_provider",), "openai")
check("a CRLF file stays CRLF", crlf.count("\n") == crlf.count("\r\n"))

nested = "outer:\n  inner:\n    enabled: true\n  enabled: true\nafter: 1\n"
nested = yaml.safe_load(set_yaml_value(nested, ("outer", "enabled"), False))
check("a deeper key of the same name is left alone",
      nested == {"outer": {"inner": {"enabled": True}, "enabled": False}, "after": 1}, nested)

# ── .env ─────────────────────────────────────────────────────────────────────
print("\nediting .env\n" + "-" * 78)
env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
updates = {"GMAIL_ADDRESS": "kim@example.com", "GMAIL_APP_PASSWORD": "abcdefghijklmnop",
           "TELEGRAM_BOT_TOKEN": "123456:AAH-x_y", "SPACED": "p@ss w0rd # not a comment",
           "QUOTED": "it's \"both\" \\ kinds"}
env_text = set_env_values(env_example, updates)
with tempfile.TemporaryDirectory() as tmp:
    Path(tmp, ".env").write_text(env_text, encoding="utf-8")
    read_back = dotenv_values(Path(tmp, ".env"))
check("every value reads back through python-dotenv",
      all(read_back.get(k) == v for k, v in updates.items()),
      {k: read_back.get(k) for k in updates if read_back.get(k) != updates[k]})
check("an existing key is replaced where it was, once",
      env_text.count("GMAIL_ADDRESS=") == 1 and env_text.index("GMAIL_ADDRESS=")
      < env_text.index("TELEGRAM_BOT_TOKEN="))
check("a new key is appended", env_text.rstrip().splitlines()[-1].startswith("QUOTED="))
check("the comments are all still there",
      all(line in env_text for line in env_example.splitlines() if line.startswith("#")))
check("an untouched key is untouched", read_back.get("OPENAI_API_KEY") == "")

# ── sheet links ──────────────────────────────────────────────────────────────
print("\nsheet links\n" + "-" * 78)
for raw, want in [
    (f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit#gid=0", SHEET_ID),
    (f"https://docs.google.com/spreadsheets/u/1/d/{SHEET_ID}/edit?usp=sharing", SHEET_ID),
    (SHEET_ID, SHEET_ID), ("my job sheet", None), ("", None),
]:
    check(f"{raw[:48] or '(empty)'}", parse_sheet_id(raw) == want, parse_sheet_id(raw))


# ── presets ──────────────────────────────────────────────────────────────────
print("\npresets\n" + "-" * 78)
from jobsift.filters import check as passes  # noqa: E402


def posting(title, company="Acme"):
    return {"title": title, "company": company}


check("skip_junior drops a junior title",
      not passes(posting("Junior Python Developer"), {"skip_junior": True})[0])
check("and keeps it when off", passes(posting("Junior Python Developer"), {"skip_junior": False})[0])
check("a senior title stays unless skip_senior is on",
      passes(posting("Senior Backend Engineer"), {"skip_junior": True})[0]
      and not passes(posting("Senior Backend Engineer"), {"skip_senior": True})[0])
check("skip_call_centres drops a named BPO",
      not passes(posting("Developer", "Concentrix Philippines"), {"skip_call_centres": True})[0])
check("an own list still works beside the presets",
      not passes(posting("QA Engineer"), {"skip_junior": True, "exclude_titles": ["qa"]})[0])
check("whole words only: International is not an intern",
      passes(posting("International Support Engineer"), {"skip_junior": True})[0])

# ── providers ────────────────────────────────────────────────────────────────
print("\nproviders\n" + "-" * 78)
from jobsift.config import DEFAULT_MODELS  # noqa: E402
from jobsift.llm import DeepSeekLLM, OpenAILLM, build_llm  # noqa: E402
from jobsift.onboard import _model_edits  # noqa: E402


class FakeCompletions:
    """chat.completions, answering from a list and keeping every request."""

    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(
            finish_reason="stop", message=SimpleNamespace(content=self.replies.pop(0)))])


def faked(llm, replies):
    llm.client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions(replies)))
    return llm.client.chat.completions


deepseek = DeepSeekLLM("sk-test")
check("DeepSeek goes to its own address",
      str(deepseek.client.base_url).startswith("https://api.deepseek.com"), deepseek.client.base_url)
sent = faked(deepseek, ["", '{"jobs": []}'])
got = deepseek.complete_json("deepseek-flash", "Extract the listings.", "an email")
check("an empty reply is asked for once more", got == {"jobs": []} and len(sent.calls) == 2)
check("the prompt says json, as DeepSeek's JSON mode needs",
      "json" in sent.calls[0]["messages"][0]["content"].lower())
check("DeepSeek gets max_tokens and temperature 0",
      "max_tokens" in sent.calls[0] and sent.calls[0].get("temperature") == 0)
openai_llm = OpenAILLM("sk-test")
sent = faked(openai_llm, ['{"a": 1}', '{"a": 1}'])
openai_llm.complete_json("gpt-5.6-luna", "Return json.", "x")
openai_llm.complete_json("gpt-4o-mini", "Return json.", "x")
reasoning, legacy = sent.calls
check("a reasoning model gets room to reason and no temperature",
      reasoning.get("max_completion_tokens", 0) >= 8000 and "temperature" not in reasoning
      and reasoning.get("reasoning_effort") == "low", reasoning)
check("a gpt-4 model keeps temperature 0",
      legacy.get("temperature") == 0 and "reasoning_effort" not in legacy, legacy)
os.environ["DEEPSEEK_API_KEY"] = "sk-from-env"
check("build_llm finds the DeepSeek key in the environment",
      build_llm("deepseek").client.api_key == "sk-from-env")
os.environ.pop("DEEPSEEK_API_KEY")
check("no default is an Opus model any more",
      not any("opus" in model for models in DEFAULT_MODELS.values() for model in models.values()))
named = {"models": {"extract": "claude-haiku-4-5-20251001", "score": "claude-sonnet-5",
                    "draft": "claude-sonnet-5"}}
check("switching a Claude config to DeepSeek replaces every model",
      dict(_model_edits(named, "deepseek")) == {("models", s): "deepseek-flash"
                                                for s in ("extract", "score", "draft")})
check("and staying on Claude changes none", _model_edits(named, "anthropic") == [])


# ── whole runs ───────────────────────────────────────────────────────────────
class Script:
    """The person at the keyboard: answers in order, each checked against its prompt."""

    def __init__(self, lines):
        self.lines = list(lines)
        self.problems = []

    def _next(self, kind, prompt):
        if not self.lines:
            self.problems.append(f"ran out of answers at: {prompt.strip()}")
            raise EOFError
        want_kind, fragment, answer = self.lines.pop(0)
        if answer is KeyboardInterrupt:
            raise KeyboardInterrupt
        if want_kind != kind or fragment not in prompt:
            self.problems.append(f"expected {want_kind} '{fragment}', got {kind} '{prompt.strip()}'")
        return answer

    def ask(self, prompt):
        return self._next("text", prompt)

    def ask_secret(self, prompt):
        return self._next("secret", prompt)


calls = []


def fake_llm(provider, key):
    calls.append(("llm", provider, key))
    if key == "sk-bad":
        raise SetupError("the key was refused")
    return "the key works"


FAKES = Checks(
    llm=fake_llm,
    gmail=lambda address, password: calls.append(("gmail", address)) or "signed in",
    telegram_bot=lambda token: "@jobsift_test_bot",
    telegram_chats=lambda token: [("777", "Test Person")],
    telegram_send=lambda token, chat: calls.append(("send", chat)) or "sent",
    sheet=lambda key_file, sheet_id: calls.append(("sheet", sheet_id)) or 'can open "Job hunt"',
)


def wizard(folder, lines):
    for name in SECRET_VARS:  # load_config reads the process environment first
        os.environ.pop(name, None)
    script, printed = Script(lines), []
    here = os.getcwd()
    os.chdir(folder)  # relative paths resolve here, as they would for a real run
    try:
        code = run("config.yaml", ".env", "profile.yaml", ask=script.ask,
                   ask_secret=script.ask_secret, checks=FAKES, out=printed.append,
                   examples=ROOT)
    finally:
        os.chdir(here)
    return code, "\n".join(printed), script


FIRST_RUN = [
    ("text", "AI provider: deepseek, openai or claude [claude]", "openai"),
    ("secret", "OPENAI_API_KEY", "sk-bad"),
    ("text", "Try again, keep it anyway, or skip", ""),
    ("secret", "OPENAI_API_KEY", "sk-good-1234567890"),
    ("text", "Gmail address", "kim@example.com"),
    ("secret", "App password", "abcd efgh ijkl mnop"),
    ("text", "Skip junior roles, internships and trainee posts? [Y/n]", ""),
    ("text", "Skip senior roles? [y/N]", "y"),
    ("text", "Skip virtual-assistant, admin and data-entry work? [Y/n]", "n"),
    ("text", "Skip call-centre, BPO and big outsourcing firms", ""),
    ("text", "Lowest monthly pay you would take, in pesos (0 for no floor) [40000]", "55k"),
    ("text", "Alert when a job scores at least, out of 100 [60]", "70"),
    ("text", "Set up Telegram alerts? [Y/n]", ""),
    ("secret", "Bot token", "123456789:AAH-secret-token"),
    ("text", "Press Enter once it is sent", ""),
    ("text", "Set up the Google Sheet now? [y/N]", "y"),
    ("text", "Key file [./service-account.json]", ""),
    ("text", "Sheet link or id", f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit"),
]
SECOND_RUN = [
    ("text", "AI provider: deepseek, openai or claude [openai]", ""),
    ("secret", "OPENAI_API_KEY (typing is hidden) [set, ends ...7890", ""),
    ("text", "Gmail address [kim@example.com]", ""),
    ("secret", "App password (typing is hidden) [set, ends ...mnop", ""),
    ("text", "Skip junior roles, internships and trainee posts? [Y/n]", ""),
    ("text", "Skip senior roles? [Y/n]", ""),
    ("text", "Skip virtual-assistant, admin and data-entry work? [y/N]", ""),
    ("text", "Skip call-centre, BPO and big outsourcing firms", ""),
    ("text", "Lowest monthly pay you would take, in pesos (0 for no floor) [55000]", ""),
    ("text", "Alert when a job scores at least, out of 100 [70]", ""),
    ("text", "Telegram is already set up (chat 777). Set it up again? [y/N]", ""),
    ("text", "The Google Sheet is already set up. Set it up again? [y/N]", ""),
]
SECRETS = ["sk-good-1234567890", "sk-bad", "abcdefghijklmnop", "abcd efgh",
           "123456789:AAH-secret-token"]

with tempfile.TemporaryDirectory() as tmp:
    folder = Path(tmp)
    (folder / "service-account.json").write_text(
        '{"type": "service_account", "client_email": "robot@proj.iam.gserviceaccount.com",'
        ' "private_key": "not a real key; read_service_account only checks one is there"}',
        encoding="utf-8")

    print("\na first run, from an empty folder\n" + "-" * 78)
    code, printed, script = wizard(folder, FIRST_RUN)
    check("the conversation went as scripted", not script.problems and not script.lines,
          script.problems or script.lines)
    check("it finishes cleanly", code == 0, code)
    check("all four files were made",
          all((folder / name).is_file() for name in ("config.yaml", ".env", "resume.txt", "profile.yaml")))
    env = dotenv_values(folder / ".env")
    cfg = yaml.safe_load((folder / "config.yaml").read_text(encoding="utf-8"))
    check("the good key is saved, the refused one never was",
          env.get("OPENAI_API_KEY") == "sk-good-1234567890"
          and "sk-bad" not in (folder / ".env").read_text(encoding="utf-8"))
    check("the refusal was said, and retried",
          "not working: the key was refused" in printed
          and [c[2] for c in calls if c[0] == "llm"] == ["sk-bad", "sk-good-1234567890"])
    check("the app password is saved without its spaces",
          env.get("GMAIL_APP_PASSWORD") == "abcdefghijklmnop")
    check("Telegram's chat was found, not typed",
          env.get("TELEGRAM_CHAT_ID") == "777" and ("send", "777") in calls)
    check("the provider is saved, and its default models left to apply",
          cfg["llm_provider"] == "openai" and not cfg.get("models"), cfg.get("models"))
    answers = {k: cfg["filters"].get(k) for k in (
        "skip_junior", "skip_senior", "skip_assistant_roles", "skip_call_centres", "min_salary_php")}
    check("the search answers are saved as switches",
          answers == {"skip_junior": True, "skip_senior": True, "skip_assistant_roles": False,
                      "skip_call_centres": True, "min_salary_php": 55000}
          and cfg["score_threshold"] == 70, answers)
    check("the sheet is on, by id",
          cfg["google_sheet"] == {**cfg["google_sheet"], "enabled": True, "sheet_id": SHEET_ID,
                                  "service_account_file": "./service-account.json"})
    check("telegram_enabled is on", cfg["telegram_enabled"] is True)
    check("no secret was printed", not [s for s in SECRETS if s in printed],
          [s for s in SECRETS if s in printed])
    check("it says the resume is still the example", "resume.txt is still the example" in printed)
    check("and what to run next", "python -m jobsift --suggest-senders" in printed)

    print("\nthe same again, pressing Enter every time\n" + "-" * 78)
    before = {name: (folder / name).read_bytes() for name in ("config.yaml", ".env")}
    code, printed, script = wizard(folder, SECOND_RUN)
    check("the conversation went as scripted", not script.problems and not script.lines,
          script.problems or script.lines)
    check("it finishes cleanly", code == 0, code)
    check("config.yaml and .env are byte for byte the same",
          all((folder / name).read_bytes() == data for name, data in before.items()))
    check("existing files are left alone", printed.count("is already there; left as it is") == 4)

with tempfile.TemporaryDirectory() as tmp:
    print("\nstopped halfway\n" + "-" * 78)
    code, printed, script = wizard(Path(tmp), [
        ("text", "AI provider", ""),
        ("secret", "ANTHROPIC_API_KEY", "sk-ant-good-0000"),
        ("text", "Gmail address", KeyboardInterrupt),
    ])
    check("it stops, and says so", code == 1 and "Stopped." in printed)
    check("the finished step was kept",
          dotenv_values(Path(tmp, ".env")).get("ANTHROPIC_API_KEY") == "sk-ant-good-0000")

print("\n" + ("-" * 78) +
      f"\n{'all cases behaved' if not failures else f'{failures} FAILED'}")
raise SystemExit(1 if failures else 0)
