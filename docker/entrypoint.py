#!/usr/bin/env python3
"""Container entrypoint for the Home Assistant add-on.

An add-on gets one persistent directory, /data, and a brand new image on every
upgrade. So there are four jobs before the assistant can say a word:

  options   turn /data/options.json into the environment assistant/config.py
            expects, which on the VPS comes from the systemd EnvironmentFile
  seed      copy the image's code onto /data/paia, keeping every
            directory that holds state (memory, routines, models, ...)
  own       code owned by root and unwritable; state owned by `assistant`
  drop      run the assistant as `assistant`, never as root

Why the code is *copied* onto the volume instead of staying in the image with
the mutable directories symlinked in: assistant/policy.py canonicalises every
path with os.path.realpath before deciding, so a symlink pointing out of the
assistant's root collapses to a path outside it and every single write is
refused. ASSISTANT_ROOT has to be one real directory tree.

That copy also has to preserve the property the systemd unit gets from
ReadOnlyPaths: the process running the model cannot rewrite the code, the
identity files, or what runs next boot. Here the filesystem does it by
ownership -- the tree is root-owned and mode 0444/0555, and only the
directories the policy allows writes into belong to `assistant`.
"""

from __future__ import annotations

import json
import os
import pwd
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

SRC = Path(os.environ.get("ASSISTANT_SRC", "/opt/paia-src"))
# Empty templates for the owner's instance: briefs, wiki skeleton, disabled
# routines. The real ones are never in the image (see .dockerignore).
DEFAULTS = SRC / "defaults"
ROOT = Path(os.environ.get("ASSISTANT_ROOT", "/data/paia"))
DATA = Path("/data")
OPTIONS = DATA / "options.json"
HOME = Path(os.environ.get("HOME", "/data/home"))
# `addon_config: rw` in addon/config.yaml, which the supervisor mounts here and
# surfaces to the owner as /addon_configs/<slug> in the file editor and over Samba.
# It is how context/ stays theirs: the briefs are editable without rebuilding the
# image, and are copied in read-only at every start.
CONFIG = Path("/config")
USER = "assistant"

# Seeded on first boot and never overwritten afterwards: this is state, and an
# upgrade that replaced it would throw away the memory, the routines the owner
# enabled and the skills the assistant wrote. New entries the image ships --
# a routine added upstream, a new skill -- do appear, because seeding is per
# child: what exists is left alone, what is missing is copied in.
KEEP = (
    "memory",
    "routines",
    "inbox",
    "state",
    "models",
    "scripts/proposals",
    ".claude/skills",
)

# Inside KEEP, but what the image ships here wins at every start. A skill in
# the repo is the owner's method, versioned in git: seeding it only when missing
# meant an edit to a skill already on the volume -- coach's SKILL.md, say --
# never reached the running assistant. The cost: an edit the assistant made
# on the volume to a skill the image also ships is overwritten on restart.
# Its own skills, which the image does not ship, are kept.
SHIPPED_WIN = (".claude/skills",)

# Owned by `assistant` and writable. The first four are the process's own
# state; the last three are the only paths assistant/policy.py lets the model
# write, and they are listed there as _WRITE_ROOTS.
WRITABLE = KEEP

# Owned by `assistant` but only one level deep, so the CLI can drop a file
# beside the code without being able to modify any of it.
OPEN_DIRS = (".", ".claude")

# option name in /data/options.json -> environment variable in .env.example
OPTION_ENV = {
    "claude_oauth_token": "CLAUDE_CODE_OAUTH_TOKEN",
    "anthropic_api_key": "ANTHROPIC_API_KEY",
    "openrouter_api_key": "OPENROUTER_API_KEY",
    "openrouter_search_model": "OPENROUTER_SEARCH_MODEL",
    "openrouter_images": "OPENROUTER_IMAGES",
    "telegram_bot_token": "TELEGRAM_BOT_TOKEN",
    "telegram_allowed_user_ids": "TELEGRAM_ALLOWED_USER_IDS",
    "model": "ASSISTANT_MODEL",
    "owner_name": "OWNER_NAME",
    "owner_language": "OWNER_LANGUAGE",
    "skills": "ASSISTANT_SKILLS",
    "drive_allowed_folders": "DRIVE_ALLOWED_FOLDERS",
    "drive_write_folders": "DRIVE_WRITE_FOLDERS",
    "web_fetch_allowed_domains": "WEB_FETCH_ALLOWED_DOMAINS",
    "scraper_url": "SCRAPER_URL",
    "scraper_token": "SCRAPER_TOKEN",
    "intervals_api_key": "INTERVALS_API_KEY",
    "intervals_athlete_id": "INTERVALS_ATHLETE_ID",
    "hrv_cutover_date": "HRV_CUTOVER_DATE",
    "whisper_model": "WHISPER_MODEL",
    "whisper_language": "WHISPER_LANGUAGE",
    "smtp_host": "SMTP_HOST",
    "smtp_port": "SMTP_PORT",
    "smtp_user": "SMTP_USER",
    "smtp_password": "SMTP_PASSWORD",
    "smtp_from": "SMTP_FROM",
    "smtp_to": "SMTP_TO",
    "mail_to": "MAIL_TO",
    "smtp_tls": "SMTP_TLS",
}

