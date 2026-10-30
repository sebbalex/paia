#!/usr/bin/env python3
"""Onboarding: the interview pairing starts and /onboard restarts.

Run: .venv/bin/python tests/test_onboarding.py
"""

from __future__ import annotations

import asyncio
import logging
import re
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from assistant import main as M  # noqa: E402
from assistant import onboarding as O  # noqa: E402
from assistant.agent import Turn  # noqa: E402
from assistant.proposals import TO_FILL, open_blanks  # noqa: E402
from assistant.telegram import Incoming  # noqa: E402

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    if not ok:
        FAILURES.append(f"{name}: {detail}")
    print(f"  {'ok  ' if ok else 'FAIL'} {name}")


def brief(name: str, body: str = "- x: y\n") -> str:
    return f"---\nname: {name}\nupdated: 2026-10-01\n---\n\n{body}"


def make_root(tmp: Path, briefs: dict[str, str] | None = None) -> Path:
    root = tmp / "paia"
    (root / "context").mkdir(parents=True)
    for name, body in (briefs or {}).items():
        (root / "context" / f"{name}.md").write_text(brief(name, body))
    return root


class Cfg:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.raw_memory_dir = root / "memory" / "raw"
        self.state_dir = root / "state"
        self.routines_dir = root / "routines"
        self.context_target = None


class Tg:
    """Records everything in order: sends as ("send", text), typing as ("typing",)."""

    def __init__(self, incoming: list[Incoming] | None = None) -> None:
        self.incoming = incoming or []
        self.events: list[tuple] = []

    async def poll(self):
        for item in self.incoming:
            yield item

    async def send(self, chat_id: int, text: str) -> None:
        self.events.append(("send", chat_id, text))

    async def typing(self, chat_id: int) -> None:
        self.events.append(("typing", chat_id))

    @property
    def sent(self) -> list[str]:
        return [e[2] for e in self.events if e[0] == "send"]


class Brain:
    """Answers from a script: a Turn, or an exception to raise."""

    def __init__(self, *script) -> None:
        self.script = list(script)
        self.asked: list[str] = []

    async def ask(self, prompt: str, **_) -> Turn:
        self.asked.append(prompt)
        await asyncio.sleep(0)  # a real turn takes time: let typing start
        answer = self.script.pop(0) if self.script else Turn("ok", True)
        if isinstance(answer, BaseException):
            raise answer
        return answer


class Delivery:
    def __init__(self) -> None:
        self.chat = None

    def set_chat(self, chat_id: int) -> None:
        self.chat = chat_id

    def available(self, channel: str) -> bool:
        return True


def paired() -> Incoming:
    return Incoming(chat_id=42, user_id=42, text="", paired=True)


def said(text: str) -> Incoming:
    return Incoming(chat_id=42, user_id=42, text=text)


def raw_log(cfg: Cfg) -> str:
    files = sorted(cfg.raw_memory_dir.glob("*.md")) if cfg.raw_memory_dir.is_dir() else []
    return "".join(f.read_text() for f in files)


async def pump(cfg: Cfg, tg: Tg, brain: Brain, delivery: Delivery | None = None) -> Delivery:
    delivery = delivery or Delivery()
    await M._pump(cfg, tg, brain, None, delivery)
    return delivery


# -- topics and the prompt ---------------------------------------------------


def test_topics() -> None:
    print("topics")
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        check("no context/ folder: no topics", O.topics(tmp / "nowhere") == [])
        root = make_root(tmp, {
            "zebra-rules": "", "training-rules": "", "shopping-rules": "",
            "calendar-rules": "", "alpha-rules": "", "notes": "",
        })
        (root / "context" / "dir-rules.md").mkdir()
        (root / "context" / "readme.txt").write_text("x")
        got = O.topics(root)
        check("ROT order first, the rest alphabetical",
              got == ["shopping", "training", "calendar", "alpha", "zebra"], str(got))
        check("a file without -rules is not a topic", "notes" not in got)
        check("a directory named like a brief is not a topic", "dir" not in got)

        for raw, want in (("training", "training"), ("training-rules", "training"),
                          ("Training-Rules.md", "training"), ("  shopping ", "shopping"),
                          ("ZEBRA", "zebra")):
            check(f"match {raw!r}", O.match(root, raw) == want, str(O.match(root, raw)))
        for raw in ("", "nope", "../context/training", "training/../shopping", "notes", "dir"):
            check(f"no match for {raw!r}", O.match(root, raw) is None, str(O.match(root, raw)))

    with tempfile.TemporaryDirectory() as t:
        root = make_root(Path(t), {"daily-tasks-rules": "", "home-duties-rules": ""})
        for raw in ("daily tasks", "Daily_Tasks", "daily-tasks", "home  duties"):
            check(f"spaces and underscores: {raw!r}", O.match(root, raw) is not None,
                  str(O.match(root, raw)))


