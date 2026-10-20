"""Scheduled routines: prompts that start on their own.

A routine is a markdown file in `routines/`, with frontmatter for the schedule
and the body as the prompt to run. Same principle as the rest of the project:
no database, files that stay readable and fixable by hand.

The property that holds it all up: **only the owner enables a routine**. The model
may write new ones, but always with `enabled: false`, and the policy refuses
any write to `routines/` that tries to be born already active. Without that
constraint, a single successful injection would obtain recurring execution --
exactly the persistence the rest of the policy exists to deny. Enabling goes
through a Telegram command handled in Python, which the model cannot emit.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

_FRONTMATTER = re.compile(r"^---\n(.*?)\n---\n?(.*)$", re.S)
_SLUG = re.compile(r"[^a-z0-9-]+")


@dataclass
class Routine:
    name: str
    schedule: str          # 5-field cron expression
    prompt: str
    enabled: bool = False
    channel: str = "telegram"
    # A chart set drawn in Python and attached after the model's text
    # (assistant/coach_charts.py). Empty: text only.
    charts: str = ""
    path: Path | None = None

    def next_run(self, after: datetime) -> datetime | None:
        from croniter import croniter

        if not croniter.is_valid(self.schedule):
            return None
        return croniter(self.schedule, after).get_next(datetime)


def slug(name: str) -> str:
    return _SLUG.sub("-", name.strip().lower()).strip("-")[:60] or "routine"


def parse(text: str, path: Path | None = None) -> Routine | None:
    match = _FRONTMATTER.match(text)
    if not match:
        return None
    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            fields[key.strip().lower()] = value.strip()
    name = fields.get("name") or (path.stem if path else "")
    schedule = fields.get("schedule", "")
    if not name or not schedule:
        return None
    return Routine(
        name=name,
        schedule=schedule,
        prompt=match.group(2).strip(),
        # Anything other than "true" counts as disabled: a mistyped field
        # must never end up as a routine that runs.
        enabled=fields.get("enabled", "").strip().lower() == "true",
        channel=fields.get("channel", "telegram").strip().lower() or "telegram",
        charts=fields.get("charts", "").strip().lower(),
        path=path,
    )


def render(routine: Routine) -> str:
    return (
        "---\n"
        f"name: {routine.name}\n"
        f"schedule: {routine.schedule}\n"
        f"enabled: {'true' if routine.enabled else 'false'}\n"
        f"channel: {routine.channel}\n"
        + (f"charts: {routine.charts}\n" if routine.charts else "")
        + "---\n\n"
        f"{routine.prompt.strip()}\n"
    )


def load_all(directory: Path) -> list[Routine]:
    routines: list[Routine] = []
    if not directory.exists():
        return routines
    for path in sorted(directory.glob("*.md")):
        try:
            routine = parse(path.read_text(encoding="utf-8"), path)
        except OSError:
            log.exception("cannot read %s", path.name)
            continue
        if routine is None:
            log.warning("malformed routine, ignored: %s", path.name)
            continue
        routines.append(routine)
    return routines


def find(directory: Path, name: str) -> Routine | None:
    wanted = slug(name)
    for routine in load_all(directory):
        if slug(routine.name) == wanted or (routine.path and routine.path.stem == wanted):
            return routine
    return None


def set_enabled(routine: Routine, enabled: bool) -> None:
    """Rewrite the file with the new state. Only the transport calls this."""
    if routine.path is None:
        raise ValueError("routine has no file")
    routine.enabled = enabled
    routine.path.write_text(render(routine), encoding="utf-8")


def describe(routines: list[Routine], now: datetime) -> str:
    if not routines:
        return ("No routines. Ask me for one: tell me what to do and when, "
                "I write it, then you enable it with /enable.")
    lines = []
    for r in sorted(routines, key=lambda r: r.name):
        state = "on" if r.enabled else "off"
        nxt = r.next_run(now) if r.enabled else None
        when = f", next {nxt:%d/%m %H:%M}" if nxt else ""
        valid = "" if r.next_run(now) or not r.enabled else "  <- invalid schedule"
        lines.append(f"• `{slug(r.name)}` — {r.schedule} [{state}{when}]{valid}")
    return "\n".join(lines)


def due(routines: list[Routine], previous: datetime, now: datetime) -> list[Routine]:
    """Routines to run in the window (previous, now].

    Comparing windows instead of an exact timestamp is what makes the tick
    forgiving: if the process falls a few seconds behind, or the tick slips,
    the run is not skipped.
    """
    ready = []
    for routine in routines:
        if not routine.enabled:
            continue
        nxt = routine.next_run(previous)
        if nxt is not None and previous < nxt <= now:
            ready.append(routine)
    return ready
