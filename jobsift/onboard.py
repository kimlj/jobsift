"""`python -m jobsift --setup`: connect the accounts jobsift needs, and prove each one.

Setup used to be four files copied by hand and five kinds of credential pasted
into them, with nothing to say whether any of it was right until the first run
failed half an hour later. This walks through them in order - an AI key, Gmail,
then optionally Telegram and a Google Sheet - and checks each one works before
moving on, which is the part a hand-edited file can never do.

What it writes, and where:
  * secrets go to .env only - never config.yaml, never the screen;
  * config.yaml is edited line by line, so every comment in it survives;
  * files that already exist are never replaced, every question offers the value
    already set, and each step is saved as it finishes. Running it again changes
    only what you change, and stopping halfway keeps the steps already done.

The checks cost nothing: listing a provider's models, one IMAP sign-in,
Telegram's getMe, opening a sheet. No model is called and no mail is read.
"""

from __future__ import annotations

import getpass
import json
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import yaml
from dotenv import dotenv_values

from .config import DEFAULT_MODELS, load_config

# The example files ship beside the package, in a checkout and in the Docker
# image alike, so they are found there rather than in the working directory.
EXAMPLES = Path(__file__).resolve().parent.parent

# Values the example files ship with. Read as "not set", or a re-run would offer
# you@gmail.com back as though somebody had typed it.
PLACEHOLDERS = {"", "you@gmail.com", "xxxx xxxx xxxx xxxx", "<YOUR_GOOGLE_SHEET_ID>"}

# In the order setup offers them.
KEY_NAMES = {"deepseek": "DEEPSEEK_API_KEY", "openai": "OPENAI_API_KEY",
             "anthropic": "ANTHROPIC_API_KEY"}
KEY_PAGES = {"deepseek": "https://platform.deepseek.com/api_keys",
             "openai": "https://platform.openai.com/api-keys",
             "anthropic": "https://console.anthropic.com/settings/keys"}
# What a person sees and types. config.yaml keeps saying "anthropic", as it always has.
SHOWN = {"deepseek": "deepseek", "openai": "openai", "anthropic": "claude"}
TYPED = {"deepseek": "deepseek", "openai": "openai", "gpt": "openai",
         "claude": "anthropic", "anthropic": "anthropic"}

# The presets --setup asks about: (switch in config.yaml's filters block, question,
# answer for a config that has neither the switch nor any of its words). The words
# behind each switch are filters.PRESET_TERMS.
PRESETS = [
    ("skip_junior", "Skip junior roles, internships and trainee posts?", True),
    ("skip_senior", "Skip senior roles?", False),
    ("skip_assistant_roles", "Skip virtual-assistant, admin and data-entry work?", True),
    ("skip_call_centres", "Skip call-centre, BPO and big outsourcing firms "
                          "(Concentrix, Accenture...)?", True),
]

SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets"


class SetupError(Exception):
    """A step that did not work, worded for the person at the keyboard."""


# ── Editing the files ────────────────────────────────────────────────────────


def _read(path: Path) -> str:
    # newline="" both ways, so a CRLF file stays CRLF. The default would turn
    # every line ending on Windows into something the file never had.
    with open(path, encoding="utf-8", newline="") as fh:
        return fh.read()