def test_prompt() -> None:
    print("\nprompt")
    shipped = REPO / "defaults" / "context"
    with tempfile.TemporaryDirectory() as t:
        root = Path(t) / "paia"
        root.mkdir()
        (root / "context").symlink_to(shipped)
        names = sorted(p.stem.removesuffix("-rules") for p in shipped.glob("*-rules.md"))
        check("every shipped brief is a topic", sorted(O.topics(root)) == names,
              f"{O.topics(root)} vs {names}")
        check("every shipped brief is in the known order",
              all(n in O.ORDER for n in names), str(set(names) - set(O.ORDER)))

        text = O.prompt(root)
        check("no placeholder left", "$" not in text, re.findall(r"\$\w+", text).__str__())
        check("names every topic", all(f"`{n}`" in text for n in names))
        blanks = sum(len(TO_FILL.findall(p.read_text())) for p in shipped.glob("*.md"))
        check("states the open blanks", f"have {blanks} `TO FILL`" in text
              and blanks == open_blanks(root), str(blanks))
        for needle in ("memory/wiki/proposals/", "/review", "/accept", "/enable",
                       "ROT.md", "coach", "skip", "stop"):
            check(f"tells the model about {needle}", needle in text)

        one = O.prompt(root, "training")
        check("one topic: only that topic listed",
              "`training`" in one and "`shopping`" not in one, one[:400])
        check("one topic: says so", "Only the topic `training`" in one)

    with tempfile.TemporaryDirectory() as t:
        root = make_root(Path(t))
        text = O.prompt(root)
        check("no briefs: says there are none", "(none: there are no briefs" in text)
        check("no briefs: zero blanks", "have 0 `TO FILL`" in text)

    template = O.TEMPLATE.read_text()
    placeholders = set(re.findall(r"\$(\w+)", template))
    check("the template uses exactly the placeholders filled in",
          placeholders == {"focus", "topics", "blanks"}, str(placeholders))


# -- the transport -------------------------------------------------------------


async def test_pairing_starts_it() -> None:
    print("\npairing starts the interview")
    with tempfile.TemporaryDirectory() as t:
        root = make_root(Path(t), {"training-rules": "- goal: **TO FILL**\n"})
        cfg = Cfg(root)
        tg, brain = Tg([paired()]), Brain(Turn("Topic 1 of 1: training. When is the goal?", True))
        delivery = await pump(cfg, tg, brain)
        check("delivery follows the paired chat", delivery.chat == 42)
        check("asked exactly once", len(brain.asked) == 1, str(len(brain.asked)))
        check("with the onboarding prompt", brain.asked and brain.asked[0] == O.prompt(root))
        check("greeting first, question second",
              tg.sent == [M.PAIRED, "Topic 1 of 1: training. When is the goal?"], str(tg.sent))
        check("typing shown while it thinks", ("typing", 42) in tg.events)
        log = raw_log(cfg)
        check("the question is in memory/raw", "When is the goal?" in log, log)
        check("the transport's prompt is not passed off as the owner's words",
              "Onboard me" not in log and "### owner" not in log, log)

    with tempfile.TemporaryDirectory() as t:
        cfg = Cfg(make_root(Path(t), {"training-rules": ""}))
        tg, brain = Tg([paired()]), Brain(Turn("API error 529: overloaded", False))
        await pump(cfg, tg, brain)
        check("a failed turn is still reported", tg.sent[-1] == "API error 529: overloaded")
        check("a failed turn is kept out of memory", "529" not in raw_log(cfg))

    with tempfile.TemporaryDirectory() as t:
        cfg = Cfg(make_root(Path(t), {"training-rules": ""}))
        tg = Tg([paired(), said("hello")])
        brain = Brain(RuntimeError("sdk crashed"), Turn("hi there", True))
        await pump(cfg, tg, brain)
        check("a turn that raises does not end the pump",
              brain.asked[-1] == "hello" and tg.sent[-1] == "hi there", str(tg.sent))
        check("the owner hears it failed", any("failed" in s for s in tg.sent), str(tg.sent))
        check("no stray typing task left running",
              not [x for x in asyncio.all_tasks()
                   if "_keep_typing" in repr(x.get_coro()) and not x.done()])

    with tempfile.TemporaryDirectory() as t:
        cfg = Cfg(make_root(Path(t), {"training-rules": ""}))
        real = O.TEMPLATE
        O.TEMPLATE = Path(t) / "missing.md"
        try:
            tg, brain = Tg([paired(), said("hello")]), Brain()
            await pump(cfg, tg, brain)
        finally:
            O.TEMPLATE = real
        check("a missing template is reported, not crashed on",
              any("Cannot start the interview" in s for s in tg.sent), str(tg.sent))
        check("and the model is not asked with half a prompt", brain.asked == ["hello"],
              str(brain.asked))

    with tempfile.TemporaryDirectory() as t:
        cfg = Cfg(make_root(Path(t), {"training-rules": ""}))
        bad = Path(t) / "bad.md"
        bad.write_text("Costs $5 and $topics")
        real = O.TEMPLATE
        O.TEMPLATE = bad
        try:
            tg, brain = Tg([paired()]), Brain()
            await pump(cfg, tg, brain)
        finally:
            O.TEMPLATE = real
        check("a template with a stray $ is reported, not crashed on",
              any("Cannot start the interview" in s for s in tg.sent) and brain.asked == [],
              str(tg.sent))


