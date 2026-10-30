#!/usr/bin/env python3
"""Brief proposals: the model proposes, /review and /accept decide.

Run: .venv/bin/python tests/test_proposals.py
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from assistant import proposals as P  # noqa: E402
from assistant.main import _command  # noqa: E402
from assistant.telegram import Incoming  # noqa: E402

FAILURES: list[str] = []

BRIEF = "---\nname: training-rules\nupdated: 2026-10-28\nowner: owner\n---\n\n- goal: **TO FILL**\n"
PROPOSED = BRIEF.replace("**TO FILL**", "Gran Fondo, 2027-05-10")


def check(name: str, ok: bool, detail: str = "") -> None:
    if not ok:
        FAILURES.append(f"{name}: {detail}")
    print(f"  {'ok  ' if ok else 'FAIL'} {name}")


class Cfg:
    def __init__(self, root: Path, target: Path | None) -> None:
        self.root = root
        self.state_dir = root / "state"
        self.context_target = target
        self.routines_dir = root / "routines"


class FakeTelegram:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, chat_id: int, text: str) -> None:
        self.sent.append(text)


def msg(text: str) -> Incoming:
    return Incoming(chat_id=7, user_id=7, text=text)


def _setup(tmp: Path) -> tuple[Path, Path]:
    root, share = tmp / "paia", tmp / "config" / "context"
    (root / "context").mkdir(parents=True)
    (root / "context" / "training-rules.md").write_text(BRIEF)
    share.mkdir(parents=True)
    (share / "training-rules.md").write_text(BRIEF)
    return root, share


def _propose(root: Path, text: str = PROPOSED) -> None:
    folder = root / P.PROPOSALS
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "training-rules.md").write_text(text)


def test_module() -> None:
    print("proposals module")
    with tempfile.TemporaryDirectory() as t:
        root, share = _setup(Path(t))
        state = root / "state"
        check("nothing open", P.list_open(root) == [])
        check("blanks counted", P.open_blanks(root) == 1)
        _propose(root)
        check("listed", P.list_open(root) == ["training-rules"])

        try:
            P.apply(root, state, share, "training-rules")
            check("accept without review refused", False)
        except P.ProposalError as exc:
            check("accept without review refused", "/review" in str(exc), str(exc))

        text, fingerprint = P.diff(root, "training-rules")
        check("diff shows the change",
              "-- goal: **TO FILL**" in text and "+- goal: Gran Fondo" in text, text)
        P.remember_review(state, "training-rules", fingerprint)

        _propose(root, PROPOSED + "- sneaked in: yes\n")
        try:
            P.apply(root, state, share, "training-rules")
            check("changed after review refused", False)
        except P.ProposalError as exc:
            check("changed after review refused", "reviewed" in str(exc), str(exc))

        _, fingerprint = P.diff(root, "training-rules")
        P.remember_review(state, "training-rules", fingerprint)
        name = P.apply(root, state, share, "TRAINING-RULES.md")
        applied = (share / "training-rules.md").read_text()
        check("applied to the share", name == "training-rules" and "Gran Fondo" in applied,
              applied)
        check("updated bumped", f"updated: {date.today().isoformat()}" in applied, applied)
        check("the model's copy untouched until the supervisor syncs",
              (root / "context" / "training-rules.md").read_text() == BRIEF)
        check("proposal gone", P.list_open(root) == [])

    with tempfile.TemporaryDirectory() as t:
        root, share = _setup(Path(t))
        state = root / "state"
        for label, text, want in (
            ("fragment refused", "- goal: Gran Fondo\n", "frontmatter"),
            ("wrong name refused", BRIEF.replace("name: training-rules", "name: other"),
             "name: training-rules"),
            ("frontmatter alone refused", "---\nname: training-rules\n---\n", "nothing else"),
        ):
            _propose(root, text)
            _, fingerprint = P.diff(root, "training-rules")
            P.remember_review(state, "training-rules", fingerprint)
            try:
                P.apply(root, state, share, "training-rules")
                check(label, False)
            except P.ProposalError as exc:
                check(label, want in str(exc), str(exc))
        check("share untouched by refusals", (share / "training-rules.md").read_text() == BRIEF)

        check("reject deletes", P.reject(root, state, "training-rules") == "training-rules"
              and P.list_open(root) == [])
        for bad in ("../../.env", "a/b", ""):
            try:
                P.diff(root, bad)
                check(f"name {bad!r} refused", False)
            except P.ProposalError:
                check(f"name {bad!r} refused", True)


async def test_commands() -> None:
    print("\ncommands")
    with tempfile.TemporaryDirectory() as t:
        root, share = _setup(Path(t))
        cfg = Cfg(root, share)

        async def say(text: str) -> str:
            tg = FakeTelegram()
            handled = await _command(cfg, tg, None, None, msg(text))
            check(f"{text.split()[0]} handled in Python", handled)
            return tg.sent[-1]

        check("/proposals with none", "No brief" in await say("/proposals"))
        _propose(root)
        check("/proposals lists it", "/review training-rules" in await say("/proposals"))
        check("/accept before /review refused", "/review" in await say("/accept training-rules"))
        reply = await say("/review training-rules")
        check("/review sends the diff", "```diff" in reply and "Gran Fondo" in reply, reply)
        reply = await say("/accept@paia_bot training-rules")
        check("/accept applies", "applied" in reply, reply)
        check("on the share", "Gran Fondo" in (share / "training-rules.md").read_text())
        check("/review without a name asks", "Which one" in await say("/review"))

        _propose(root)
        cfg_vps = Cfg(root, None)
        tg = FakeTelegram()
        await _command(cfg_vps, tg, None, None, msg("/review training-rules"))
        await _command(cfg_vps, tg, None, None, msg("/accept training-rules"))
        check("no target: says so, applies nothing",
              "read-only" in tg.sent[-1] and P.list_open(root) == ["training-rules"],
              tg.sent[-1])
        check("/reject discards", "discarded" in await say("/reject training-rules"))


def main() -> int:
    test_module()
    asyncio.run(test_commands())
    if FAILURES:
        print("\n".join(["", "FAILURES:", *FAILURES]))
        return 1
    print("\nall ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
