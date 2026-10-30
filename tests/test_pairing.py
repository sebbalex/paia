#!/usr/bin/env python3
"""Pairing: the owner proves themselves with a code from the log.

Run: .venv/bin/python tests/test_pairing.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from assistant.pairing import OWNER_FILE, Pairing  # noqa: E402
from assistant.main import PAIRED, _pump  # noqa: E402
from assistant.telegram import Incoming, Telegram  # noqa: E402

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    if not ok:
        FAILURES.append(f"{name}: {detail}")
    print(f"  {'ok  ' if ok else 'FAIL'} {name}")


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


def logged_code(capture: Capture) -> str:
    codes = [line.split()[2] for line in capture.lines if line.startswith("PAIRING CODE:")]
    return codes[-1] if codes else ""


def test_pairing() -> None:
    print("pairing")
    capture = Capture()
    logging.getLogger("assistant.pairing").addHandler(capture)
    logging.getLogger("assistant.pairing").setLevel(logging.INFO)
    with tempfile.TemporaryDirectory() as t:
        state = Path(t)

        closed = Pairing(state, configured=frozenset({7}))
        check("ids in the options: pairing never opens", not closed.open)
        check("no code logged for a configured install", logged_code(capture) == "")

        clock = Clock()
        pairing = Pairing(state, clock=clock)
        code = logged_code(capture)
        check("open with nobody configured or paired", pairing.open)
        check("code is six digits in the log", len(code) == 6 and code.isdigit(), code)

        check("hello costs no attempt", not pairing.try_pair(42, "hi"))
        wrong = f"{(int(code) + 1) % 1_000_000:06d}"
        for _ in range(4):
            pairing.try_pair(42, wrong)
        check("four wrong codes leave it open", pairing.open)
        check("right code pairs", pairing.try_pair(42, f"  {code}\n"))
        check("closed after pairing", not pairing.open)
        saved = json.loads((state / OWNER_FILE).read_text())
        check("owner saved", saved.get("user_ids") == [42], str(saved))
        check("the same code does not pair a second person",
              not pairing.try_pair(43, code))

        again = Pairing(state, clock=clock)
        check("a restart after pairing stays closed", not again.open)
        check("owners read back", again.owners() == frozenset({42}))

    with tempfile.TemporaryDirectory() as t:
        state = Path(t)
        pairing = Pairing(state, clock=Clock())
        code = logged_code(capture)
        wrong = f"{(int(code) + 1) % 1_000_000:06d}"
        for _ in range(5):
            pairing.try_pair(42, wrong)
        check("five wrong codes close it", not pairing.open)
        check("even the right code is refused then", not pairing.try_pair(42, code))
        check("nothing saved", not (state / OWNER_FILE).exists())

    with tempfile.TemporaryDirectory() as t:
        state = Path(t)
        clock = Clock()
        pairing = Pairing(state, clock=clock, ttl_seconds=600)
        old = logged_code(capture)
        clock.now += 601
        check("expired code refused", not pairing.try_pair(42, old))
        new = logged_code(capture)
        check("expiry rotates the code into the log", new != "" and pairing.open)
        check("the rotated code pairs", new == old or pairing.try_pair(42, new))

    with tempfile.TemporaryDirectory() as t:
        state = Path(t)
        (state / OWNER_FILE).write_text("{ not json")
        try:
            Pairing(state)
            check("an unreadable owner file stops the start", False)
        except SystemExit:
            check("an unreadable owner file stops the start", True)


def _update(uid: int, text: str, chat_type: str = "private", update_id: int = 1) -> dict:
    return {"update_id": update_id, "message": {
        "from": {"id": uid}, "chat": {"id": uid, "type": chat_type}, "text": text}}


async def _poll(tg: Telegram, batches: list[list[dict]]) -> tuple[list, list]:
    """Feed `batches` through poll(), return what it yielded and what it sent."""
    sent: list[dict] = []
    queue = list(batches)

    async def fake_call(method, **params):
        if method == "getUpdates":
            if not queue:
                raise asyncio.CancelledError
            return queue.pop(0)
        sent.append({"method": method, **params})
        return {}

    tg._call = fake_call
    yielded = []
    try:
        async for incoming in tg.poll():
            yielded.append(incoming)
    except asyncio.CancelledError:
        pass
    return yielded, sent


async def test_transport() -> None:
    print("\ntransport")
    capture = Capture()
    logging.getLogger("assistant.pairing").addHandler(capture)

    tg = Telegram("token", frozenset({7}))
    yielded, sent = await _poll(tg, [[_update(99, "hello")]])
    check("no pairing: stranger dropped", yielded == [] and sent == [], f"{yielded} {sent}")

    with tempfile.TemporaryDirectory() as t:
        pairing = Pairing(Path(t))
        code = logged_code(capture)
        tg = Telegram("token", frozenset(), pairing)

        yielded, sent = await _poll(tg, [[_update(99, code, chat_type="group")]])
        check("a group chat cannot pair", yielded == [] and pairing.open, str(yielded))

        yielded, sent = await _poll(tg, [[_update(99, "hello", update_id=2),
                                          _update(99, "hello again", update_id=3)]])
        check("stranger gets the hint, once a minute",
              yielded == [] and len(sent) == 1, str(sent))
        check("the hint never carries the code",
              all(code not in json.dumps(s) for s in sent), str(sent))

        yielded, sent = await _poll(tg, [[_update(99, code, update_id=4)],
                                         [_update(99, "what's up", update_id=5)]])
        check("pairing message yielded as paired, without its text",
              len(yielded) == 2 and yielded[0].paired and yielded[0].text == "",
              str(yielded))
        check("the owner's next message comes through",
              yielded[1].text == "what's up" and not yielded[1].paired, str(yielded))

        yielded, sent = await _poll(tg, [[_update(100, code, update_id=6)]])
        check("a second person with the code is dropped silently",
              yielded == [] and sent == [], f"{yielded} {sent}")
        await tg.aclose()


async def test_pump() -> None:
    print("\npump")

    class Tg:
        def __init__(self) -> None:
            self.sent: list[tuple[int, str]] = []

        async def poll(self):
            yield Incoming(chat_id=42, user_id=42, text="", paired=True)

        async def send(self, chat_id, text):
            self.sent.append((chat_id, text))

    class Delivery:
        chat = None

        def set_chat(self, chat_id):
            self.chat = chat_id

    class Brain:
        asked: list[str] = []

        async def ask(self, prompt, **_):
            self.asked.append(prompt)

    tg, delivery, brain = Tg(), Delivery(), Brain()
    await _pump(None, tg, brain, None, delivery)
    check("delivery now goes to the paired chat", delivery.chat == 42)
    check("the owner is greeted", tg.sent == [(42, PAIRED)], str(tg.sent))
    check("the model is not asked", brain.asked == [], str(brain.asked))


def main() -> int:
    test_pairing()
    asyncio.run(test_transport())
    asyncio.run(test_pump())
    if FAILURES:
        print("\n".join(["", "FAILURES:", *FAILURES]))
        return 1
    print("\nall ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