async def test_onboard_command() -> None:
    print("\n/onboard")
    with tempfile.TemporaryDirectory() as t:
        root = make_root(Path(t), {"training-rules": "- goal: **TO FILL**\n",
                                   "daily-tasks-rules": "", "shopping-rules": ""})
        cfg = Cfg(root)

        async def run(text: str) -> tuple[Tg, Brain]:
            tg, brain = Tg([said(text)]), Brain(Turn("question", True))
            await pump(cfg, tg, brain)
            return tg, brain

        tg, brain = await run("/onboard")
        check("all topics", brain.asked == [O.prompt(root)])
        check("the answer is sent", tg.sent == ["question"], str(tg.sent))

        tg, brain = await run("/onboard training")
        check("one topic", brain.asked == [O.prompt(root, "training")])
        tg, brain = await run("/onboard Daily Tasks")
        check("a topic in words", brain.asked == [O.prompt(root, "daily-tasks")])
        tg, brain = await run("/onboard@paia_bot shopping-rules")
        check("bot suffix and brief name", brain.asked == [O.prompt(root, "shopping")])

        tg, brain = await run("/onboard gardening")
        check("unknown topic: model not asked", brain.asked == [])
        check("unknown topic: the real ones listed",
              tg.sent and "gardening" in tg.sent[0] and "`training`" in tg.sent[0], str(tg.sent))
        tg, brain = await run("/onboard ../../.env")
        check("a path is not a topic", brain.asked == [] and "No brief" in tg.sent[0])

        check("/onboard is in the menu", "onboard" in [n for n, _ in M.COMMANDS])
        check("/onboard is in /help", "/onboard" in M.HELP)

    with tempfile.TemporaryDirectory() as t:
        cfg = Cfg(make_root(Path(t)))
        tg, brain = Tg([said("/onboard training")]), Brain()
        await pump(cfg, tg, brain)
        check("no briefs at all: says there are no topics",
              brain.asked == [] and "Topics: none" in tg.sent[0], str(tg.sent))


async def test_help_hint() -> None:
    print("\n/help")
    with tempfile.TemporaryDirectory() as t:
        root = make_root(Path(t), {"training-rules": "- goal: **TO FILL**\n- w: `TO FILL`\n"})
        cfg = Cfg(root)
        tg = Tg([said("/help")])
        await pump(cfg, tg, Brain())
        check("open blanks: points at /onboard",
              "2 `TO FILL` lines open: `/onboard`" in tg.sent[0], tg.sent[0][-120:])

        (root / "context" / "training-rules.md").write_text(brief("training-rules", "- goal: GF\n"))
        tg = Tg([said("/start")])
        await pump(cfg, tg, Brain())
        check("none open: no hint", "lines open" not in tg.sent[0] and tg.sent[0] == M.HELP)

        (root / "context").rename(root / "gone")
        tg = Tg([said("/help")])
        await pump(cfg, tg, Brain())
        check("no context/ at all: plain help", tg.sent[0] == M.HELP)


def main() -> int:
    # The failure paths log tracebacks on purpose; the checks are the output.
    logging.disable(logging.CRITICAL)
    test_topics()
    test_prompt()
    asyncio.run(test_pairing_starts_it())
    asyncio.run(test_onboard_command())
    asyncio.run(test_help_hint())
    if FAILURES:
        print("\n".join(["", "FAILURES:", *FAILURES]))
        return 1
    print("\nall ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
