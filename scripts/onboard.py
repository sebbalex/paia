#!/usr/bin/env python3
"""First-run setup: ask the owner what an instance needs, then write it down.

The repository is the engine; an instance is `.env`, `context/`, `memory/wiki/`
and `routines/`. This walks a new owner through every question those need,
in one sitting, and writes nothing until they have seen the summary:

  seed      copy defaults/ into place without overwriting anything
  .env      tokens, owner, skills, Drive folders, coach, scraper, voice
  context/  the TO FILL lines in the template briefs
  wiki      memory/wiki/calendar.md, from the calendars `gws` can see
  instance  optionally, the private .instance.git beside the public one

Re-runnable. An existing .env supplies the defaults and is backed up before it
is rewritten; a brief line already filled is not asked again. Every question
can be skipped with Enter, which leaves the TO FILL where it is.

Secrets are read with getpass and go to .env (mode 600) and nowhere else. The
only network calls are to api.telegram.org with the bot's own token, and only
after asking.

Usage:
  python3 scripts/onboard.py                  # VPS or local checkout
  python3 scripts/onboard.py --addon          # print Home Assistant options
  python3 scripts/onboard.py --addon --root /addon_configs/<id>_paia
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

REPO = Path(__file__).resolve().parent.parent
TODAY = date.today().isoformat()

# Values that .env.example ships uncommented as placeholders. Left unanswered,
# they are commented out rather than written as a fake token.
_PLACEHOLDERS = {
    "CLAUDE_CODE_OAUTH_TOKEN",
    "ANTHROPIC_API_KEY",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_ALLOWED_USER_IDS",
    "MAIL_TO",
}
_SECRETS = {
    "CLAUDE_CODE_OAUTH_TOKEN",
    "ANTHROPIC_API_KEY",
    "OPENROUTER_API_KEY",
    "TELEGRAM_BOT_TOKEN",
    "INTERVALS_API_KEY",
    "SCRAPER_TOKEN",
    "SMTP_PASSWORD",
}
_TOKEN = re.compile(r"\*\*TO FILL\*\*|`TO FILL`")
# The one TO FILL that is a whole bullet rather than a value on one.
CALENDAR_BULLET = (
    "- **TO FILL** — one line per calendar: its name, and whether to surface it\n"
    "  always, or only as background.\n"
)


# -- prompting --------------------------------------------------------------


def section(title: str, why: str = "") -> None:
    print(f"\n== {title} " + "=" * max(0, 60 - len(title)))
    if why:
        print(why)


def ask(question: str, default: str = "", hint: str = "") -> str:
    if hint:
        print(f"  ({hint})")
    shown = f" [{default}]" if default else ""
    answer = input(f"{question}{shown}: ").strip()
    return answer or default


def ask_secret(question: str, current: str = "") -> str:
    shown = " [keep current]" if current else ""
    answer = getpass.getpass(f"{question}{shown}: ").strip()
    return answer or current


def confirm(question: str, default: bool = True) -> bool:
    answer = input(f"{question} [{'Y/n' if default else 'y/N'}]: ").strip().lower()
    if not answer:
        return default
    return answer.startswith("y")


def choose(question: str, options: list[str], default: int = 0) -> int:
    for i, option in enumerate(options, 1):
        print(f"  {i}. {option}")
    while True:
        answer = input(f"{question} [{default + 1}]: ").strip()
        if not answer:
            return default
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            return int(answer) - 1
        print("  pick a number from the list")


def folder_id(text: str) -> str:
    """A Drive folder id, from either the id or the folder's URL."""
    text = text.strip()
    match = re.search(r"/folders/([A-Za-z0-9_-]+)", text)
    return match.group(1) if match else text


def split_list(text: str) -> list[str]:
    return [part.strip() for part in text.split(",") if part.strip()]


def mask(key: str, value: str) -> str:
    if key in _SECRETS and value:
        return value[:10] + "..." if len(value) > 14 else "***"
    return value


