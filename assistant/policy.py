"""Enforcement layer: what the assistant is allowed to touch.

This module exists because everything above it is advisory. Instructions in
IDENTITY.md and ROT.md shape behaviour but do not constrain it: text arriving
from a web page, a file, or a tool result can argue the model out of any rule
stated in prose. The checks here are the ones that hold, because they run in
Python before the tool call happens and never consult the model.

Wired in as a PreToolUse hook rather than a can_use_tool callback. That is
deliberate. The SDK auto-approves any tool named in `allowed_tools`, and any
allow rule in a settings file, *before* can_use_tool is consulted -- the SDK
warns about this via CanUseToolShadowedWarning. A PreToolUse hook is not
shadowed, so it is the only in-process gate that sees every call.

The policy is default-deny: a tool this module does not recognise is refused.
Adding a capability must be a deliberate edit here, not an oversight.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import os
import socket
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

log = logging.getLogger(__name__)

# Where each tool keeps the thing it is about to touch.
_PATH_ARGS = {
    "Read": "file_path",
    "Write": "file_path",
    "Edit": "file_path",
    "NotebookEdit": "notebook_path",
    "Glob": "path",
    "Grep": "path",
}

# Readable, but never writable: these define what the assistant is. If a tool
# call could rewrite them, a single injected instruction would be able to
# redefine the assistant's rules, widen this very policy, or plant code that
# runs as root on the next deploy.
# scripts/proposals/ and not scripts/: scripts/ holds distill.py, which the
# systemd timer runs every night. Being able to write there would mean a
# successful injection rewrites code that then starts on its own -- the same
# persistence the routine constraint denies. The model proposes in proposals/,
# the owner reads and moves.
# .claude/skills and not .claude/: settings.json holds the enabled plugins and
# the permission rules the SDK reads at connect time, so a write there is a
# write to the assistant's own configuration. The skills folder underneath it
# is method, which the model is the one who notices needs writing down.
_WRITE_ROOTS = (
    "memory/wiki", "routines", "scripts/proposals", ".claude/skills",
)

# A routine is a prompt that starts on its own: being able to create one
# already active would mean obtaining recurring execution, the persistence
# everything else in this module exists to deny. The model may propose them,
# the owner activates them with a command that goes through the transport.
_ENABLED_TRUE = re.compile(r"^\s*enabled\s*:\s*true\s*$", re.I | re.M)

# A skill is a method the model reads and then follows, so a live one has the
# same authority over the next run as a routine's prompt does. Two rules keep
# that from becoming a way to rewrite the rules: only Markdown may be written
# under .claude/skills/ (a bundled script would be code the model authored),
# and a skill named in ASSISTANT_SKILLS is frozen -- The owner takes it out of the
# list before the model may touch it. What makes a written skill harmless
# until then is that it is not in the list at all: the SDK never shows it to
# the model and check() below refuses to invoke it.
_SKILL_ROOT = ".claude/skills"

# Where the Skill tool puts the name of the skill being invoked. Checked in
# order; a call carrying none of them is refused rather than guessed at.
_SKILL_NAME_ARGS = ("skill", "name", "command")

# Never readable at all. Reading is not harmless: anything the model reads can
# be repeated into a Telegram message or smuggled into a URL.
_SECRET_NAMES = {".env", ".env.bak", ".env.example.local", "session_id"}
_SECRET_SUFFIXES = (".pem", ".key", ".p12", ".pfx")
_SECRET_DIRS = ("state", ".git")

_READ_ONLY_TOOLS = {"Read", "Glob", "Grep"}
_WRITE_TOOLS = {"Write", "Edit", "NotebookEdit"}
_FREE_TOOLS = {"TodoWrite", "WebSearch"}
# MCP tools take their prefix from how the plugin is mounted: claude-mem
# exposes mcp__plugin_claude-mem_mcp-search__search under Claude Code and
# mcp__mcp-search__search elsewhere. Pinning the exact name here had silenced
# memory search with nothing to show for it -- the model saw the tool denied
# and carried on. Match the server and the operation, not the whole string.
_MEMORY_TOOL = re.compile(
    # \Z, not $: Python's `$` also matches before a trailing newline, so a
    # name ending in "\n" would satisfy an anchored pattern.
    r"\Amcp__[\w-]*mcp-search__(search|get_observations|timeline)\Z"
)

# Google Workspace, through the in-process tools in assistant/gws.py. That
# module already refuses any method outside its own table, so this is the
# second of two locks rather than the only one: the names are pinned here so
# that adding a tool to gws.py is not enough on its own to make it callable.
#
# Seven of these write. Three go to the calendar, and only because the owner granted
# the scope and asked: create (2026-08-19), update and move (2026-08-20). What
# keeps them inside their own calendar lives in gws.py -- bodies built from
# named fields so no attendee list can reach Google, sendUpdates=none, and the
# two edits refusing events that have guests or repeat. Deleting an event
# stays out: an edit can be put back from what the tool reports, a deletion
# cannot. Four go to Drive, and all four only inside the folders
# DRIVE_WRITE_FOLDERS names: drive_create_doc (2026-08-20) makes a new Doc in
# one of them, drive_append_doc (2026-08-21) adds to the end of one that is
# already in one of them, and drive_replace_text and drive_rewrite_doc
# (2026-09-01) change what such a Doc already says -- one exact string at a
# time, or the whole body at once. None of the four can do more than that --
# no files.update, no files.delete, no permissions call anywhere in gws.py, so
# a file cannot be deleted, renamed, moved or shared by a sentence that
# arrives in a chat. What the last two can do is destroy text the owner wrote, and
# that is the point of them: the limits are in gws.py -- the folder scope,
# ranges built from the document rather than from arguments, a refusal when a
# rewrite would flatten a table or an image, and every write pinned to the
# revision it read -- plus Google's own version history, which keeps the
# replaced text. Mail stays read-only, and rule 5 holds:
# the model drafts, the owner sends. drive_read_file (2026-08-20) returns a file's
# contents rather than its metadata, and is a read like the rest: gws.py puts
# the download in a temporary directory it owns and deletes, so the model
# never names a path on disk. Which files the Drive tools may reach at all
# is a narrower question than which tools exist, and gws.py answers that
# too: DRIVE_ALLOWED_FOLDERS confines them to a few folder trees.
_GWS_TOOL = re.compile(
    r"\Amcp__gws__("
    r"calendar_calendars|calendar_events|calendar_event"
    r"|calendar_create_event|calendar_update_event|calendar_move_event"
    r"|gmail_search|gmail_message"
    r"|drive_search|drive_file|drive_read_file"
    r"|drive_create_doc|drive_append_doc|drive_replace_text|drive_rewrite_doc"
    r")\Z"
)


# The scraper worker, through the in-process tools in assistant/scrape.py.
# Pinned here for the same reason the gws names are: registering a tool in
# scrape.py is not enough to make it callable, so a tool added there by mistake
# -- or by an edit nobody reviewed -- is denied until it is also named here.
#
# All three read. There is no write anywhere in the worker: scraper/safety.py
# refuses any method but GET and HEAD before a socket is opened, which is what
# makes "buy nothing, add nothing to a cart, fill in no form" a property of the
# code rather than a sentence in a skill the model reads.
#
# What is genuinely new here, and why check_host still applies: the worker is a
# fetcher that lives inside the owner's network. Without the extra check in
# assistant/agent.py, routing a fetch through it would hide the target from the
# SSRF gate entirely -- the gate would see only the worker's own loopback
# address and wave it through, while the worker happily opened 169.254.169.254
# or the router's admin page. The target url is checked here, and again inside
# the worker, because routines reach the worker without passing through this
# module at all.
_SCRAPE_TOOL = re.compile(
    r"\Amcp__scrape__(page_read|page_price|page_sources)\Z"
)


# Intervals.icu, through the in-process tools in assistant/intervals.py: the
# cycling coach's data (2026-10-19). Pinned for the same reason as the two
# above. Eight read. One writes: coach_plan_workout (2026-10-20) puts a planned
# workout on the Intervals calendar, from where Intervals pushes it to Garmin
# Connect and the head unit. What keeps it narrow is in intervals.py -- a body built
# from four named fields, today to 28 days ahead, a rewrite only of events
# carrying its own external_id tag, no delete at all. What keeps it the owner's
# decision is here, in code, because it reaches their head unit: the write is
# refused in a routine turn, and a confirm is refused unless the very same
# workout was previewed in an earlier turn -- so between the preview and the
# write there is always one message of theirs.
# The coaching protocol the coach tools answer to. A coach_* call is refused
# until a file under here has been Read in the same turn (Policy.begin_turn).
_SECTION_11 = ".claude/skills/coach/references/section-11"

_COACH_TOOL = re.compile(
    r"\Amcp__coach__("
    r"coach_readiness|coach_load|coach_activity|coach_sessions"
    r"|coach_week|coach_history|coach_power_curve|coach_thresholds"
    r"|coach_plan_workout|coach_report"
    r")\Z"
)
_COACH_WRITE = "mcp__coach__coach_plan_workout"
# Files the weekly report for the transport to mail to MAIL_TO. Routine turns
# only: in chat the answer is the reply itself, and a report filed there would
# sit on the desk until a routine picked it up.
_COACH_REPORT = "mcp__coach__coach_report"


@dataclass(frozen=True)
class Decision:
    allow: bool
    reason: str


class Policy:
    def __init__(self, root: Path, web_fetch_domains: frozenset[str],
                 skills: frozenset[str] | tuple[str, ...] = ()) -> None:
        self._root = root.resolve()
        self._domains = web_fetch_domains
        # The same list the SDK is given. It filters the listing the model
        # sees, which is a context filter and not a gate -- the model can name
        # a skill it was never shown. This copy is the gate.
        self._skills = frozenset(skills)
        self._audit = root / "state" / "audit.log"
        self._section11_read = False
        self._turn = 0
        self._routine = False
        # Workout previews, by content, with the turn each was shown in.
        self._previews: dict[str, int] = {}

    def begin_turn(self, routine: bool = False) -> None:
        """A new turn: the coach must read Section 11 again before its tools.

        A sentence in the coach skill asks for this; the skill is read once
        and then summarised away when the context compacts, so the rule would
        fade exactly when the session is longest. Per turn, in code, it holds.

        `routine` marks a turn nobody is watching. The calendar write is
        refused in one: a routine's prompt is text the model may have written.
        """
        self._section11_read = False
        self._turn += 1
        self._routine = routine

    def _note_section11(self, tool_input: dict) -> None:
        path = self._resolve(str(tool_input.get("file_path") or ""))
        if path is None:
            return
        try:
            rel = path.relative_to(self._root).as_posix()
        except ValueError:
            return
        if rel.startswith(_SECTION_11 + "/"):
            self._section11_read = True

    # -- path handling -----------------------------------------------------

    def _resolve(self, raw: str) -> Path | None:
        """Canonicalise a tool's path argument, or None if it is unusable.

        os.path.realpath is what makes traversal and symlink escapes fail:
        '../../.ssh/id_rsa', a symlink inside memory/ pointing at /etc, and
        '~/.env' all collapse to an absolute real path before comparison.
        """
        try:
            expanded = Path(os.path.expanduser(raw))
            if not expanded.is_absolute():
                expanded = self._root / expanded
            return Path(os.path.realpath(expanded))
        except (OSError, ValueError, RuntimeError):
            return None

    def _inside_root(self, path: Path) -> bool:
        return path == self._root or self._root in path.parents

    def _is_secret(self, path: Path) -> bool:
        if path.name in _SECRET_NAMES or path.name.startswith(".env"):
            return True
        if path.suffix in _SECRET_SUFFIXES:
            return True
        try:
            parts = path.relative_to(self._root).parts
        except ValueError:
            return True
        return bool(parts) and parts[0] in _SECRET_DIRS

    def _writable(self, path: Path) -> bool:
        try:
            rel = path.relative_to(self._root).as_posix()
        except ValueError:
            return False
        return any(rel == r or rel.startswith(r + "/") for r in _WRITE_ROOTS)

    # -- the decision ------------------------------------------------------

    def check(self, tool: str, tool_input: dict) -> Decision:
        if tool in _FREE_TOOLS or _MEMORY_TOOL.match(tool):
            return Decision(True, "no filesystem or network target")

        if tool.startswith("mcp__gws__"):
            if _GWS_TOOL.match(tool):
                return Decision(True, "Google Workspace, reads and the four writes")
            return Decision(
                False,
                "the Google writes permitted are creating, updating and moving "
                "a calendar event, and creating a new Doc in the one folder "
                "opened for writing -- deleting an event, answering an "
                "invitation, sending mail, changing or sharing a file: all "
                "outward-facing or irreversible, so draft it for the owner instead",
            )

        if tool.startswith("mcp__scrape__"):
            if _SCRAPE_TOOL.match(tool):
                return self._check_scrape(tool_input)
            return Decision(
                False,
                "the scraper worker reads pages and does nothing else -- there "
                "is no tool here that posts a form, adds to a cart, logs in or "
                "buys anything, and there is not going to be one",
            )

        if tool.startswith("mcp__coach__"):
            if _COACH_TOOL.match(tool):
                if not self._section11_read:
                    return Decision(
                        False,
                        "read the Section 11 parts this job needs first "
                        f"({_SECTION_11}/SECTION_11.md, map in the coach "
                        "skill), in this turn -- then call the coach tools",
                    )
                if tool == _COACH_WRITE:
                    return self._check_plan(tool_input)
                if tool == _COACH_REPORT:
                    if not self._routine:
                        return Decision(False, "coach_report is for the weekly routine "
                                               "only -- in chat, answer in the reply")
                    return Decision(True, "weekly report, filed for the transport")
                return Decision(True, "Intervals.icu, read")
            return Decision(
                False,
                "the coach tools read Intervals.icu and plan one workout at a "
                "time with coach_plan_workout -- nothing else writes to the "
                "training calendar, and nothing deletes from it",
            )

        if tool == "Skill":
            return self._check_skill(tool_input)

        if tool == "WebFetch":
            return self._check_web_fetch(tool_input)

        if tool in _PATH_ARGS:
            decision = self._check_path(tool, tool_input)
            if decision.allow and tool == "Read":
                self._note_section11(tool_input)
            return decision

        # Default deny. Bash lands here, and so does any tool added to the
        # model's toolset by a future SDK version that this policy predates.
        return Decision(False, f"tool '{tool}' is not permitted")

    async def decide(self, tool: str, tool_input: dict) -> Decision:
        """check(), the address check, and the audit row: the whole gate.

        Both brains call this and nothing else -- the SDK's PreToolUse hook in
        assistant/agent.py and the tool loop in assistant/openrouter.py -- so
        a call is judged the same way whichever model is behind it.
        """
        decision = self.check(tool, tool_input)
        # The address check applies to every tool that opens a URL, not just
        # WebFetch. The scraper worker is the reason this matters more than it
        # looks: it is a fetcher sitting inside the owner's own network, so a
        # scrape call that skipped this check would hand the model a proxy onto
        # the LAN and onto any metadata endpoint -- the gate would have seen
        # only the worker's loopback address and approved it. The worker
        # repeats the check itself, because routines never pass through here.
        if decision.allow and tool_input.get("url") and (
            tool == "WebFetch" or tool.startswith("mcp__scrape__")
        ):
            # DNS resolution blocks, so it goes off the event loop:
            # Telegram's long poll must not stall on a slow lookup.
            decision = await asyncio.to_thread(
                self.check_host, tool_input.get("url", "")
            )
        self.record(tool, tool_input, decision)
        if not decision.allow:
            log.warning("blocked %s: %s", tool, decision.reason)
        return decision

    def readable(self, path: Path) -> bool:
        """May a file turn up in a Glob or Grep that walks the tree?

        The gate judges the directory a search starts from; this judges every
        file the walk then reaches, so a Grep over the root does not read
        .env on its way past.
        """
        path = Path(os.path.realpath(path))
        return self._inside_root(path) and not self._is_secret(path)

    def _check_plan(self, tool_input: dict) -> Decision:
        """Preview in one turn, write in a later one, never in a routine."""
        if self._routine:
            return Decision(
                False,
                "a routine cannot write to the training calendar -- put the "
                "workout in the routine's output and let the owner ask for it",
            )
        key = _workout_key(tool_input)
        if not _confirmed(tool_input):
            self._previews[key] = self._turn
            return Decision(True, "Intervals.icu, workout preview (writes nothing)")
        shown = self._previews.get(key)
        if shown is None:
            return Decision(
                False,
                "no preview of exactly this workout -- call without confirm, "
                "show the owner what it would write, and confirm after their yes",
            )
        if shown >= self._turn:
            return Decision(
                False,
                "previewed in this same turn -- The owner has not answered yet. Show "
                "them the workout and wait for their yes",
            )
        del self._previews[key]
        return Decision(True, "Intervals.icu, workout write after a preview")

    def _check_skill(self, tool_input: dict) -> Decision:
        """Only a skill the owner put in ASSISTANT_SKILLS may be invoked.

        Two things arrive at this gate. One is the model choosing a method for
        the job in front of it, which is the point of skills. The other is a
        skill file the model wrote itself an hour ago: writing one is allowed,
        and this is what stops writing one from being the same as adopting one.
        """
        name = ""
        for key in _SKILL_NAME_ARGS:
            value = tool_input.get(key)
            if isinstance(value, str) and value.strip():
                name = value.strip()
                break
        if not name:
            return Decision(False, "no skill named")
        if name not in self._skills:
            return Decision(
                False,
                f"skill '{name}' is not live -- The owner enables a skill by "
                "adding its name to ASSISTANT_SKILLS, and until they have, "
                "writing it is all that can be done with it",
            )
        return Decision(True, f"skill '{name}' is live")

    def _check_path(self, tool: str, tool_input: dict) -> Decision:
        raw = tool_input.get(_PATH_ARGS[tool])
        if raw is None:
            # Glob and Grep default to cwd, which is root. Harmless.
            if tool in _READ_ONLY_TOOLS:
                return Decision(True, "defaults to project root")
            return Decision(False, "no path given")
        if not isinstance(raw, str):
            return Decision(False, "path is not a string")

        path = self._resolve(raw)
        if path is None:
            return Decision(False, "path could not be resolved")
        if not self._inside_root(path):
            return Decision(False, "path is outside the assistant's directory")
        if self._is_secret(path):
            return Decision(False, "path holds credentials or internal state")
        if tool in _WRITE_TOOLS and not self._writable(path):
            return Decision(
                False,
                "writes are confined to memory/wiki/, routines/ and "
                "scripts/proposals/ -- the assistant may not modify its own "
                "instructions, code, or configuration",
            )
        if tool in _WRITE_TOOLS and self._in_routines(path):
            # Two separate holes, both leading to recurring execution.
            #
            # First: a routine born enabled. Second, less obvious: editing the
            # *body* of a routine that is already enabled. The scan below only
            # sees the text being written, so an Edit that replaces prose in a
            # live routine carries no 'enabled: true' and would sail through --
            # and the next tick runs whatever the new body says. An enabled
            # routine is therefore frozen: the owner runs /disable, the model
            # rewrites it, the owner reads it and runs /enable. set_enabled() writes
            # the file from Python and never passes through here, so the
            # commands themselves keep working.
            if self._is_enabled_on_disk(path):
                return Decision(
                    False,
                    "that routine is enabled -- an enabled routine cannot be "
                    "modified. Ask the owner to /disable it first, then propose the "
                    "change",
                )
            written = " ".join(
                str(tool_input.get(key, ""))
                for key in ("content", "new_string")
            )
            if _ENABLED_TRUE.search(written):
                return Decision(
                    False,
                    "a routine cannot be written already enabled -- write it "
                    "with 'enabled: false' and tell the owner to activate it with "
                    "/enable",
                )
        if tool in _WRITE_TOOLS:
            skill = self._skill_slug(path)
            if skill is not None:
                if path.suffix.lower() != ".md":
                    return Decision(
                        False,
                        "a skill is Markdown -- a script bundled beside it "
                        "would be code the model wrote, and there is no shell "
                        "here to run it anyway",
                    )
                if skill in self._skills:
                    return Decision(
                        False,
                        f"skill '{skill}' is live -- a live skill cannot be "
                        "modified. Ask the owner to take it out of "
                        "ASSISTANT_SKILLS first, then propose the change",
                    )
        return Decision(True, "within permitted paths")

    def _is_enabled_on_disk(self, path: Path) -> bool:
        """Is the routine currently live? Unreadable counts as live.

        Failing closed matters here: if the file cannot be read, refusing the
        write costs a round trip, while allowing it could hand a live prompt
        to whatever asked for the change.
        """
        try:
            return bool(_ENABLED_TRUE.search(path.read_text(encoding="utf-8")))
        except FileNotFoundError:
            return False  # a new routine is not yet anything
        except OSError:
            return True

    def _skill_slug(self, path: Path) -> str | None:
        """The skill a path belongs to, or None if it is not under skills/.

        The slug is the directory directly beneath .claude/skills/, which is
        the name the CLI discovers a skill by and therefore the name that
        appears in ASSISTANT_SKILLS. A file dropped straight into
        .claude/skills/ belongs to no skill and has no slug to freeze, so it
        comes back as the empty string and never matches the live set.
        """
        try:
            rel = path.relative_to(self._root / _SKILL_ROOT).parts
        except ValueError:
            return None
        return rel[0] if len(rel) > 1 else ""

    def _in_routines(self, path: Path) -> bool:
        try:
            return path.relative_to(self._root).parts[:1] == ("routines",)
        except (ValueError, IndexError):
            return False

    def _check_scrape(self, tool_input: dict) -> Decision:
        """The syntax half for a scrape call. check_host does the rest.

        page_sources takes a domain rather than a url and reaches no network of
        its own -- it reads the worker's own recipe store -- so there is
        nothing here to check beyond the tool being pinned.
        """
        if "url" not in tool_input:
            return Decision(True, "no url in this call")
        url = tool_input.get("url")
        if not isinstance(url, str):
            return Decision(False, "url is not a string")
        if len(url) > 2048:
            return Decision(False, "url is implausibly long")
        try:
            parsed = urlparse(url)
        except ValueError:
            return Decision(False, "url could not be parsed")
        if parsed.scheme != "https":
            return Decision(False, "only https is fetched")
        host = (parsed.hostname or "").lower()
        if not host:
            return Decision(False, "url has no host")
        # WEB_FETCH_ALLOWED_DOMAINS narrows the scraper too. One allowlist for
        # every way out of this process, or the narrower one is theatre.
        if self._domains:
            if not any(host == d or host.endswith("." + d) for d in self._domains):
                return Decision(False, f"domain '{host}' is not allowlisted")
            return Decision(True, f"domain '{host}' is allowlisted")
        return Decision(True, "open web, through the scraper worker")

    def _check_web_fetch(self, tool_input: dict) -> Decision:
        """Syntactic checks. The network check lives in check_host()."""
        url = tool_input.get("url")
        if not isinstance(url, str):
            return Decision(False, "no url given")
        if len(url) > 2048:
            return Decision(False, "url is implausibly long")
        try:
            parsed = urlparse(url)
        except ValueError:
            return Decision(False, "url could not be parsed")
        # https only: over http anyone in the middle can rewrite the page
        # the model then reads as trustworthy data.
        if parsed.scheme != "https":
            return Decision(False, "only https is permitted")
        host = (parsed.hostname or "").lower()
        if not host:
            return Decision(False, "url has no host")

        if self._domains:
            allowed = any(
                host == d or host.endswith("." + d) for d in self._domains
            )
            if not allowed:
                return Decision(False, f"domain '{host}' is not allowlisted")
            return Decision(True, f"domain '{host}' is allowlisted")

        # No allowlist: the web is open. The network check still applies --
        # not a question of trusting the site, but of where the name points.
        return Decision(True, "open web")

    def check_host(self, url: str) -> Decision:
        """Block addresses that do not belong to the internet.

        A public name can resolve to 127.0.0.1, to a 10.x on the provider's
        network, or to 169.254.169.254 -- the metadata endpoint that on a VPS
        hands back the instance credentials. The model must not be talkable
        into proxying for that: it is textbook SSRF, and "ask an agent to open
        this link" is exactly how you get there.

        Synchronous on purpose: DNS resolution blocks, so the gate calls it in
        a thread.
        """
        host = (urlparse(url).hostname or "").lower()
        if not host:
            return Decision(False, "url has no host")
        try:
            infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
        except socket.gaierror:
            return Decision(False, f"host '{host}' does not resolve")
        except Exception:
            return Decision(False, f"host '{host}' could not be resolved")

        for info in infos:
            address = info[4][0]
            try:
                ip = ipaddress.ip_address(address.split("%")[0])
            except ValueError:
                return Decision(False, f"unparseable address for '{host}'")
            # Every address must be public: if even one isn't, the name is
            # untrustworthy (DNS rebinding starts here).
            if (ip.is_private or ip.is_loopback or ip.is_link_local
                    or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
                return Decision(
                    False,
                    f"host '{host}' resolves to a non-public address ({ip})",
                )
        return Decision(True, f"host '{host}' resolves publicly")

    # -- audit -------------------------------------------------------------

    def record(self, tool: str, tool_input: dict, decision: Decision) -> None:
        """Append every decision to state/audit.log.

        Denials are the interesting rows: a run of them against paths the
        assistant has no business touching is what an injection attempt looks
        like from the outside.
        """
        target = ""
        for key in ("file_path", "notebook_path", "path", "url", "query"):
            value = tool_input.get(key)
            if isinstance(value, str):
                target = value[:200]
                break
        verdict = "ALLOW" if decision.allow else "DENY "
        line = f"{_now()} {verdict} {tool} {target} :: {decision.reason}\n"
        try:
            self._audit.parent.mkdir(parents=True, exist_ok=True)
            with self._audit.open("a", encoding="utf-8") as fh:
                fh.write(line)
        except OSError:
            log.exception("could not write audit log")


def _confirmed(tool_input: dict) -> bool:
    """The same reading of `confirm` as intervals.py: only a real yes."""
    value = tool_input.get("confirm")
    return value is True or (isinstance(value, str) and value.strip().lower() == "true")


def _workout_key(tool_input: dict) -> str:
    """What makes two workout calls the same workout, normalised the way
    intervals.workout_body normalises it, so a reflowed space is not a new one."""
    import hashlib
    import json

    fields = {
        "date": str(tool_input.get("date") or "").strip(),
        "name": " ".join(str(tool_input.get("name") or "").split()),
        "type": str(tool_input.get("type") or "Ride").strip(),
        "description": str(tool_input.get("description") or "").replace("\r\n", "\n").strip(),
        "event_id": str(tool_input.get("event_id") or "").strip(),
    }
    return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()


def _now() -> str:
    from datetime import datetime

    return datetime.now().isoformat(timespec="seconds")