def _write(path: Path, text: str, private: bool = False) -> None:
    """Replace a file whole or not at all: a crash mid-write must not leave a
    truncated config.yaml behind."""
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    if private and os.name != "nt":
        os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _eol(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def _scalar(value) -> str:
    """A value as YAML, quoted only when it has to be."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if re.fullmatch(r"[A-Za-z0-9_./@-]+", text) and yaml.safe_load(text) == text:
        return text
    return json.dumps(text)  # a JSON string is a valid double-quoted YAML scalar


_LINE = r"^(?P<indent>{indent})(?P<key>{key}):(?P<gap>[ \t]*)(?P<value>.*?)(?P<tail>[ \t]+#.*)?$"


def _find(lines: list[str], start: int, end: int, indent: str, key: str):
    pattern = re.compile(_LINE.format(indent=re.escape(indent), key=re.escape(key)))
    for index in range(start, end):
        match = pattern.match(lines[index])
        if match:
            return index, match
    return None


def set_yaml_value(text: str, path: tuple, value, insert: bool = False) -> str:
    """Replace one scalar in config.yaml's text, keeping every comment.

    Only the two shapes this file uses: a top-level key, and a key one level
    inside a top-level block. A path it cannot find is an error rather than an
    append, because a config.yaml without google_sheet.sheet_id where the example
    puts it has been rearranged by someone who should decide where it goes.

    `insert` is the exception, for switches newer than the config being edited:
    a config written before the presets has no filters.skip_senior, and the key
    is added at the top of its block rather than refused.
    """
    eol = _eol(text)
    lines = text.split(eol)
    if len(path) == 1:
        start, end, indent = 0, len(lines), ""
    elif len(path) == 2:
        block = _find(lines, 0, len(lines), "", path[0])
        if block is None:
            raise SetupError(f"config.yaml has no `{path[0]}:` section")
        start = end = block[0] + 1
        while end < len(lines) and (not lines[end].strip() or lines[end][0] in " \t#"):
            end += 1
        # The block's own indentation, from its first key, so a key nested deeper
        # inside it is never mistaken for one of its own.
        indent = next((re.match(r"[ \t]+", line).group(0) for line in lines[start:end]
                       if line.strip() and line[0] in " \t" and not line.lstrip().startswith("#")),
                      "  ")
    else:
        raise ValueError(f"unsupported path {path}")

    found = _find(lines, start, end, indent, path[-1])
    if found is not None:
        index, match = found
        lines[index] = (f"{match['indent']}{match['key']}:{match['gap'] or ' '}"
                        f"{_scalar(value)}{match['tail'] or ''}")
    elif not insert:
        raise SetupError(f"config.yaml has no `{'.'.join(path)}` to set")
    elif len(path) == 1:
        # After the last line with anything on it, so the final newline stays last.
        at = len(lines)
        while at and not lines[at - 1].strip():
            at -= 1
        lines.insert(at, f"{path[0]}: {_scalar(value)}")
    else:
        if block[1]["value"].strip():
            raise SetupError(f"config.yaml writes `{path[0]}` on one line; "
                             f"add `{path[1]}` to it by hand")
        lines.insert(block[0] + 1, f"{indent}{path[1]}: {_scalar(value)}")
    new = eol.join(lines)

    # Read the edit back the way load_config will, and refuse anything that does
    # not come back as the value meant.
    got = yaml.safe_load(new)
    for part in path:
        got = got.get(part) if isinstance(got, dict) else None
    if got != value:
        raise SetupError(f"could not set `{'.'.join(path)}` cleanly; edit config.yaml by hand")
    return new


def _env_value(value: str) -> str:
    """A value as .env text that python-dotenv and docker compose read the same way."""
    if re.fullmatch(r"[A-Za-z0-9_@.:/+=,-]*", value):
        return value
    if "'" not in value:
        return f"'{value}'"  # single quotes are literal to both readers
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def set_env_values(text: str, updates: dict) -> str:
    """Set keys in .env text, in place where they already are, appended where not.
    Comments and every other line are left exactly as they were."""
    eol = _eol(text) if text else os.linesep
    body = text[: -len(eol)] if text.endswith(eol) else text
    lines = body.split(eol) if body else []
    written = set()
    for index, line in enumerate(lines):
        match = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        if match and match.group(1) in updates:
            key = match.group(1)
            lines[index] = f"{key}={_env_value(updates[key])}"
            written.add(key)
    lines += [f"{key}={_env_value(value)}" for key, value in updates.items()
              if key not in written]
    return eol.join(lines) + eol


def parse_sheet_id(text: str):
    """The id out of a sheet's address, or the id itself; None for anything else."""
    text = (text or "").strip()
    # /u/1/ appears when the browser is signed in to more than one Google account.
    match = re.search(r"/spreadsheets/(?:u/\d+/)?d/([A-Za-z0-9_-]{20,})", text)
    if match:
        return match.group(1)
    return text if re.fullmatch(r"[A-Za-z0-9_-]{20,}", text) else None


def read_service_account(path: str) -> str:
    """The key file's client_email, or a SetupError naming the usual mistake."""
    file = Path(path)
    if not file.is_file():
        raise SetupError(f"there is no file at {path}")
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise SetupError("that file is not JSON. The key Google downloads is a .json file") from None
    if not isinstance(data, dict) or data.get("type") != "service_account":
        if isinstance(data, dict) and ("installed" in data or "web" in data):
            raise SetupError("that is an OAuth client file, not a service-account key. Open "
                             "the service account itself, then Keys > Add key > JSON")
        raise SetupError("that file is not a service-account key")
    if not data.get("client_email") or not data.get("private_key"):
        raise SetupError("the key file is missing client_email or private_key; download a new one")
    return data["client_email"]


def _mask(secret: str) -> str:
    return f"set, ends ...{secret[-4:]}" if len(secret) > 8 else "set"


def _provider_of(model: str) -> str:
    if model.startswith("claude"):
        return "anthropic"
    if model.startswith("deepseek"):
        return "deepseek"
    return "openai"


def _model_edits(cfg: dict, provider: str) -> list:
    """Models in config.yaml that belong to another provider.

    load_config only fills the stages a config leaves out, so a config naming
    Claude models that switches to DeepSeek would otherwise send Claude model
    ids to DeepSeek on the first call. Each is replaced with the new provider's
    default for that stage.
    """
    wanted = DEFAULT_MODELS[provider]
    return [(("models", stage), wanted.get(stage, wanted["score"]))
            for stage, model in (cfg.get("models") or {}).items()
            if isinstance(model, str) and _provider_of(model) != provider]


# ── The checks ───────────────────────────────────────────────────────────────


def _http(verb: str, url: str, **kwargs):
    import httpx

    try:
        return httpx.request(verb, url, timeout=20, **kwargs)
    except httpx.HTTPError as err:
        # Never str(err): for Telegram the token is part of the URL, and httpx
        # puts the URL in its messages.
        raise SetupError(f"could not reach {httpx.URL(url).host} ({type(err).__name__}); "
                         "check the internet connection") from None


def check_llm(provider: str, key: str) -> str:
    if provider == "anthropic":
        resp = _http("GET", "https://api.anthropic.com/v1/models",
                     headers={"x-api-key": key, "anthropic-version": "2023-06-01"})
    elif provider == "deepseek":
        resp = _http("GET", "https://api.deepseek.com/models",
                     headers={"Authorization": f"Bearer {key}"})
    else:
        resp = _http("GET", "https://api.openai.com/v1/models",
                     headers={"Authorization": f"Bearer {key}"})
    if resp.status_code in (401, 403):
        raise SetupError("the key was refused. Copy it again; a key is shown only once, "
                         "when it is made")
    if resp.status_code != 200:
        raise SetupError(f"the provider answered {resp.status_code}; try again in a minute")
    return "the key works"


def check_gmail(address: str, password: str) -> str:
    import imaplib

    from .gmail import IMAP_HOST

    try:
        conn = imaplib.IMAP4_SSL(IMAP_HOST, timeout=30)
    except OSError:
        raise SetupError(f"could not reach {IMAP_HOST}; check the internet connection") from None
    try:
        try:
            conn.login(address, password)
        except imaplib.IMAP4.error as err:
            text = str(err)
            if "Application-specific password required" in text:
                raise SetupError("Gmail wants an app password here, not your normal "
                                 "password") from None
            raise SetupError("Gmail refused that address and app password. App passwords "
                             "only exist once 2-Step Verification is on") from None
        typ, data = conn.select("INBOX", readonly=True)
        count = data[0].decode() if typ == "OK" and data and data[0] else ""
        return "signed in" + (f", {count} emails in the inbox" if count.isdigit() else "")
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def _telegram(token: str, method: str, verb: str = "GET", **params):
    if verb == "GET":
        resp = _http("GET", f"https://api.telegram.org/bot{token}/{method}", params=params)
    else:
        resp = _http("POST", f"https://api.telegram.org/bot{token}/{method}", data=params)
    try:
        body = resp.json()
    except ValueError:
        body = {}
    if resp.status_code in (401, 404):
        raise SetupError("Telegram does not know that bot token. Copy it again from @BotFather")
    if resp.status_code == 409:
        raise SetupError("this bot has a webhook, so its messages cannot be read from here")
    if not body.get("ok"):
        raise SetupError(f"Telegram said: {body.get('description') or resp.status_code}")
    return body["result"]


def telegram_bot(token: str) -> str:
    return "@" + _telegram(token, "getMe")["username"]


def telegram_chats(token: str) -> list:
    """(chat id, name) for every chat that has written to the bot lately."""
    chats: dict[str, str] = {}
    for update in _telegram(token, "getUpdates", timeout=0):
        for kind in ("message", "edited_message", "channel_post", "my_chat_member"):
            chat = (update.get(kind) or {}).get("chat")
            if chat:
                name = (chat.get("title")
                        or " ".join(filter(None, (chat.get("first_name"), chat.get("last_name"))))
                        or chat.get("username") or "?")
                chats[str(chat["id"])] = name
    return list(chats.items())


def telegram_send(token: str, chat_id: str) -> str:
    _telegram(token, "sendMessage", verb="POST", chat_id=chat_id,
              text="jobsift is connected. The best job matches will arrive here.")
    return "sent. It should be on your phone now"


def check_sheet(key_file: str, sheet_id: str) -> str:
    import gspread
    from google.oauth2.service_account import Credentials

    email = read_service_account(key_file)
    try:
        creds = Credentials.from_service_account_file(key_file, scopes=[SHEETS_SCOPE])
        title = gspread.authorize(creds).open_by_key(sheet_id).title
    except gspread.exceptions.SpreadsheetNotFound:
        raise SetupError(f"no sheet with that id is shared with {email}") from None
    except Exception as err:
        text = str(err)
        if "has not been used" in text or "SERVICE_DISABLED" in text or "is disabled" in text:
            raise SetupError("the Google Sheets API is not turned on in the key's project. "
                             "Enable it: https://console.cloud.google.com/apis/library/"
                             "sheets.googleapis.com") from None
        if "403" in text or "PERMISSION_DENIED" in text or "caller does not have permission" in text:
            raise SetupError(f"the sheet is not shared with {email} yet. Share it, as "
                             "Editor") from None
        if "invalid_grant" in text or "JWT" in text:
            raise SetupError("Google rejected the key itself; it was deleted or revoked. "
                             "Make a new JSON key") from None
        raise SetupError(f"could not open the sheet ({type(err).__name__}: {text[:160]})") from None
    return f'can open "{title}"'


@dataclass
class Checks:
    """Every network call the wizard makes, in one place so a dry run can fake them."""
    llm: Callable = check_llm
    gmail: Callable = check_gmail
    telegram_bot: Callable = telegram_bot
    telegram_chats: Callable = telegram_chats
    telegram_send: Callable = telegram_send
    sheet: Callable = check_sheet


# ── The conversation ─────────────────────────────────────────────────────────

GMAIL_HELP = """\
  jobsift reads job-alert emails from one Gmail inbox. It signs in with an app
  password: a separate 16-letter password Google makes for one program, so your
  real password never goes in a file. App passwords need 2-Step Verification.
  Make one here (name it anything, e.g. jobsift):
    https://myaccount.google.com/apppasswords
"""

BOTFATHER_HELP = """\
  1. In Telegram, open @BotFather, send /newbot and answer its two questions.
     It replies with a token that looks like 123456789:AAH...
"""

SHEET_INTRO = """\
  Every scored job lands in a spreadsheet you can sort, filter and work from your
  phone. It is the longest step - about ten minutes, mostly in Google Cloud - and
  Telegram and the CSV export work without it. Run --setup again to add it later.
"""

SERVICE_ACCOUNT_HELP = """\
  jobsift writes to the sheet as a service account: a robot Google account that
  can open only the sheets you share with it. Make one and download its key:
    1. https://console.cloud.google.com/projectcreate
       a new project, any name
    2. https://console.cloud.google.com/apis/library/sheets.googleapis.com
       press Enable
    3. https://console.cloud.google.com/iam-admin/serviceaccounts
       Create service account, name it jobsift, skip the optional steps
    4. open it > Keys > Add key > Create new key > JSON. A .json file downloads.
  Put that file in this folder. Treat it like a password.
"""

SHARE_HELP = """\
  5. Make a sheet (https://sheets.new), press Share, and add
       {email}
     as an Editor. Everything else can be right and it still fails without this.
"""


class _Wizard:
    def __init__(self, config_path: Path, env_path: Path, profile_path: Path,
                 ask, ask_secret, checks: Checks, out, examples: Path):
        self.config_path, self.env_path, self.profile_path = config_path, env_path, profile_path
        self.ask, self.ask_secret, self.checks, self.out = ask, ask_secret, checks, out
        self.examples = examples
        docker = bool(os.environ.get("JOBSIFT_DOCKER"))
        self.cmd = "docker compose run --rm jobsift" if docker else "python -m jobsift"
        self.keep_running = "docker compose up -d" if docker else "python -m jobsift"

    # small pieces --------------------------------------------------------

    def _title(self, number, name: str) -> None:
        self.out(f"\n{number} of 6 - {name}\n" if number else f"\n{name}\n")

    def _config_now(self) -> dict:
        if not self.config_path.is_file():
            return {}
        return yaml.safe_load(_read(self.config_path)) or {}

    def _env_now(self) -> dict:
        if not self.env_path.is_file():
            return {}
        return {key: value for key, value in dotenv_values(self.env_path).items()
                if (value or "") not in PLACEHOLDERS}

    def _save_env(self, updates: dict) -> None:
        text = _read(self.env_path) if self.env_path.is_file() else ""
        _write(self.env_path, set_env_values(text, updates), private=True)

    def _save_config(self, edits: list, insert: bool = False) -> None:
        text = _read(self.config_path)
        for path, value in edits:
            text = set_yaml_value(text, path, value, insert=insert)
        _write(self.config_path, text)

    def _text(self, prompt: str, current: str = "") -> str:
        shown = f" [{current}]" if current else ""
        return self.ask(f"  {prompt}{shown}: ").strip() or current

    def _secret(self, prompt: str, current: str = "") -> str:
        shown = f" [{_mask(current)}; Enter keeps it]" if current else ""
        return self.ask_secret(f"  {prompt}{shown}: ").strip() or current

    def _yes(self, prompt: str, default: bool) -> bool:
        answer = self.ask(f"  {prompt} [{'Y/n' if default else 'y/N'}]: ").strip().lower()
        return default if not answer else answer.startswith("y")

    def _checked(self, label: str, check, *args):
        """Run one check. Its description when it works; None, with the reason
        said out loud, when it does not."""
        self.out(f"  checking {label}...")
        try:
            result = check(*args)
        except SetupError as err:
            self.out(f"  not working: {err}.")
            return None
        self.out(f"  ok - {result}")
        return result

    def _after_failure(self) -> str:
        answer = self.ask("  Try again, keep it anyway, or skip this step? [T/k/s]: ").strip().lower()
        return {"k": "keep", "s": "skip"}.get(answer[:1], "retry")

    # the steps -----------------------------------------------------------

    def run(self) -> int:
        self.out("jobsift setup\n\n"
                 "Connects the accounts jobsift uses and checks each one works: an AI key,\n"
                 "your Gmail, and optionally Telegram and a Google Sheet. It also asks what\n"
                 "to filter out. Press Enter to keep what is already set. Secrets are saved\n"
                 "only in .env, and the checks read no email and call no AI model.")
        if not self._files():
            return 1
        self._llm()
        self._gmail()
        self._search()
        self._telegram()
        self._sheet()
        return self._finish()

    def _files(self) -> bool:
        self._title(1, "Files")
        resume = "resume.txt"
        pairs = [(self.config_path, "config.example.yaml", "settings, each one explained inside"),
                 (self.env_path, ".env.example", "secrets, filled in by the next steps"),
                 (None, "resume.example.txt", "your resume; every job is scored against it"),
                 (self.profile_path, "profile.example.yaml", "facts for drafting applications")]
        for target, example, what in pairs:
            if target is None:
                # Wherever config.yaml says, which is only knowable once it exists.
                resume = str(self._config_now().get("resume_path") or "./resume.txt")
                target = Path(resume)
            source = self.examples / example
            if target.exists():
                self.out(f"  {target} is already there; left as it is")
            elif source.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
                self.out(f"  made {target} from {example} - {what}")
            elif target == self.config_path:
                self.out(f"  there is no {target}, and no {example} to make one from.")
                return False
        return True

    def _llm(self) -> None:
        self._title(2, "AI provider")
        self.out("  jobsift uses an AI model to read and score each job, which costs money:\n"
                 "  roughly 150 calls for a day of alerts. It needs an API key from one\n"
                 "  provider, which bills you directly. Prices differ a lot between them;\n"
                 "  config.example.yaml lists what each default model costs.")
        cfg, env = self._config_now(), self._env_now()
        provider = str(cfg.get("llm_provider") or "anthropic").lower()
        while True:
            typed = self._text("AI provider: deepseek, openai or claude",
                               SHOWN.get(provider, provider)).lower()
            if typed in TYPED:
                provider = TYPED[typed]
                break
            self.out("  type deepseek, openai or claude")
        name = KEY_NAMES[provider]
        self.out(f"  Make a key at {KEY_PAGES[provider]}")
        while True:
            key = self._secret(f"{name} (typing is hidden)", env.get(name) or "")
            if not key:
                self.out("  No key entered; nothing changed. jobsift cannot run without one.")
                return
            if self._checked("the key", self.checks.llm, provider, key) is None:
                choice = self._after_failure()
                if choice == "retry":
                    continue
                if choice == "skip":
                    self.out("  skipped; nothing changed")
                    return
            self._save_env({name: key})
            edits = [(("llm_provider",), provider)] + _model_edits(cfg, provider)
            self._save_config(edits)
            for path, value in edits[1:]:
                self.out(f"  models.{path[1]} named the other provider's model; now {value}")
            self.out("  saved. (That checks the key, not the balance on the account.)")
            return

    def _gmail(self) -> None:
        self._title(3, "Gmail")
        self.out(GMAIL_HELP)
        env = self._env_now()
        while True:
            address = self._text("Gmail address", env.get("GMAIL_ADDRESS") or "")
            if not address:
                self.out("  No address entered; Gmail skipped for now.")
                return
            if "@" not in address:
                self.out("  that is not an email address")
                continue
            password = self._secret("App password (typing is hidden)",
                                    env.get("GMAIL_APP_PASSWORD") or "").replace(" ", "")
            if not password:
                self.out("  No app password entered; Gmail skipped for now.")
                return
            if self._checked("the sign-in", self.checks.gmail, address, password) is None:
                choice = self._after_failure()
                if choice == "retry":
                    continue
                if choice == "skip":
                    self.out("  skipped; nothing changed")
                    return
            self._save_env({"GMAIL_ADDRESS": address, "GMAIL_APP_PASSWORD": password})
            self.out("  saved.")
            return

    def _number(self, prompt: str, current: int, low: int = 0, high: int | None = None) -> int:
        """A whole number, as typed by a person: "50,000" and "50k" both mean 50000."""
        while True:
            raw = self._text(prompt, str(current)).lower().replace(",", "").replace(" ", "")
            if raw.endswith("k") and raw[:-1].isdigit():
                raw = str(int(raw[:-1]) * 1000)
            if raw.isdigit() and int(raw) >= low and (high is None or int(raw) <= high):
                return int(raw)
            self.out("  a whole number" + (f" from {low} to {high}" if high is not None else ""))

    def _search(self) -> None:
        from .filters import PRESET_TERMS

        self._title(4, "Your search")
        self.out("  What jobsift throws away before it spends anything on a job. Each answer\n"
                 "  is an on/off switch in config.yaml, so it can be changed there any time.")
        cfg = self._config_now()
        filters_cfg = cfg.get("filters") or {}
        own_titles = [str(t).lower() for t in filters_cfg.get("exclude_titles") or []]
        own_companies = [str(c).lower() for c in filters_cfg.get("exclude_companies") or []]
        edits = []
        for key, question, fallback in PRESETS:
            # A config written before the switches spells the words out itself.
            # Asked with the answer that matches what it already does.
            mine = own_companies if key == "skip_call_centres" else own_titles
            listed = [term for term in PRESET_TERMS[key] if term in mine]
            current = filters_cfg.get(key)
            answer = self._yes(question, bool(current) if current is not None
                               else (bool(listed) or fallback))
            edits.append((("filters", key), answer))
            if not answer and listed:
                shown = ", ".join(listed[:4]) + ("..." if len(listed) > 4 else "")
                self.out(f"    your own list in config.yaml still names {shown}; "
                         "remove those to let them through")
        floor = self._number("Lowest monthly pay you would take, in pesos (0 for no floor)",
                             int(filters_cfg.get("min_salary_php") or 0))
        threshold = self._number("Alert when a job scores at least, out of 100",
                                 int(cfg.get("score_threshold") or 60), 0, 100)
        edits += [(("filters", "min_salary_php"), floor), (("score_threshold",), threshold)]
        self._save_config(edits, insert=True)
        self.out("  saved.")

    def _telegram(self) -> None:
        self._title(5, "Telegram alerts (optional)")
        self.out("  The best matches arrive as a message on your phone. Free, about two minutes.")
        env = self._env_now()
        token_now = env.get("TELEGRAM_BOT_TOKEN") or ""
        chat_now = env.get("TELEGRAM_CHAT_ID") or ""
        if token_now and chat_now:
            if not self._yes(f"Telegram is already set up (chat {chat_now}). Set it up again?", False):
                return
        elif not self._yes("Set up Telegram alerts?", True):
            return
        self.out(BOTFATHER_HELP)
        while True:
            token = self._secret("Bot token (typing is hidden)", token_now)
            if not token:
                self.out("  No token entered; Telegram skipped for now.")
                return
            bot = self._checked("the token", self.checks.telegram_bot, token)
            if bot is None:
                choice = self._after_failure()
                if choice == "retry":
                    continue
                if choice == "skip":
                    self.out("  skipped; nothing changed")
                    return
            break
        chat = self._find_chat(token, bot, chat_now)
        if chat is None:
            self.out("  skipped; nothing changed")
            return
        if self._checked("a test message", self.checks.telegram_send, token, chat) is None:
            if not self._yes("Keep these settings anyway?", False):
                self.out("  nothing changed. Run --setup again to retry.")
                return
        self._save_env({"TELEGRAM_BOT_TOKEN": token, "TELEGRAM_CHAT_ID": chat})
        self._save_config([(("telegram_enabled",), True)])
        self.out("  saved.")

    def _find_chat(self, token: str, bot, chat_now: str):
        link = f"https://t.me/{bot[1:]}" if bot else "your bot's chat"
        self.out(f"  2. Open {link}, press Start, and send it any message.")
        while True:
            answer = self.ask("  Press Enter once it is sent (or type the chat id, "
                              "or s to skip): ").strip()
            if answer.lower() == "s":
                return None
            if answer:
                if re.fullmatch(r"-?\d+", answer):
                    return answer
                self.out("  a chat id is a number, like 123456789")
                continue
            try:
                chats = self.checks.telegram_chats(token)
            except SetupError as err:
                self.out(f"  could not look: {err}. Type the chat id instead.")
                continue
            if not chats:
                self.out("  no message has reached the bot yet. Check it is the right bot, "
                         "send one more, then press Enter.")
                continue
            if len(chats) == 1:
                self.out(f"  found {chats[0][1]} (chat {chats[0][0]})")
                return chats[0][0]
            for number, (chat_id, name) in enumerate(chats, 1):
                self.out(f"    {number}. {name} (chat {chat_id})")
            pick = self._text("Which one", "1")
            if pick.isdigit() and 1 <= int(pick) <= len(chats):
                return chats[int(pick) - 1][0]
            self.out("  pick a number from the list")

    def _sheet(self) -> None:
        self._title(6, "Google Sheet (optional)")
        sheet_cfg = self._config_now().get("google_sheet") or {}
        current_id = str(sheet_cfg.get("sheet_id") or "")
        current_id = "" if current_id in PLACEHOLDERS else current_id
        if sheet_cfg.get("enabled") and current_id:
            if not self._yes("The Google Sheet is already set up. Set it up again?", False):
                return
        else:
            self.out(SHEET_INTRO)
            if not self._yes("Set up the Google Sheet now?", False):
                return
        self.out(SERVICE_ACCOUNT_HELP)
        key_file = str(sheet_cfg.get("service_account_file") or "./service-account.json")
        while True:
            key_file = self._text("Key file", key_file)
            try:
                email = read_service_account(key_file)
                break
            except SetupError as err:
                self.out(f"  not working: {err}.")
                if self._after_failure() == "skip":
                    self.out("  skipped; nothing changed")
                    return
        self.out(f"  ok - the key is for {email}")
        self.out(SHARE_HELP.format(email=email))
        while True:
            raw = self._text("Sheet link or id", current_id)
            if not raw:
                self.out("  No sheet entered; skipped, nothing changed.")
                return
            sheet_id = parse_sheet_id(raw)
            if not sheet_id:
                self.out("  that is not a sheet link. Copy the address bar while the sheet is open.")
                continue
            if self._checked("the sheet", self.checks.sheet, key_file, sheet_id) is None:
                choice = self._after_failure()
                if choice == "retry":
                    continue
                if choice == "skip":
                    self.out("  skipped; nothing changed")
                    return
            break
        self._save_config([(("google_sheet", "enabled"), True),
                           (("google_sheet", "sheet_id"), sheet_id),
                           (("google_sheet", "service_account_file"), key_file)])
        self.out("  saved.")

    def _finish(self) -> int:
        self._title(None, "Where that leaves you")
        cfg, env = self._config_now(), self._env_now()
        provider = str(cfg.get("llm_provider") or "anthropic").lower()
        todo = [f"{name} is not set. Run --setup again, or put it in {self.env_path}."
                for name in (KEY_NAMES.get(provider, "ANTHROPIC_API_KEY"),
                             "GMAIL_ADDRESS", "GMAIL_APP_PASSWORD")
                if not env.get(name)]
        blocking = bool(todo)

        resume = Path(str(cfg.get("resume_path") or "./resume.txt"))
        example = self.examples / "resume.example.txt"
        if (resume.is_file() and example.is_file()
                and _read(resume).replace("\r\n", "\n") == _read(example).replace("\r\n", "\n")):
            todo.append(f"{resume} is still the example. Paste your own resume into it: "
                        "every job is scored against it.")
        evidence = cfg.get("evidence") or {}
        if evidence.get("enabled") and any("/path/to/" in str(root)
                                           for root in evidence.get("roots") or []):
            todo.append("evidence.roots in config.yaml is still the example path. Point it at "
                        "your projects (docs/evidence-index.md) or set evidence.enabled: false. "
                        "It only matters for drafting applications.")
        if todo:
            self.out("  Still to do:")
            self.out("\n".join(f"  - {item}" for item in todo))

        # The real test: the loader an actual run uses.
        try:
            load_config(str(self.config_path), str(self.env_path))
        except SystemExit as err:
            self.out(f"\n  jobsift will not start yet: {err}")
            return 1
        if blocking:
            return 1
        self.out("\n  config.yaml and .env load cleanly.\n\n  Next:")
        self.out(f"    {self.cmd} --suggest-senders\n"
                 "        which emails in your inbox look like job alerts it does not know yet\n"
                 f"    {self.cmd} --once --no-telegram\n"
                 "        one full pass that alerts nobody\n"
                 f"    {self.cmd} --export jobs.csv\n"
                 "        everything it found, and why each job was kept or dropped\n"
                 f"    {self.keep_running}\n"
                 "        and leave it running")
        return 0


def run(config_path: str = "config.yaml", env_path: str = ".env",
        profile_path: str = "profile.yaml", ask=None, ask_secret=None,
        checks: Checks | None = None, out=print, examples: Path = EXAMPLES) -> int:
    wizard = _Wizard(Path(config_path), Path(env_path), Path(profile_path),
                     ask or input, ask_secret or getpass.getpass, checks or Checks(),
                     out, Path(examples))
    try:
        return wizard.run()
    except (KeyboardInterrupt, EOFError):
        out("\n\nStopped. The steps already finished are saved; run --setup again to carry on.")
        return 1