# -- .env -------------------------------------------------------------------


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.split(" #", 1)[0].strip().strip("'\"")
        values[key.strip()] = value
    return values


def render_env(template: str, values: dict[str, str]) -> str:
    """.env.example with the answers in place, comments and order kept.

    A key is set on the first line that carries it, commented or not --
    `# KEY=` with at most one space, so the indented examples inside comments
    stay examples. A placeholder nobody answered is commented out. Keys the
    template does not know go at the end.
    """
    done: set[str] = set()
    out: list[str] = []
    for line in template.splitlines():
        match = re.match(r"^(# ?)?([A-Z][A-Z0-9_]*)=", line)
        if not match:
            out.append(line)
            continue
        key = match.group(2)
        if key in values and key not in done:
            out.append(f"{key}={values[key]}" if values[key] else f"# {key}=")
            done.add(key)
        elif match.group(1) is None and key in _PLACEHOLDERS:
            out.append(f"# {line}")
        else:
            out.append(line)
    extra = [key for key in values if key not in done and values[key]]
    if extra:
        out += ["", "# -- set by scripts/onboard.py, not in .env.example"]
        out += [f"{key}={values[key]}" for key in extra]
    return "\n".join(out) + "\n"


def write_env(path: Path, text: str) -> None:
    if path.exists():
        backup = path.with_name(f".env.bak-{datetime.now():%Y%m%d-%H%M%S}")
        shutil.copy2(path, backup)
        os.chmod(backup, 0o600)
        print(f"  backed up the old .env to {backup.name}")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(text)
    os.chmod(path, 0o600)


# -- briefs -----------------------------------------------------------------


def unfilled(text: str, label: str) -> bool:
    return re.search(rf"^- {label}[^\n]*?(?:{_TOKEN.pattern})", text, re.M) is not None


def fill(text: str, label: str, answer: str) -> str:
    """Put an answer where a brief says TO FILL, on the bullet starting `label`.

    The hint in parentheses after the token goes with it, even when it wraps
    onto the next line. A backticked token keeps its backticks, so a folder id
    stays code. Unknown label or already filled: the text comes back unchanged.
    """
    pattern = re.compile(rf"(^- {label}[^\n]*?)({_TOKEN.pattern})( \([^)]*\))?", re.M)
    match = pattern.search(text)
    if not match:
        return text
    value = f"`{answer}`" if match.group(2).startswith("`") else answer
    return text[: match.start(2)] + value + text[match.end() :]


def bump(text: str) -> str:
    return re.sub(r"^updated: .*$", f"updated: {TODAY}", text, count=1, flags=re.M)


