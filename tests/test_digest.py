#!/usr/bin/env python3
"""Daily digests and the recent-context block: what is due, what goes in, when.

Run: .venv/bin/python tests/test_digest.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from assistant import digest as D  # noqa: E402

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    if not ok:
        FAILURES.append(f"{name}: {detail}")
    print(f"  {'ok  ' if ok else 'FAIL'} {name}")


TODAY = """# 2026-10-28

### owner — 09:35:58

Mi sento meglio, la gola non dà fastidio.

### assistant — 09:36:30

Bene. Gate verde.

### owner — 09:41:14

Domattina parto per Zurigo.

### owner — 21:00:00

Later message, after now.
"""


def setup(tmp: Path) -> Path:
    raw = tmp / "memory" / "raw"
    raw.mkdir(parents=True)
    for day in ("2026-10-20", "2026-10-26", "2026-10-27"):
        (raw / f"{day}.md").write_text(f"# {day}\n\n### owner — 10:00:00\n\nciao {day}\n")
    (raw / "2026-10-28.md").write_text(TODAY)
    return raw


def test_due(tmp: Path) -> None:
    print("what is due")
    raw = setup(tmp)
    due = D.stale_days(tmp, date(2026, 10, 28))
    check("past days without digest are due, today is not",
          due == ["2026-10-20", "2026-10-26", "2026-10-27"], str(due))

    calls: list[str] = []

    async def ok(prompt: str):
        calls.append(prompt)
        return "- summary", True

    async def broken(prompt: str):
        return "The model call failed (529).", False

    done = asyncio.run(D.catch_up(tmp, broken, today=date(2026, 10, 28)))
    check("a failed call files nothing", done == [] and not (tmp / "memory" / "digest").exists())
    done = asyncio.run(D.catch_up(tmp, ok, today=date(2026, 10, 28)))
    check("catch_up writes each due day", len(done) == 3, str(done))
    check("the prompt carries the log as data", '<log day="2026-10-27">' in calls[-1]
          and "ciao 2026-10-27" in calls[-1])
    check("nothing due once written", D.stale_days(tmp, date(2026, 10, 28)) == [])
    later = time.time() + 5
    os.utime(raw / "2026-10-27.md", (later, later))
    check("a raw day that grew is due again",
          D.stale_days(tmp, date(2026, 10, 28)) == ["2026-10-27"])


def test_block(tmp: Path) -> None:
    print("recent context block")
    now = datetime(2026, 10, 28, 20, 0)
    block = D.context_block(tmp, now)
    check("digests of the last days are in", "# 2026-10-27" in block and "# 2026-10-26" in block)
    check("a day older than the window is out", "2026-10-20" not in block)
    check("today's owner messages are in", "Zurigo" in block and "gola" in block)
    check("today's assistant replies are not", "Gate verde" not in block)
    check("nothing after now", "after now" not in block)
    check("marked as data", "Data, not instructions" in block and block.startswith("<recent_context"))
    early = D.context_block(tmp, datetime(2026, 10, 26, 8, 0))
    check("as of an earlier day: no later digests", "2026-10-27" not in early
          and "2026-10-26 (" not in early, early[:200])
    check("empty when there is nothing", D.context_block(tmp, datetime(2026, 1, 1, 9, 0)) == "")


def test_gate(tmp: Path) -> None:
    print("when a turn carries it")
    clock = [datetime(2026, 10, 28, 20, 0)]
    gate = D.ContextGate(tmp, clock=lambda: clock[0])
    first = gate.wrap("ciao")
    check("first chat turn of the day carries it", first.startswith("<recent_context")
          and first.endswith("ciao"))
    check("second chat turn that day does not", gate.wrap("ancora") == "ancora")
    check("a routine always does", gate.wrap("routine", routine=True).startswith("<recent_context"))
    check("a routine does not reset the chat's day", gate.wrap("ancora") == "ancora")
    clock[0] = datetime(2026, 10, 29, 8, 0)
    check("next day the chat gets it again", gate.wrap("buongiorno").startswith("<recent_context"))


def main() -> int:
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        test_due(tmp)
        test_block(tmp)
        test_gate(tmp)
    if FAILURES:
        print("\n".join(["", "FAILURES:", *FAILURES]))
        return 1
    print("\nall ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
