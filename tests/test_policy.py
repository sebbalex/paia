#!/usr/bin/env python3
"""Policy tests. Run: .venv/bin/python tests/test_policy.py

No test framework on purpose -- this must stay runnable on the server with
nothing installed but the runtime dependencies.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from assistant.policy import Policy  # noqa: E402

FAILURES: list[str] = []


def check(name: str, got: bool, want: bool, reason: str = "") -> None:
    if got != want:
        verb = "allowed" if got else "denied"
        FAILURES.append(f"{name}: {verb} but should be "
                        f"{'allowed' if want else 'denied'} ({reason})")
    print(f"  {'ok  ' if got == want else 'FAIL'} {name}")


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="policy-")).resolve()
    (root / "memory" / "wiki").mkdir(parents=True)
    (root / "state").mkdir()
    (root / "context").mkdir()
    (root / ".claude").mkdir()
    (root / ".env").write_text("SECRET=1")
    (root / "ROT.md").write_text("rules")

    p = Policy(root, frozenset({"example.com"}))

    def verdict(tool, **kw):
        d = p.check(tool, kw)
        return d.allow, d.reason

    print("\nreads inside root")
    check("read wiki page", *verdict("Read", file_path="memory/wiki/a.md")[:1], want=True)
    check("read context file", *verdict("Read", file_path="context/notes.md")[:1], want=True)
    check("grep with no path", *verdict("Grep", pattern="x")[:1], want=True)

    print("\nescapes")
    check("traversal", *verdict("Read", file_path="../../etc/passwd")[:1], want=False)
    check("absolute outside", *verdict("Read", file_path="/etc/passwd")[:1], want=False)
    check("home expansion", *verdict("Read", file_path="~/.ssh/id_rsa")[:1], want=False)

    # A symlink inside the tree pointing out of it is the case a naive
    # startswith() check waves through.
    link = root / "memory" / "wiki" / "escape"
    os.symlink("/etc", link)
    check("symlink out of tree", *verdict("Read", file_path=str(link / "passwd"))[:1], want=False)

    print("\nsecrets")
    check("read .env", *verdict("Read", file_path=".env")[:1], want=False)
    check("read .env via subpath", *verdict("Read", file_path="./.env")[:1], want=False)
    check("read session id", *verdict("Read", file_path="state/session_id")[:1], want=False)
    check("read git internals", *verdict("Read", file_path=".git/config")[:1], want=False)
    check("read a key file", *verdict("Read", file_path="memory/wiki/tls.pem")[:1], want=False)

    print("\nself-modification")
    check("write ROT.md", *verdict("Write", file_path="ROT.md")[:1], want=False)
    check("write IDENTITY.md", *verdict("Write", file_path="IDENTITY.md")[:1], want=False)
    check("write own policy", *verdict("Write", file_path="assistant/policy.py")[:1], want=False)
    check("write plugin settings", *verdict("Write", file_path=".claude/settings.json")[:1], want=False)
    check("edit own config", *verdict("Edit", file_path="assistant/config.py")[:1], want=False)
    check("write raw log", *verdict("Write", file_path="memory/raw/2026-01-01.md")[:1], want=False)
    check("write wiki page", *verdict("Write", file_path="memory/wiki/x.md")[:1], want=True)

    print("\nmemory tools (the MCP prefix varies)")
    # claude-mem exposes mcp__plugin_claude-mem_mcp-search__search under Claude
    # Code and mcp__mcp-search__search elsewhere. The policy listed only the
    # second form, so memory search was denied in silence.
    check("name with plugin prefix",
          *verdict("mcp__plugin_claude-mem_mcp-search__search")[:1], want=True)
    check("name without prefix",
          *verdict("mcp__mcp-search__search")[:1], want=True)
    check("get_observations",
          *verdict("mcp__plugin_claude-mem_mcp-search__get_observations")[:1], want=True)
    check("smart_search stays out (it is code search)",
          *verdict("mcp__plugin_claude-mem_mcp-search__smart_search")[:1], want=False)
    check("another MCP server does not pass",
          *verdict("mcp__someoneelse__search")[:1], want=False)

    print("\ngoogle workspace (read-only, in-process tools)")
    # Writing to Google moves other people's days and sends mail in the owner's
    # name. It stays denied whatever an event description asks for, and a tool
    # added to assistant/gws.py is not callable until it is named here too.
    check("read events",
          *verdict("mcp__gws__calendar_events")[:1], want=True)
    check("list calendars",
          *verdict("mcp__gws__calendar_calendars")[:1], want=True)
    check("read one event",
          *verdict("mcp__gws__calendar_event")[:1], want=True)
    check("search mail",
          *verdict("mcp__gws__gmail_search")[:1], want=True)
    check("read one mail",
          *verdict("mcp__gws__gmail_message")[:1], want=True)
    check("search drive",
          *verdict("mcp__gws__drive_search")[:1], want=True)
    check("read one file's metadata",
          *verdict("mcp__gws__drive_file")[:1], want=True)
    check("read one file's contents",
          *verdict("mcp__gws__drive_read_file")[:1], want=True)
    # The one write the owner asked for, once they granted the calendar write scope.
    # It cannot reach anyone else: gws.py builds the body from named fields,
    # so no attendee list survives, and it sends sendUpdates=none.
    check("creating an event is allowed",
          *verdict("mcp__gws__calendar_create_event")[:1], want=True)
    check("updating an event is allowed",
          *verdict("mcp__gws__calendar_update_event")[:1], want=True)
    check("moving an event is allowed",
          *verdict("mcp__gws__calendar_move_event")[:1], want=True)
    # The four Drive writes, all confined to the folders DRIVE_WRITE_FOLDERS
    # names: a new Doc, more text at the end of one already there, one exact
    # string swapped inside one, or the whole body of one rewritten. gws.py
    # has no files.update, no files.delete and no permissions call, so no file
    # can be deleted, renamed, moved or shared -- the last two change text
    # inside a Doc and nothing else, and Google's version history keeps what
    # they replaced.
    check("creating a drive doc is allowed",
          *verdict("mcp__gws__drive_create_doc")[:1], want=True)
    check("appending to a drive doc is allowed",
          *verdict("mcp__gws__drive_append_doc")[:1], want=True)
    check("replacing text inside a drive doc is allowed",
          *verdict("mcp__gws__drive_replace_text")[:1], want=True)
    check("rewriting a drive doc is allowed",
          *verdict("mcp__gws__drive_rewrite_doc")[:1], want=True)
    check("editing a file they wrote stays out",
          *verdict("mcp__gws__drive_update_file")[:1], want=False)
    check("deleting a file stays out",
          *verdict("mcp__gws__drive_delete_file")[:1], want=False)
    # Deleting is the one calendar verb that stays out: an edit can be undone
    # from the before-values the tool reports, a deletion cannot.
    check("deleting an event stays out",
          *verdict("mcp__gws__calendar_delete_event")[:1], want=False)
    check("answering an invitation stays out",
          *verdict("mcp__gws__calendar_respond_event")[:1], want=False)
    check("sending mail stays out",
          *verdict("mcp__gws__gmail_send")[:1], want=False)
    check("replying stays out",
          *verdict("mcp__gws__gmail_reply")[:1], want=False)
    check("uploading to drive stays out",
          *verdict("mcp__gws__drive_upload")[:1], want=False)
    check("sharing a file stays out",
          *verdict("mcp__gws__drive_share")[:1], want=False)
    check("a lookalike name does not pass",
          *verdict("mcp__gws__calendar_events_insert")[:1], want=False)
    check("another server borrowing the tool name does not pass",
          *verdict("mcp__elsewhere__calendar_events")[:1], want=False)
    check("the old claude.ai connector is not what we use",
          *verdict("mcp__claude_ai_Google_Calendar__list_events")[:1], want=False)
    # Python's `$` matches before a trailing newline, so an anchored pattern
    # accepts "name\n" unless it ends in \Z.
    check("a trailing newline does not smuggle a name past the anchor",
          *verdict("mcp__gws__calendar_events\n")[:1], want=False)
    check("nor past the memory-tool anchor",
          *verdict("mcp__mcp-search__search\n")[:1], want=False)

    print("\nproposed scripts")
    # Rule 4 wants the model to write reusable scripts, but scripts/ holds
    # distill.py, which the timer runs every night: only the proposals
    # subfolder is writable.
    check("proposes in scripts/proposals", *verdict("Write", file_path="scripts/proposals/sum.py")[:1], want=True)
    check("does not touch distill.py", *verdict("Write", file_path="scripts/distill.py")[:1], want=False)
    check("does not write in scripts/", *verdict("Write", file_path="scripts/other.py")[:1], want=False)
    check("no climbing out of proposals", *verdict("Write", file_path="scripts/proposals/../distill.py")[:1], want=False)

    print("\nskills")
    # A skill is method the model reads and then follows, so writing one and
    # adopting one have to stay separate acts: the write is the model's, the
    # entry in ASSISTANT_SKILLS is the owner's.
    p3 = Policy(root, frozenset(), ("price-hunt",))

    def verdict3(tool, **kw):
        return p3.check(tool, kw).allow

    check("invokes a live skill", verdict3("Skill", skill="price-hunt"), want=True)
    check("trims the name before matching",
          verdict3("Skill", skill="  price-hunt  "), want=True)
    check("a skill it wrote itself is not live",
          verdict3("Skill", skill="self-written"), want=False)
    check("no name given", verdict3("Skill"), want=False)
    check("name that is not a string", verdict3("Skill", skill=17), want=False)
    # The SDK's own skills list only filters the listing -- the model can name
    # a skill it found by reading the folder. This is the gate.
    check("no skills configured means none invocable",
          p.check("Skill", {"skill": "price-hunt"}).allow, want=False)

    check("writes a new skill", verdict3("Write", file_path=".claude/skills/new/SKILL.md"), want=True)
    check("cannot edit a live skill",
          verdict3("Edit", file_path=".claude/skills/price-hunt/SKILL.md"), want=False)
    check("cannot bundle a script beside one",
          verdict3("Write", file_path=".claude/skills/new/run.py"), want=False)
    check("cannot reach settings.json from skills/",
          verdict3("Write", file_path=".claude/skills/../settings.json"), want=False)
    check("still cannot write .claude itself",
          verdict3("Write", file_path=".claude/settings.local.json"), want=False)
    check("reads a live skill", verdict3("Read", file_path=".claude/skills/price-hunt/SKILL.md"), want=True)

    print("\nshell")
    check("bash", *verdict("Bash", command="ls")[:1], want=False)
    check("unknown future tool", *verdict("SomeNewTool", x=1)[:1], want=False)

    print("\nweb")
    check("websearch", *verdict("WebSearch", query="weather")[:1], want=True)
    check("allowlisted host", *verdict("WebFetch", url="https://example.com/a")[:1], want=True)
    check("allowlisted subdomain", *verdict("WebFetch", url="https://docs.example.com/a")[:1], want=True)
    check("lookalike domain", *verdict("WebFetch", url="https://evil-example.com/a")[:1], want=False)
    check("suffix-glued domain", *verdict("WebFetch", url="https://exampleXcom.tld/a")[:1], want=False)
    check("non-allowlisted host", *verdict("WebFetch", url="https://evil.tld/a")[:1], want=False)
    check("plain http", *verdict("WebFetch", url="http://example.com/a")[:1], want=False)

    print("\nopen web (no allowlist)")
    p2 = Policy(root, frozenset())
    check("allows any site",
          p2.check("WebFetch", {"url": "https://example.com"}).allow, want=True)
    check("http still refused",
          p2.check("WebFetch", {"url": "http://example.com"}).allow, want=False)
    check("absurdly long url",
          p2.check("WebFetch", {"url": "https://a.tld/" + "x" * 3000}).allow,
          want=False)

    print("\nSSRF: addresses that are not the internet")
    # The real risk of opening WebFetch on a VPS: a public name pointing inside
    # the network, or the metadata endpoint that hands back the instance
    # credentials.
    for url, label in [
        ("https://127.0.0.1/admin", "loopback"),
        ("https://localhost/admin", "localhost"),
        ("https://10.0.0.1/", "private network 10/8"),
        ("https://192.168.1.1/", "private network 192.168"),
        ("https://169.254.169.254/latest/meta-data/", "cloud metadata"),
        ("https://[::1]/", "loopback IPv6"),
    ]:
        check(label, p2.check_host(url).allow, want=False)

    check("nonexistent host",
          p2.check_host("https://really-does-not-exist-12345.invalid/").allow,
          want=False)

    try:
        public = p2.check_host("https://example.com/")
    except Exception:
        public = None
    if public is None:
        print("  skip public host (no network)")
    else:
        check("public host allowed", public.allow, want=True,
              reason=public.reason)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED:")
        for f in FAILURES:
            print("  -", f)
        return 1
    print("all policy tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