class Briefs:
    """The context/ files, edited in memory and written once at the end."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.texts: dict[str, str] = {}
        self.changed: set[str] = set()

    def text(self, name: str) -> str:
        if name not in self.texts:
            path = self.folder / f"{name}.md"
            self.texts[name] = path.read_text() if path.exists() else ""
        return self.texts[name]

    def open(self, name: str, label: str) -> bool:
        return unfilled(self.text(name), label)

    def set(self, name: str, label: str, answer: str) -> None:
        if not answer:
            return
        new = fill(self.text(name), label, answer)
        if new != self.texts[name]:
            self.texts[name] = new
            self.changed.add(name)

    def replace(self, name: str, old: str, new: str) -> None:
        text = self.text(name)
        if old in text:
            self.texts[name] = text.replace(old, new, 1)
            self.changed.add(name)

    def ask(self, name: str, label: str, question: str, hint: str = "") -> None:
        if self.open(name, label):
            self.set(name, label, ask(question, hint=hint))

    def write(self) -> list[Path]:
        written = []
        for name in sorted(self.changed):
            path = self.folder / f"{name}.md"
            path.write_text(bump(self.texts[name]))
            written.append(path)
        return written

    def remaining(self) -> list[str]:
        left = []
        for path in sorted(self.folder.glob("*.md")):
            text = self.texts.get(path.stem) or path.read_text()
            count = len(re.findall(rf"^- [^\n]*?(?:{_TOKEN.pattern})", text, re.M))
            if count:
                left.append(f"context/{path.name}: {count}")
        return left


# -- outside calls ----------------------------------------------------------


def telegram(token: str, method: str, params: dict | None = None) -> dict:
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = json.dumps(params or {}).encode()
    request = urllib.request.Request(url, data, {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        try:
            return json.load(error)
        except ValueError:
            return {"ok": False, "description": f"HTTP {error.code}"}
    except (urllib.error.URLError, TimeoutError) as error:
        return {"ok": False, "description": str(error)}


def senders(token: str) -> list[tuple[int, str]]:
    """Who has written to the bot, then mark those updates read.

    Acknowledging them stops the assistant answering a stale /start the first
    time it polls.
    """
    reply = telegram(token, "getUpdates", {"timeout": 0})
    if not reply.get("ok"):
        print(f"  getUpdates failed: {reply.get('description')}")
        return []
    found: dict[int, str] = {}
    last = 0
    for update in reply["result"]:
        last = max(last, update["update_id"])
        message = update.get("message") or update.get("edited_message") or {}
        user = message.get("from") or {}
        if user.get("id") and not user.get("is_bot"):
            found[user["id"]] = user.get("username") or user.get("first_name", "")
    if last:
        telegram(token, "getUpdates", {"offset": last + 1, "timeout": 0})
    return list(found.items())


def calendars(gws: str) -> list[dict] | None:
    argv = [gws, "calendar", "calendarList", "list", "--params", "{}", "--format", "json"]
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        print(f"  could not run gws: {error}")
        return None
    if done.returncode != 0:
        print(f"  gws failed: {(done.stderr or done.stdout).strip()[:300]}")
        return None
    try:
        return json.loads(done.stdout).get("items", [])
    except ValueError:
        print("  gws did not return JSON")
        return None


# -- the interview ----------------------------------------------------------


def interview(env: dict[str, str], briefs: Briefs, addon: bool) -> tuple[dict[str, str], dict]:
    """Every question, in the order an owner can answer them. Returns the new
    environment and anything else to write (calendar rows)."""
    new = dict(env)
    extra: dict = {}

    section("You", "Who the assistant serves. Briefs stay English; it talks to you in your language.")
    new["OWNER_NAME"] = ask("Your first name", env.get("OWNER_NAME", ""))
    new["OWNER_LANGUAGE"] = ask("Language you want answers in", env.get("OWNER_LANGUAGE", "English"))
    zone = _local_zone()
    while briefs.open("calendar-rules", "Timezone is"):
        tz = ask("Your timezone (IANA)", zone)
        if not tz:
            break
        try:
            ZoneInfo(tz)
        except (ZoneInfoNotFoundError, ValueError):
            print(f"  {tz!r} is not an IANA zone name, e.g. Europe/Rome")
            continue
        briefs.set("calendar-rules", "Timezone is", f"`{tz}`")
        break

    section("Model", "One credential, never two: each one shadows or switches the others.")
    current = (
        2 if env.get("OPENROUTER_API_KEY")
        else 1 if env.get("ANTHROPIC_API_KEY") and not env.get("CLAUDE_CODE_OAUTH_TOKEN")
        else 0
    )
    kind = choose(
        "How is it billed",
        [
            "Claude subscription  (`claude setup-token`, sk-ant-oat...)",
            "Claude API key  (console.anthropic.com, sk-ant-api...)",
            "OpenRouter  (any tool-calling model, sk-or-...; no claude-mem)",
        ],
        current,
    )
    new["CLAUDE_CODE_OAUTH_TOKEN"] = new["ANTHROPIC_API_KEY"] = new["OPENROUTER_API_KEY"] = ""
    if kind == 0:
        token = ask_secret("OAuth token", env.get("CLAUDE_CODE_OAUTH_TOKEN", ""))
        if token and not token.startswith("sk-ant-oat"):
            print("  warning: that does not look like an sk-ant-oat token")
        new["CLAUDE_CODE_OAUTH_TOKEN"] = token
    elif kind == 1:
        key = ask_secret("API key", env.get("ANTHROPIC_API_KEY", ""))
        if key and not key.startswith("sk-ant-api"):
            print("  warning: that does not look like an sk-ant-api key")
        new["ANTHROPIC_API_KEY"] = key
    else:
        key = ask_secret("OpenRouter key", env.get("OPENROUTER_API_KEY", ""))
        if key and not key.startswith("sk-or-"):
            print("  warning: that does not look like an sk-or- key")
        new["OPENROUTER_API_KEY"] = key
    model = env.get("ASSISTANT_MODEL", "")
    if kind == 2:
        # assistant/config.py refuses to start on a name without a vendor.
        while "/" not in (model := ask("OpenRouter model (vendor/model-name)", model if "/" in model else "")):
            print("  an OpenRouter slug has a vendor: vendor/model-name")
    else:
        model = ask("Model", model if model and "/" not in model else "claude-opus-5")
    new["ASSISTANT_MODEL"] = model

    section("Telegram", "Create a bot with @BotFather first; it gives you the token.")
    token = ask_secret("Bot token", env.get("TELEGRAM_BOT_TOKEN", ""))
    new["TELEGRAM_BOT_TOKEN"] = token
    ids = env.get("TELEGRAM_ALLOWED_USER_IDS", "")
    if token and confirm("Check the token with Telegram and find your user id?"):
        me = telegram(token, "getMe")
        if not me.get("ok"):
            print(f"  Telegram refused the token: {me.get('description')}")
        else:
            print(f"  token ok: @{me['result']['username']}")
            print(f"  Send any message to @{me['result']['username']} from your own account now.")
            print("  (Stop the assistant first if it is already running: two pollers conflict.)")
            input("  Press Enter once it is sent...")
            found = senders(token)
            for uid, name in found:
                if confirm(f"  Allow {name} ({uid})?"):
                    ids = ",".join(dict.fromkeys(split_list(ids) + [str(uid)]))
            if not found:
                print("  nobody found; type the id instead (@userinfobot tells you yours)")
    new["TELEGRAM_ALLOWED_USER_IDS"] = ask(
        "Allowed Telegram user ids, comma-separated", ids, "this list is the whole access control"
    )

    section("Skills", "Methods the assistant follows. Off means invisible to it.")
    on = set(split_list(env.get("ASSISTANT_SKILLS", "price-hunt,adventure-log,trip-design")))
    skills = []
    for path in sorted((REPO / ".claude" / "skills").iterdir()):
        if (path / "SKILL.md").exists() and confirm(f"Enable {path.name}?", path.name in on):
            skills.append(path.name)
    new["ASSISTANT_SKILLS"] = ",".join(skills)

    section(
        "Google",
        "Calendar, Gmail and Drive go through the `gws` CLI. Authenticate it first:\n"
        "  gws auth setup && gws auth login   (as the user the assistant runs as)",
    )
    if not addon:
        found_gws = env.get("GWS_BIN") or shutil.which("gws") or ""
        new["GWS_BIN"] = ask("Path to gws (empty disables Google)", found_gws, "absolute, systemd has no PATH")
        if new["GWS_BIN"] and confirm("List your calendars now with gws?"):
            items = calendars(new["GWS_BIN"])
            if items is not None:
                extra["calendars"] = _calendar_choices(items)
    if "calendars" not in extra and CALENDAR_BULLET in briefs.text("calendar-rules"):
        names = ask("Calendars to show in full, comma-separated (names)", hint="Enter to fill in later")
        if names:
            extra["calendars"] = [{"summary": n, "id": "", "timeZone": "", "surface": "always"} for n in split_list(names)]
    new["MAIL_TO"] = ask("Address routines may mail (only this one)", env.get("MAIL_TO", ""))

    allowed = split_list(env.get("DRIVE_ALLOWED_FOLDERS", ""))
    writable = split_list(env.get("DRIVE_WRITE_FOLDERS", ""))
    if {"adventure-log", "trip-design"} & set(skills) and briefs.open("adventures-rules", "folder"):
        raw = ask("Drive folder for trip documents (id or URL)")
        if raw:
            fid = folder_id(raw)
            briefs.set("adventures-rules", "folder", fid)
            allowed.append(fid)
            if confirm("May the assistant create and edit Docs there (after your yes in chat)?"):
                writable.append(fid)
    more = ask("Other Drive folders it may read (ids or URLs, comma-separated)", hint="empty list = whole Drive")
    allowed += [folder_id(part) for part in split_list(more)]
    allowed = list(dict.fromkeys(allowed))
    if not allowed and new.get("GWS_BIN"):
        print("  warning: no folders listed means the whole Drive is readable")
    new["DRIVE_ALLOWED_FOLDERS"] = ",".join(allowed)
    new["DRIVE_WRITE_FOLDERS"] = ",".join(dict.fromkeys(f for f in writable if f in allowed))

    if briefs.open("shopping-rules", "Ship to"):
        section("Shopping")
    briefs.ask("shopping-rules", "Ship to", "Ship to (country, acceptable sellers)", "e.g. Italy, EU sellers only")

    if "coach" in skills:
        section("Coach", "Intervals.icu > Settings > Developer Settings gives both. Connect Garmin directly, not via Strava.")
        new["INTERVALS_API_KEY"] = ask_secret("Intervals.icu API key", env.get("INTERVALS_API_KEY", ""))
        new["INTERVALS_ATHLETE_ID"] = ask("Intervals.icu athlete id", env.get("INTERVALS_ATHLETE_ID", ""), "i12345")
        new["HRV_CUTOVER_DATE"] = ask(
            "Day a new watch took over overnight HRV (YYYY-MM-DD)", env.get("HRV_CUTOVER_DATE", ""), "empty if never"
        )
        for label, question, hint in _TRAINING:
            briefs.ask("training-rules", label, question, hint)

    section("Price scraper", "Optional worker that opens shop pages properly. Empty = search only.")
    default_url = env.get("SCRAPER_URL", "")
    new["SCRAPER_URL"] = ask("Scraper URL", default_url, "http://127.0.0.1:8099 on a VPS")
    if new["SCRAPER_URL"] and not env.get("SCRAPER_TOKEN"):
        new["SCRAPER_TOKEN"] = secrets.token_hex(32)
        print("  generated SCRAPER_TOKEN; the worker needs the same value")

    section("Voice notes", "Transcribed on this machine. Nothing leaves it.")
    new["WHISPER_MODEL"] = ask("Whisper model (tiny|base|small|medium)", env.get("WHISPER_MODEL", "small"), "base on a Pi")
    new["WHISPER_LANGUAGE"] = ask("Force a language code (empty = detect)", env.get("WHISPER_LANGUAGE", ""))

    if not addon:
        new["ASSISTANT_ROOT"] = env.get("ASSISTANT_ROOT") or str(REPO)
    return new, extra


_TRAINING = [
    ("discipline and sessions a week", "Discipline and sessions a week", "e.g. road, 4"),
    ("age, weight, years riding", "Age, weight, years riding", "e.g. 42, 74 kg, 8 years"),
    ("injuries and niggles", "Injuries or niggles to train around", "none, if none"),
    ("typical week", "Typical week", "e.g. 1 h on weekdays, long ride at the weekend"),
    ("training mode", "Training mode", "bike only, or bike plus gym"),
    ("fixed constraints", "Fixed constraints", "work, family, weather, commute"),
    ("event and date", "Goal event and date", "e.g. Maratona dles Dolomites, 2027-07-04"),
    ("distance, elevation and target", "Its distance, elevation and your target", "e.g. 138 km, 4230 m D+, finish"),
    ("devices", "Devices", "head unit, power meter, HR strap, watch"),
    ("Pre-ride reminder", "Pre-ride reminder", "e.g. zero-offset the power meter"),
]


def _local_zone() -> str:
    try:
        link = os.readlink("/etc/localtime")
        return link.split("zoneinfo/", 1)[1]
    except (OSError, IndexError):
        return os.environ.get("TZ", "")


def _calendar_choices(items: list[dict]) -> list[dict]:
    picked = []
    for item in items:
        name = item.get("summaryOverride") or item.get("summary") or item.get("id")
        if "holiday" in item.get("id", ""):
            surface = "background"
            print(f"  {name}: public holidays, background")
        else:
            surface = ["always", "background", "ignore"][choose(f"  {name}", ["show always", "background only", "ignore"])]
        picked.append({"summary": name, "id": item.get("id", ""), "timeZone": item.get("timeZone", ""), "surface": surface})
    return picked


def calendar_rules_lines(rows: list[dict]) -> str:
    words = {"always": "surface always", "background": "background only", "ignore": "ignore"}
    return "".join(f"- **{row['summary']}** — {words[row['surface']]}.\n" for row in rows)


def calendar_wiki(text: str, rows: list[dict]) -> str:
    rows = [row for row in rows if row["id"]]
    if not rows or "| id |" in text:
        return text
    table = ["", "| calendar | id | timezone | surfaced |", "|---|---|---|---|"]
    table += [f"| {r['summary']} | `{r['id']}` | {r['timeZone']} | {r['surface']} |" for r in rows]
    return bump(text.rstrip("\n") + "\n" + "\n".join(table) + "\n")


# -- addon ------------------------------------------------------------------

_ADDON = {
    "TELEGRAM_BOT_TOKEN": "telegram_bot_token",
    "CLAUDE_CODE_OAUTH_TOKEN": "claude_oauth_token",
    "ANTHROPIC_API_KEY": "anthropic_api_key",
    "OPENROUTER_API_KEY": "openrouter_api_key",
    "ASSISTANT_MODEL": "model",
    "OWNER_NAME": "owner_name",
    "OWNER_LANGUAGE": "owner_language",
    "DRIVE_WRITE_FOLDERS": "drive_write_folders",
    "MAIL_TO": "mail_to",
    "SCRAPER_URL": "scraper_url",
    "SCRAPER_TOKEN": "scraper_token",
    "INTERVALS_API_KEY": "intervals_api_key",
    "INTERVALS_ATHLETE_ID": "intervals_athlete_id",
    "HRV_CUTOVER_DATE": "hrv_cutover_date",
    "WHISPER_MODEL": "whisper_model",
    "WHISPER_LANGUAGE": "whisper_language",
}
_ADDON_LISTS = {
    "TELEGRAM_ALLOWED_USER_IDS": "telegram_allowed_user_ids",
    "ASSISTANT_SKILLS": "skills",
    "DRIVE_ALLOWED_FOLDERS": "drive_allowed_folders",
}


def addon_options(env: dict[str, str]) -> str:
    lines = []
    for key, option in _ADDON_LISTS.items():
        items = split_list(env.get(key, ""))
        if key == "TELEGRAM_ALLOWED_USER_IDS":
            lines.append(f"{option}: [{', '.join(items)}]")
        else:
            lines.append(f"{option}: [{', '.join(json.dumps(i) for i in items)}]")
    for key, option in _ADDON.items():
        if env.get(key):
            lines.append(f"{option}: {json.dumps(env[key])}")
    return "\n".join(lines) + "\n"


# -- the rest ---------------------------------------------------------------


def seed(root: Path, addon: bool) -> list[str]:
    """defaults/ into place, never over a file that exists (cp -Rn)."""
    copied = []
    parts = ["context"] if addon else ["context", "memory", "routines"]
    for part in parts:
        source = REPO / "defaults" / part
        for path in sorted(source.rglob("*")):
            if path.is_dir():
                continue
            target = root / path.relative_to(REPO / "defaults")
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
                copied.append(str(target.relative_to(root)))
    return copied


def init_instance(root: Path) -> None:
    git = ["git", "--git-dir=.instance.git"]
    steps = [
        ["git", "init", "--bare", "-q", ".instance.git"],
        git + ["config", "core.bare", "false"],
        git + ["config", "core.worktree", str(root)],
        git
        + [
            "config",
            "alias.snap",
            '!f() { git add -f context memory/wiki routines && git commit -m "${1:-snapshot $(date +%F)}"; }; f',
        ],
    ]
    for argv in steps:
        subprocess.run(argv, cwd=root, check=True)
    (root / ".instance.git" / "info" / "exclude").write_text(
        "/*\n!/context/\n!/memory/\n!/routines/\n/memory/raw/\n"
    )
    subprocess.run(git + ["snap", "onboarding"], cwd=root, check=False)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--root", type=Path, default=REPO, help="instance root (default: this checkout)")
    parser.add_argument("--addon", action="store_true", help="Home Assistant: print options, edit briefs only")
    args = parser.parse_args()
    root = args.root.resolve()

    print("PAIA onboarding. Enter keeps the default in [brackets] or skips the question.")
    print("Nothing is written until the end, and you see a summary first.")

    env_path = REPO / ".env"
    env = {} if args.addon else read_env(env_path)
    # Briefs are read from defaults/ when context/ is not seeded yet, so the
    # questions know which lines are still TO FILL.
    folder = root / "context" if (root / "context" / "calendar-rules.md").exists() else REPO / "defaults" / "context"
    briefs = Briefs(folder)

    try:
        new, extra = interview(env, briefs, args.addon)
    except (KeyboardInterrupt, EOFError):
        print("\nStopped. Nothing written.")
        return 1

    rows = extra.get("calendars")
    if rows:
        briefs.replace("calendar-rules", CALENDAR_BULLET, calendar_rules_lines(rows))

    section("Summary")
    for key in sorted(new):
        if new[key] != env.get(key, "") or args.addon:
            print(f"  {key}={mask(key, new[key])}")
    for name in sorted(briefs.changed):
        print(f"  context/{name}.md: filled")
    if not confirm("\nWrite this?"):
        print("Nothing written.")
        return 1

    for path in seed(root, args.addon):
        print(f"  seeded {path}")
    briefs.folder = root / "context"
    for path in briefs.write():
        print(f"  wrote {path.relative_to(root)}")

    if args.addon:
        print("\nPaste into the add-on's Configuration tab (YAML mode). It holds secrets: do not share it.\n")
        print(addon_options(new))
    else:
        write_env(env_path, render_env((REPO / ".env.example").read_text(), new))
        print("  wrote .env (mode 600)")
        if rows:
            wiki = root / "memory" / "wiki" / "calendar.md"
            wiki.write_text(calendar_wiki(wiki.read_text(), rows))
            print("  wrote memory/wiki/calendar.md")
        if not (root / ".instance.git").exists() and confirm(
            "Keep a private git history of your briefs and wiki (.instance.git)?"
        ):
            init_instance(root)
            print("  .instance.git ready; `git --git-dir=.instance.git snap` takes a snapshot")

    left = briefs.remaining()
    section("Next")
    if left:
        print("Still TO FILL (the assistant treats these as unknown and asks):")
        for line in left:
            print(f"  {line}")
    print("Then: ONBOARDING.md, part 3 -- the first conversation, and enabling routines.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
