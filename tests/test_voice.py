#!/usr/bin/env python3
"""Voice flow: parsing, limits, and rejections.

Run: .venv/bin/python tests/test_voice.py
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from assistant.agent import Turn  # noqa: E402
from assistant.main import _pump, _transcribe  # noqa: E402
from assistant.telegram import Incoming, Voice  # noqa: E402

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    if not ok:
        FAILURES.append(f"{name}: {detail}")
    print(f"  {'ok  ' if ok else 'FAIL'} {name}")


class FakeCfg:
    max_voice_seconds = 600
    max_voice_bytes = 20 * 1024 * 1024
    raw_memory_dir = Path(tempfile.mkdtemp(prefix="pump-raw-"))


class FakeTelegram:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.downloaded = False

    async def send(self, chat_id: int, text: str) -> None:
        self.sent.append(text)

    async def typing(self, chat_id: int) -> None:
        pass

    async def download(self, file_id: str, dest: Path) -> bool:
        self.downloaded = True
        dest.write_bytes(b"fake")
        return True


class FakeTranscriber:
    def __init__(self, text: str = "hello", available: bool = True) -> None:
        self._text, self.available = text, available

    async def transcribe(self, path: Path) -> str:
        return self._text


def incoming(duration: int = 5, size: int = 1000) -> Incoming:
    return Incoming(chat_id=1, user_id=2, text="",
                    voice=Voice(file_id="f", duration=duration, size=size))


async def run() -> int:
    cfg = FakeCfg()

    print("\nnormal case")
    tg, tr = FakeTelegram(), FakeTranscriber("remind me about the shopping")
    text = await _transcribe(cfg, tg, tr, incoming())
    check("returns the text", text == "remind me about the shopping", repr(text))
    check("echoes the transcript", any("remind me" in m for m in tg.sent), str(tg.sent))
    check("downloads the file", tg.downloaded)

    print("\nvoice note too long")
    tg, tr = FakeTelegram(), FakeTranscriber()
    text = await _transcribe(cfg, tg, tr, incoming(duration=9999))
    check("no text", text == "", repr(text))
    check("downloads nothing", not tg.downloaded, "downloaded anyway")
    check("warns the owner", any("too long" in m for m in tg.sent), str(tg.sent))

    print("\nvoice note too heavy")
    tg, tr = FakeTelegram(), FakeTranscriber()
    text = await _transcribe(cfg, tg, tr, incoming(size=99 * 1024 * 1024))
    check("no text", text == "", repr(text))
    check("downloads nothing", not tg.downloaded, "downloaded anyway")

    print("\nfaster-whisper missing")
    tg, tr = FakeTelegram(), FakeTranscriber(available=False)
    text = await _transcribe(cfg, tg, tr, incoming())
    check("no text", text == "", repr(text))
    check("downloads nothing", not tg.downloaded, "downloaded anyway")
    check("says so", any("faster-whisper" in m for m in tg.sent), str(tg.sent))

    print("\nsilence")
    tg, tr = FakeTelegram(), FakeTranscriber("")
    text = await _transcribe(cfg, tg, tr, incoming())
    check("no text", text == "", repr(text))
    check("says so", any("no speech" in m for m in tg.sent), str(tg.sent))

    print("\npump: the spoken question reaches the model")

    class PollingTelegram(FakeTelegram):
        async def poll(self):
            yield incoming()

    class FakeBrain:
        def __init__(self) -> None:
            self.prompts: list[str] = []

        async def ask(self, prompt: str) -> Turn:
            self.prompts.append(prompt)
            return Turn("answer", ok=True)

    tg = PollingTelegram()
    brain = FakeBrain()
    # The bug this test pins: _pump passed incoming.text to brain.ask, which
    # for a voice note is empty, so the model answered nothing while the log
    # recorded the correct transcript.
    class NoDelivery:
        def available(self, channel): return True
        async def send(self, channel, subject, body): pass

    await _pump(cfg, tg, brain, FakeTranscriber("how old is the server?"),
                NoDelivery())
    check("the model receives the transcript",
          brain.prompts == ["how old is the server?"], str(brain.prompts))
    check("the model never receives an empty string",
          "" not in brain.prompts, "empty prompt passed to the model")
    check("the answer is sent",
          any("answer" in m for m in tg.sent), str(tg.sent))

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED:")
        for f in FAILURES:
            print("  -", f)
        return 1
    print("voice flow ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
