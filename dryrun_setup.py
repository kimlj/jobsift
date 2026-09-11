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
import tempfile
from pathlib import Path

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
    (("models", "extract"), "gpt-4o-mini"),
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
extract_line = next(line for line in text.splitlines() if line.strip().startswith("extract:"))
check("the comment after a value survives", "# pull job listings" in extract_line, extract_line)

for path in [("google_sheet", "worksheet_closed"), ("nosuchblock", "enabled")]:
    try:
        set_yaml_value(original_text, path, "x")
        check(f"{'.'.join(path)} is refused, not appended", False)
    except SetupError:
        check(f"{'.'.join(path)} is refused, not appended", True)

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
    ("text", "Provider, anthropic or openai [anthropic]", "openai"),
    ("secret", "OPENAI_API_KEY", "sk-bad"),
    ("text", "Try again, keep it anyway, or skip", ""),
    ("secret", "OPENAI_API_KEY", "sk-good-1234567890"),
    ("text", "Gmail address", "kim@example.com"),
    ("secret", "App password", "abcd efgh ijkl mnop"),
    ("text", "Set up Telegram alerts? [Y/n]", ""),
    ("secret", "Bot token", "123456789:AAH-secret-token"),
    ("text", "Press Enter once it is sent", ""),
    ("text", "Set up the Google Sheet now? [y/N]", "y"),
    ("text", "Key file [./service-account.json]", ""),
    ("text", "Sheet link or id", f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit"),
]
SECOND_RUN = [
    ("text", "Provider, anthropic or openai [openai]", ""),
    ("secret", "OPENAI_API_KEY (typing is hidden) [set, ends ...7890", ""),
    ("text", "Gmail address [kim@example.com]", ""),
    ("secret", "App password (typing is hidden) [set, ends ...mnop", ""),
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
    check("the provider switch moved the models too",
          cfg["llm_provider"] == "openai" and cfg["models"] == {
              "extract": "gpt-4o-mini", "enrich": "gpt-4o-mini", "score": "gpt-4o"}, cfg["models"])
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
        ("text", "Provider", ""),
        ("secret", "ANTHROPIC_API_KEY", "sk-ant-good-0000"),
        ("text", "Gmail address", KeyboardInterrupt),
    ])
    check("it stops, and says so", code == 1 and "Stopped." in printed)
    check("the finished step was kept",
          dotenv_values(Path(tmp, ".env")).get("ANTHROPIC_API_KEY") == "sk-ant-good-0000")

print("\n" + ("-" * 78) +
      f"\n{'all cases behaved' if not failures else f'{failures} FAILED'}")
raise SystemExit(1 if failures else 0)
