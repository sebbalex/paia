"""`/selftest`: is every connector this install has actually working?

Run by the transport, never by the model: a broken model provider must not
hide the rest, and a check costs no tokens it does not have to. Every check is
a read. Nothing is created at Google, nothing at Intervals, nothing is mailed
-- except `/selftest mail`, which sends one message down the routine path to
the fixed address in MAIL_TO, because "the login works" and "the mail
arrives" are different facts and only a send proves the second.

Each check answers one of four states:

  ok    it answered
  fail  it is configured and broken -- the line says why
  warn  it works, with a caveat the owner should know
  off   not configured on this install, which is a choice, not a fault

The output is the owner's to forward to whoever helps them, so it carries no
token, password or full mail address: errors are cut to one line and every
address is masked.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Awaitable, Callable

import httpx

from . import routines as routines_module

log = logging.getLogger(__name__)

CHECK_TIMEOUT = 20      # seconds, per check
MODEL_TIMEOUT = 90      # the Claude CLI takes a while to start

_EMAIL = re.compile(r"([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")


@dataclass(frozen=True)
class Result:
    name: str
    state: str          # ok | fail | warn | off
    detail: str = ""


def mask(text: str) -> str:
    """`friend@example.com` -> `f***@example.com`."""
    return _EMAIL.sub(lambda m: f"{m.group(1)}***@{m.group(2)}", text)


def _clean(text: str, limit: int = 220) -> str:
    line = " ".join(str(text).split())
    line = mask(line)
    return line if len(line) <= limit else line[: limit - 1] + "…"


def _why(exc: BaseException) -> str:
    message = str(exc).strip()
    # gws errors are worded for the model ("tell the owner to..."); the owner
    # reading this needs the short form.
    if "invalid_grant" in message:
        return "Google sign-in expired or revoked: `/google connect`"
    if "not signed in" in message and "/google" in message:
        return "Google not signed in: `/google`"
    return _clean(message) if message else type(exc).__name__


# -- the individual checks ----------------------------------------------------
#
# Each returns a Result or a list of them, and may raise: the runner turns an
# exception into a `fail` with its message, and a timeout into one that says so.


async def check_model(cfg, probe: Callable[[], Awaitable[str]] | None = None) -> Result:
    started = time.monotonic()
    if probe is not None:
        note = await probe()
    elif cfg.backend == "openrouter":
        note = await _probe_openrouter(cfg)
    else:
        note = await _probe_claude(cfg)
    took = time.monotonic() - started
    return Result("model", "ok", f"{cfg.model} answered in {took:.1f}s" + (f", {note}" if note else ""))


async def _probe_openrouter(cfg) -> str:
    async with httpx.AsyncClient(timeout=CHECK_TIMEOUT) as http:
        response = await http.get(
            f"{cfg.openrouter_base_url}/key",
            headers={"Authorization": f"Bearer {cfg.openrouter_api_key}"},
        )
    if response.status_code in (401, 403):
        raise RuntimeError("OpenRouter refused the API key")
    if response.status_code >= 400:
        raise RuntimeError(f"OpenRouter answered HTTP {response.status_code}")
    data = (response.json() or {}).get("data") or {}
    limit, usage = data.get("limit"), data.get("usage")
    if isinstance(limit, (int, float)) and isinstance(usage, (int, float)):
        return f"credit used {usage:.2f} of {limit:.2f}"
    return ""


async def _probe_claude(cfg) -> str:
    """One tiny turn through the same CLI the brain uses, outside its
    session: no settings (so no claude-mem hooks recording a test), no tools,
    one turn."""
    from claude_agent_sdk import (
        AssistantMessage, ClaudeAgentOptions, ResultMessage, TextBlock, query,
    )

    options = ClaudeAgentOptions(
        cwd=str(cfg.state_dir),
        model=cfg.model,
        setting_sources=[],
        tools=[],
        max_turns=1,
        env={"CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"},
    )
    text: list[str] = []
    async for message in query(prompt="Reply with the single word OK.", options=options):
        if isinstance(message, AssistantMessage):
            text += [b.text for b in message.content if isinstance(b, TextBlock)]
        elif isinstance(message, ResultMessage) and getattr(message, "is_error", False):
            status = getattr(message, "api_error_status", None)
            said = " ".join(text).strip()
            raise RuntimeError(said or f"API error{f' {status}' if status else ''}")
    return ""


def check_google(cfg, google) -> Result:
    if not cfg.gws_bin:
        return Result("google", "off", "GWS_BIN is empty")
    if google is None or google.missing is None:
        hint = " `/google connect` to sign in." if google is not None and google.configured else ""
        return Result("google", "warn", "no sign-in from the chat; calendar, gmail and drive "
                      "below say whether an older terminal login works." + hint)
    who = google.email or "an unnamed account"
    if google.missing:
        return Result("google", "warn", f"connected as {who}, not granted: "
                      + ", ".join(google.missing) + ". `/google connect` to grant them")
    return Result("google", "ok", f"connected as {who}")


async def check_calendar(cfg, gws) -> Result:
    if gws is None:
        return Result("calendar", "off", "no gws")
    payload = await gws.call(("calendar", "calendarList", "list"), {})
    count = len((payload or {}).get("items") or [])
    return Result("calendar", "ok", f"{count} calendars visible")


async def check_gmail(cfg, gws) -> Result:
    if gws is None:
        return Result("gmail", "off", "no gws")
    await gws.call(("gmail", "users", "messages", "list"), {"userId": "me", "maxResults": 1})
    return Result("gmail", "ok", "inbox readable")


async def check_drive(cfg, gws) -> Result:
    if gws is None:
        return Result("drive", "off", "no gws")
    read = sorted(cfg.drive_folder_ids)
    write = [f for f in cfg.drive_write_folders if f not in cfg.drive_folder_ids]
    if not read and not cfg.drive_write_folders:
        await gws.call(("drive", "files", "list"), {"pageSize": 1, "fields": "files(id)"})
        return Result("drive", "warn", "readable, but DRIVE_ALLOWED_FOLDERS is empty: the whole Drive is open")
    bad: list[str] = []
    for folder in read + write:
        try:
            meta = await gws.call(("drive", "files", "get"),
                                  {"fileId": folder, "fields": "id,mimeType,trashed"})
        except Exception as exc:  # noqa: BLE001 - one line per folder
            bad.append(f"{folder[:8]}…: {_why(exc)}")
            continue
        if meta.get("trashed"):
            bad.append(f"{folder[:8]}…: in the trash")
        elif meta.get("mimeType") != "application/vnd.google-apps.folder":
            bad.append(f"{folder[:8]}…: not a folder")
    total = len(read) + len(write)
    reasons = {b.split(": ", 1)[1] for b in bad}
    if len(bad) == total and len(reasons) == 1:
        raise RuntimeError(reasons.pop())  # one cause, said once
    if bad:
        return Result("drive", "fail", f"{len(bad)} of {total} folders unreachable: " + "; ".join(bad))
    return Result("drive", "ok", f"{len(read)} read folders, {len(cfg.drive_write_folders)} write folders reachable")


async def check_intervals(cfg, icu) -> Result:
    if icu is None or not icu.enabled:
        return Result("intervals", "off", "no INTERVALS_API_KEY / INTERVALS_ATHLETE_ID")
    await icu.ping()
    return Result("intervals", "ok", "athlete record readable")


async def check_scraper(cfg) -> Result:
    if not cfg.scraper_url:
        return Result("scraper", "off", "no SCRAPER_URL")
    from .scrape import Scraper

    scraper = Scraper(cfg.scraper_url, cfg.scraper_token, CHECK_TIMEOUT)
    await scraper.call("GET", "/status", params={"domain": "example.com"})
    return Result("scraper", "ok", "worker answered")


async def check_mail(cfg, delivery, google) -> Result:
    route = delivery.mail_route
    to = mask(delivery.mail_recipient)
    if route is None:
        return Result("mail", "off", "no MAIL_TO: email routines go to Telegram")
    if route == "smtp":
        await asyncio.to_thread(delivery.smtp_login)
        return Result("mail", "ok", f"SMTP {cfg.smtp.host}:{cfg.smtp.port} login accepted, to {to}. "
                      "`/selftest mail` sends a real one")
    # gws: sending needs gmail.send, which only the sign-in can show.
    if google is not None and google.missing is not None:
        if "email routines" in google.missing:
            return Result("mail", "fail", "via Gmail, but the sign-in did not grant gmail.send: "
                          "`/google connect`, or set SMTP host and from")
        return Result("mail", "ok", f"via Gmail to {to}. `/selftest mail` sends a real one")
    return Result("mail", "warn", f"via Gmail to {to}, but no /google sign-in: "
                  "the send fails unless an older login has gmail.send. `/selftest mail` to try")


def check_voice(cfg, transcriber) -> Result:
    if transcriber is None or not transcriber.available:
        return Result("voice", "off", "faster-whisper not installed")
    return Result("voice", "ok", f"whisper `{cfg.whisper_model}`, loads on the first voice note")


async def check_memory(cfg) -> Result:
    if cfg.backend != "claude":
        return Result("claude-mem", "off", "not used with OpenRouter")
    settings = Path(os.environ.get("HOME", str(Path.home()))) / ".claude-mem" / "settings.json"
    try:
        values = json.loads(settings.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        values = {}
    host = values.get("CLAUDE_MEM_WORKER_HOST") or "127.0.0.1"
    port = values.get("CLAUDE_MEM_WORKER_PORT") or "37777"
    try:
        async with httpx.AsyncClient(timeout=5) as http:
            response = await http.get(f"http://{host}:{port}/api/readiness")
    except httpx.HTTPError:
        if not settings.exists():
            return Result("claude-mem", "fail", "not installed (no ~/.claude-mem): recall is off, the wiki still works")
        return Result("claude-mem", "warn", "worker not running now; it starts with the next chat session")
    if response.status_code != 200:
        return Result("claude-mem", "fail", f"worker answered HTTP {response.status_code}")
    return observer_health(settings.parent / "observer-health.json")


def observer_health(path: Path) -> Result:
    """A ready worker can still be recording nothing: the observer that
    compresses each session signs in on its own, and fails quietly when it
    cannot."""
    try:
        health = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Result("claude-mem", "ok", "worker ready")
    failures = health.get("consecutiveFailures") or 0
    if not failures:
        return Result("claude-mem", "ok", "worker ready, observer recording")
    since = health.get("failingSinceAt")
    when = datetime.fromtimestamp(since / 1000).strftime("%Y-%m-%d %H:%M") if since else "?"
    why = _clean(str(health.get("lastErrorMessage") or "unknown error"), 120)
    return Result("claude-mem", "fail",
                  f"observer failing since {when} ({failures} in a row): {why}. "
                  "Nothing new is remembered; a restart rewrites its credentials")


def check_routines(cfg, delivery) -> Result:
    files = sorted(cfg.routines_dir.glob("*.md")) if cfg.routines_dir.exists() else []
    loaded = routines_module.load_all(cfg.routines_dir)
    problems: list[str] = []
    named = {r.path.name for r in loaded if r.path}
    problems += [f"`{f.name}` malformed" for f in files if f.name not in named]
    for r in loaded:
        if r.next_run(datetime.now()) is None:
            problems.append(f"`{r.name}` bad schedule `{r.schedule}`")
        if r.enabled and not delivery.available(r.channel):
            problems.append(f"`{r.name}` wants `{r.channel}`, not available")
    on = sum(r.enabled for r in loaded)
    if problems:
        return Result("routines", "fail", "; ".join(problems))
    return Result("routines", "ok", f"{len(loaded)} parsed, {on} enabled")


def check_skills(cfg) -> Result:
    if not cfg.skills:
        return Result("skills", "off", "ASSISTANT_SKILLS is empty")
    missing = [s for s in cfg.skills if not (cfg.skills_dir / s / "SKILL.md").is_file()]
    if missing:
        return Result("skills", "fail", "no SKILL.md for " + ", ".join(f"`{s}`" for s in missing))
    return Result("skills", "ok", ", ".join(cfg.skills))


# -- running and rendering ----------------------------------------------------


async def _guard(name: str, job, timeout: int) -> list[Result]:
    try:
        if asyncio.iscoroutine(job):
            outcome = await asyncio.wait_for(job, timeout)
        else:
            outcome = job
    except asyncio.TimeoutError:
        return [Result(name, "fail", f"no answer within {timeout}s")]
    except Exception as exc:  # noqa: BLE001 - every failure is a line, not a crash
        log.warning("selftest %s failed: %s", name, type(exc).__name__)
        return [Result(name, "fail", _why(exc))]
    return outcome if isinstance(outcome, list) else [outcome]


async def run_all(cfg, delivery, *, google=None, gws=None, icu=None,
                  transcriber=None, model_probe=None, probe_model=True) -> list[Result]:
    """Every check, in parallel, each under its own timeout. `probe_model`
    False skips the one check that spends tokens (the startup run)."""
    def sync(name: str, fn, *args):
        # Synchronous checks still must not take the run down with them.
        try:
            return fn(*args)
        except Exception as exc:  # noqa: BLE001
            return Result(name, "fail", _why(exc))

    jobs = [
        ("model", check_model(cfg, model_probe) if probe_model
         else Result("model", "off", "not probed at startup"), MODEL_TIMEOUT),
        ("google", sync("google", check_google, cfg, google), 0),
        ("calendar", check_calendar(cfg, gws), CHECK_TIMEOUT),
        ("gmail", check_gmail(cfg, gws), CHECK_TIMEOUT),
        ("drive", check_drive(cfg, gws), CHECK_TIMEOUT * 2),
        ("intervals", check_intervals(cfg, icu), CHECK_TIMEOUT),
        ("scraper", check_scraper(cfg), CHECK_TIMEOUT),
        ("mail", check_mail(cfg, delivery, google), CHECK_TIMEOUT),
        ("voice", sync("voice", check_voice, cfg, transcriber), 0),
        ("claude-mem", check_memory(cfg), CHECK_TIMEOUT),
        ("routines", sync("routines", check_routines, cfg, delivery), 0),
        ("skills", sync("skills", check_skills, cfg), 0),
    ]
    groups = await asyncio.gather(*(_guard(n, j, t) for n, j, t in jobs))
    return [r for group in groups for r in group]


_MARK = {"ok": "ok  ", "fail": "FAIL", "warn": "warn", "off": "off "}


def render(results: list[Result]) -> str:
    counts = {s: sum(r.state == s for r in results) for s in _MARK}
    head = (f"Self-test: {counts['ok']} ok, {counts['fail']} failing, "
            f"{counts['warn']} warnings, {counts['off']} off.")
    lines = [f"`{_MARK[r.state]}` *{r.name}* — {r.detail}" if r.detail
             else f"`{_MARK[r.state]}` *{r.name}*" for r in results]
    return head + "\n\n" + "\n".join(lines)


def summary(results: list[Result]) -> str:
    """One line for the log at startup."""
    failing = [f"{r.name} ({r.detail[:60]})" for r in results if r.state == "fail"]
    ok = sum(r.state == "ok" for r in results)
    return f"selftest: {ok} ok, {len(failing)} failing" + (": " + "; ".join(failing) if failing else "")


async def mail_test(delivery) -> str:
    """`/selftest mail`: one real message to MAIL_TO, or the reason it failed."""
    route = delivery.mail_route
    if route is None:
        return "No mail route: set Mail to (and SMTP host + from to send through SMTP)."
    to = mask(delivery.mail_recipient)
    try:
        await asyncio.wait_for(delivery.send_test(
            "PAIA self-test",
            "If you are reading this, routines with channel: email reach you.",
        ), CHECK_TIMEOUT * 2)
    except asyncio.TimeoutError:
        return f"Mail via {route} to {to}: no answer within {CHECK_TIMEOUT * 2}s."
    except Exception as exc:  # noqa: BLE001
        return f"Mail via {route} to {to} FAILED: {_why(exc)}"
    return f"Test mail sent via {route} to {to}. Check the inbox (and spam)."