_stopping = False


def log(message: str) -> None:
    print(f"[entrypoint] {message}", flush=True)


# -- options ---------------------------------------------------------------


def _flatten(value: object) -> str | None:
    """One option value as the environment wants it, or None to leave it out.

    Absent and empty are the same thing here: assistant/config.py treats an
    empty GWS_BIN or DRIVE_ALLOWED_FOLDERS as a deliberate choice, and an
    option the user never filled in should not look like one.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, list):
        joined = ",".join(str(v).strip() for v in value if str(v).strip())
        return joined or None
    text = str(value).strip()
    return text or None


def build_env() -> dict[str, str]:
    options: dict = {}
    if OPTIONS.is_file():
        try:
            options = json.loads(OPTIONS.read_text())
        except (OSError, ValueError) as exc:
            raise SystemExit(f"cannot read {OPTIONS}: {exc}")
    else:
        log(f"{OPTIONS} not found -- falling back to the process environment")

    env = {
        "PATH": os.environ.get("PATH", "/opt/venv/bin:/usr/local/bin:/usr/bin:/bin"),
        "HOME": str(HOME),
        "ASSISTANT_ROOT": str(ROOT),
        "PYTHONUNBUFFERED": "1",
        # A bare name resolves through PATH, which the child does inherit, but
        # being explicit here is what .env.example asks for on a server.
        "GWS_BIN": "/usr/local/bin/gws",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TZ": os.environ.get("TZ", "UTC"),
    }
    for name in ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "OPENROUTER_API_KEY",
                 "TELEGRAM_BOT_TOKEN",
                 "TELEGRAM_ALLOWED_USER_IDS", "ASSISTANT_MODEL", "ASSISTANT_SKILLS"):
        if os.environ.get(name):
            env[name] = os.environ[name]

    for option, variable in OPTION_ENV.items():
        flat = _flatten(options.get(option))
        if flat is not None:
            env[variable] = flat

    # Both credential types set is the failure .env.example warns about:
    # ANTHROPIC_API_KEY wins and a stale one silently shadows a good token.
    if env.get("ANTHROPIC_API_KEY") and env.get("CLAUDE_CODE_OAUTH_TOKEN"):
        raise SystemExit(
            "both anthropic_api_key and claude_oauth_token are set. They are "
            "different credential types and the API key takes precedence, so "
            "leave exactly one of them filled in."
        )
    claude = env.get("ANTHROPIC_API_KEY") or env.get("CLAUDE_CODE_OAUTH_TOKEN")
    # The OpenRouter key picks the other brain outright (assistant/config.py),
    # so a Claude credential beside it would sit unused and look like it
    # mattered. One brain, one credential.
    if claude and env.get("OPENROUTER_API_KEY"):
        raise SystemExit(
            "both a Claude credential and openrouter_api_key are set. The "
            "OpenRouter key switches the assistant to OpenRouter, so leave "
            "exactly one kind filled in."
        )
    if not claude and not env.get("OPENROUTER_API_KEY"):
        raise SystemExit(
            "no model credential. Fill in claude_oauth_token "
            "(`claude setup-token`, a subscription), anthropic_api_key "
            "(console.anthropic.com, metered), or openrouter_api_key with an "
            "OpenRouter slug in model."
        )
    # Checked here rather than left to assistant/config.py: _require() there
    # raises inside the supervised child, which would restart on a five second
    # loop and bury the reason in the log.
    for variable, option in (("TELEGRAM_BOT_TOKEN", "telegram_bot_token"),
                             ("TELEGRAM_ALLOWED_USER_IDS", "telegram_allowed_user_ids")):
        if not env.get(variable):
            raise SystemExit(f"option '{option}' is empty and the add-on cannot start without it")
    for raw in env["TELEGRAM_ALLOWED_USER_IDS"].split(","):
        if not raw.strip().isdigit():
            raise SystemExit(
                f"telegram_allowed_user_ids: '{raw.strip()}' is not a numeric "
                "Telegram user id -- @userinfobot tells you yours"
            )
    return env


# -- seeding ---------------------------------------------------------------


def _copy(src: Path, dst: Path) -> None:
    if src.is_dir():
        shutil.copytree(src, dst, symlinks=False)
    else:
        shutil.copy2(src, dst)


def _seed_children(src: Path, dst: Path) -> None:
    """Fill in what is missing, touch nothing that is already there."""
    dst.mkdir(parents=True, exist_ok=True)
    if not src.is_dir():
        return
    for child in sorted(src.iterdir()):
        target = dst / child.name
        if not target.exists():
            _copy(child, target)


def _sync(src: Path, dst: Path, rel: str = "") -> None:
    if rel in SHIPPED_WIN:
        # Each skill the image ships is replaced whole; skills only on the
        # volume -- the ones the assistant wrote -- are left alone.
        dst.mkdir(parents=True, exist_ok=True)
        for child in sorted(src.iterdir()):
            _sync(child, dst / child.name, f"{rel}/{child.name}")
        return
    if rel in KEEP:
        _seed_children(src, dst)
        return
    if src.is_dir():
        dst.mkdir(parents=True, exist_ok=True)
        names = {c.name for c in src.iterdir()}
        # Anything the image no longer ships goes, so an upgrade that deletes
        # a module does not leave it behind to be imported.
        for stale in dst.iterdir():
            if stale.name in names:
                continue
            if f"{rel}/{stale.name}".lstrip("/") in KEEP:
                continue
            if stale.is_dir() and not stale.is_symlink():
                shutil.rmtree(stale, ignore_errors=True)
            else:
                stale.unlink(missing_ok=True)
        for child in sorted(src.iterdir()):
            _sync(child, dst / child.name, f"{rel}/{child.name}".lstrip("/"))
        return
    if dst.is_dir() and not dst.is_symlink():
        shutil.rmtree(dst, ignore_errors=True)
    shutil.copy2(src, dst)


def seed() -> None:
    if not SRC.is_dir():
        raise SystemExit(f"no source tree at {SRC}")
    log(f"seeding {ROOT} from {SRC}")
    ROOT.mkdir(parents=True, exist_ok=True)
    for entry in sorted(SRC.iterdir()):
        if entry.name == "defaults":
            continue
        _sync(entry, ROOT / entry.name, entry.name)
    for rel in KEEP:
        (ROOT / rel).mkdir(parents=True, exist_ok=True)
    # memory/raw is deliberately not in the image (see .dockerignore) and the
    # transport expects it to exist.
    (ROOT / "memory" / "raw").mkdir(parents=True, exist_ok=True)
    # The image carries no one's memory or routines, only the empty shapes in
    # defaults/. Per file and only when missing, like the rest of KEEP: a page
    # the owner already has is theirs, a page a new release adds shows up.
    for rel in ("memory/wiki", "routines"):
        _seed_children(DEFAULTS / rel, ROOT / rel)
    _seed_briefs()


def _seed_briefs() -> None:
    """Let context/ be edited from outside the image.

    ROT.md is explicit that context/ is the owner's and the assistant only applies
    it -- but inside a container the image is the assistant's, so a brief they
    cannot edit without a rebuild is a brief they do not own. The add-on
    config share is the way out: the image seeds it once, and from then on
    what is in there is what the assistant reads.
    """
    if not CONFIG.is_dir():
        # Outside Home Assistant there is no share to edit them in: the
        # template briefs are all there is, re-seeded at every start.
        _seed_children(DEFAULTS / "context", ROOT / "context")
        return
    briefs = CONFIG / "context"
    if not briefs.exists():
        log(f"seeding {briefs} with the template briefs from the image")
        _copy(DEFAULTS / "context", briefs)
    _sync(briefs, ROOT / "context", "context")


# -- ownership -------------------------------------------------------------


def _walk(path: Path, uid: int, gid: int, dir_mode: int, file_mode: int) -> None:
    if path.is_symlink():
        return
    os.chown(path, uid, gid)
    if path.is_dir():
        os.chmod(path, dir_mode)
        for child in path.iterdir():
            _walk(child, uid, gid, dir_mode, file_mode)
    else:
        os.chmod(path, file_mode)


def _chown_tree(path: Path, uid: int, gid: int) -> None:
    """Ownership only, modes untouched -- HOME holds executables."""
    if path.is_symlink() or not path.exists():
        return
    os.chown(path, uid, gid)
    if path.is_dir():
        for child in path.iterdir():
            _chown_tree(child, uid, gid)


def own(uid: int, gid: int) -> None:
    """Give the filesystem the same shape the systemd unit enforces.

    Code, identity and rules: root, read-only. State: `assistant`. The process
    that runs the model is not root, so no tool call and no injected
    instruction can rewrite what the assistant is or what it runs next boot --
    even if assistant/policy.py were bypassed entirely.
    """
    _walk(ROOT, 0, 0, dir_mode=0o555, file_mode=0o444)
    for rel in OPEN_DIRS:
        directory = ROOT / rel
        if directory.is_dir():
            os.chown(directory, uid, gid)
            os.chmod(directory, 0o755)
    for rel in WRITABLE:
        directory = ROOT / rel
        if directory.exists():
            _walk(directory, uid, gid, dir_mode=0o750, file_mode=0o640)
    HOME.mkdir(parents=True, exist_ok=True)
    os.chmod(HOME, 0o700)
    # Anything root left behind here -- an `npm` or a `gws auth login` run
    # through `docker exec` without dropping to this account first -- makes the
    # next start fail with EACCES somewhere unhelpful. Heal it rather than
    # explain it.
    _chown_tree(HOME, uid, gid)


# -- running ---------------------------------------------------------------


def _demote(uid: int, gid: int):
    def apply() -> None:
        os.setgid(gid)
        os.initgroups(USER, gid)
        os.setuid(uid)
        os.umask(0o027)
    return apply


def spawn(argv: list[str], env: dict[str, str], uid: int, gid: int) -> subprocess.Popen:
    return subprocess.Popen(
        argv,
        cwd=str(ROOT),
        env=env,
        preexec_fn=_demote(uid, gid),  # noqa: PLW1509 - single-threaded parent
        stdin=subprocess.DEVNULL,
    )


def install_claude_mem(env: dict[str, str], uid: int, gid: int) -> None:
    """One-shot, on the first boot of a fresh volume.

    claude-mem is what .claude/settings.json enables; installing it writes the
    plugin and its store under HOME, which lives on /data. A failure here is
    logged and survived: the wiki and the raw log are the memory tiers ROT.md
    calls authoritative, and both work without it.
    """
    marker = HOME / ".claude-mem-installed"
    if marker.exists():
        return
    log("installing claude-mem (first boot on this volume)")
    try:
        result = subprocess.run(
            ["claude-mem", "install"],
            cwd=str(ROOT), env=env, preexec_fn=_demote(uid, gid),
            stdin=subprocess.DEVNULL, timeout=600,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log(f"claude-mem install failed: {exc} -- continuing without it")
        return
    if result.returncode != 0:
        log(f"claude-mem install exited {result.returncode} -- continuing without it")
        return
    marker.touch()
    os.chown(marker, uid, gid)


def _next_distill(hour: int, after: datetime) -> datetime:
    when = after.replace(hour=hour, minute=30, second=0, microsecond=0)
    return when if when > after else when + timedelta(days=1)


def supervise(env: dict[str, str], uid: int, gid: int, distill_hour: int | None) -> int:
    """Restart the assistant if it dies; run the distiller once a night.

    The systemd deploy gets both from the unit and the timer. In a container
    there is one process, so this loop is the unit: Restart=always for the
    assistant, OnCalendar for scripts/distill.py.
    """
    argv = [sys.executable, "-m", "assistant.main"]
    child = spawn(argv, env, uid, gid)
    log(f"assistant started, pid {child.pid}")

    distiller: subprocess.Popen | None = None
    due = _next_distill(distill_hour, datetime.now()) if distill_hour is not None else None
    if due is not None:
        log(f"nightly distillation at {due:%Y-%m-%d %H:%M}")

    while not _stopping:
        time.sleep(2)
        if child.poll() is not None:
            log(f"assistant exited with {child.returncode}, restarting in 5s")
            time.sleep(5)
            if _stopping:
                break
            child = spawn(argv, env, uid, gid)
            log(f"assistant started, pid {child.pid}")
        if distiller is not None and distiller.poll() is not None:
            log(f"distillation finished with {distiller.returncode}")
            distiller = None
        if due is not None and datetime.now() >= due:
            due = _next_distill(distill_hour, datetime.now())
            if distiller is None:
                log("running scripts/distill.py")
                distiller = spawn(
                    [sys.executable, "scripts/distill.py"], env, uid, gid
                )

    for process in (distiller, child):
        if process is None or process.poll() is not None:
            continue
        process.terminate()
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()
    return 0


def main() -> int:
    global _stopping

    def stop(signum, _frame) -> None:
        global _stopping
        log(f"signal {signum}, shutting down")
        _stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    if os.geteuid() != 0:
        raise SystemExit("entrypoint must start as root: it drops privileges itself")

    env = build_env()
    options: dict = {}
    if OPTIONS.is_file():
        options = json.loads(OPTIONS.read_text())
    account = pwd.getpwnam(USER)

    seed()
    own(account.pw_uid, account.pw_gid)
    install_claude_mem(env, account.pw_uid, account.pw_gid)

    hour = options.get("distill_hour")
    distill_hour = int(hour) if isinstance(hour, int) and 0 <= hour <= 23 else None
    if distill_hour is None:
        log("nightly distillation disabled")

    log(f"root={ROOT} user={USER} model={env.get('ASSISTANT_MODEL', 'default')}")
    return supervise(env, account.pw_uid, account.pw_gid, distill_hour)


if __name__ == "__main__":
    sys.exit(main())
