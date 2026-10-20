#!/usr/bin/env python3
"""A failed turn must reach the owner but not memory/raw/.

Run: .venv/bin/python tests/test_turn_logging.py
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from assistant import memory  # noqa: E402
from assistant.agent import Turn  # noqa: E402


class FakeTelegram:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, chat_id: int, text: str) -> None:
        self.sent.append(text)


async def run() -> int:
    raw = Path(tempfile.mkdtemp(prefix="turnlog-")) / "raw"
    tg = FakeTelegram()
    failures = []

    async def deliver(turn: Turn) -> None:
        # Mirrors assistant/main.py::_pump
        if turn.ok:
            await memory.append(raw, "assistant", turn.text)
        await tg.send(1, turn.text)

    await deliver(Turn("a real answer", ok=True))
    await deliver(Turn("API Error: 401 API key is invalid.", ok=False))
    await deliver(Turn("another real answer", ok=True))

    logged = "\n".join(p.read_text() for p in raw.glob("*.md")) if raw.exists() else ""

    if "401" in logged:
        failures.append("error text was written to the raw log")
    for text in ("a real answer", "another real answer"):
        if text not in logged:
            failures.append(f"real reply missing from raw log: {text!r}")
    if len(tg.sent) != 3:
        failures.append(f"expected 3 messages sent to the owner, got {len(tg.sent)}")
    if "401" not in "".join(tg.sent):
        failures.append("error was not reported to the owner")

    print("  raw log entries :", logged.count("### assistant"))
    print("  messages sent   :", len(tg.sent))
    if failures:
        print("\nFAILED:")
        for f in failures:
            print("  -", f)
        return 1
    print("\nfailed turns reported but not remembered — ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
