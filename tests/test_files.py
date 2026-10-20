#!/usr/bin/env python3
"""Attachments: filters, name sanitising, expiry, prompt.

Run: .venv/bin/python tests/test_files.py
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from assistant.main import _receive_file, _safe_name, _sweep_inbox  # noqa: E402
from assistant.telegram import Document, Incoming  # noqa: E402

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    if not ok:
        FAILURES.append(f"{name}: {detail}")
    print(f"  {'ok  ' if ok else 'FAIL'} {name}")


class Cfg:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.inbox_dir = root / "inbox"
        self.allowed_file_types = (".pdf", ".png", ".jpg", ".txt")
        self.max_file_bytes = 20 * 1024 * 1024
        self.inbox_retention_days = 7


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
        dest.write_bytes(b"%PDF-1.4 fake")
        return True


def incoming(name="report.pdf", size=1000, caption="", mime="application/pdf"):
    return Incoming(chat_id=1, user_id=2, text=caption,
                    document=Document(file_id="f", file_name=name,
                                      mime=mime, size=size))


async def run() -> int:
    root = Path(tempfile.mkdtemp(prefix="files-"))
    cfg = Cfg(root)

    print("\nhostile names")
    check("traversal neutralised", "/" not in _safe_name("../../.env"),
          _safe_name("../../.env"))
    check("extension preserved on a long name",
          _safe_name("x" * 300 + ".pdf").endswith(".pdf"))
    check("no path separators",
          "\\" not in _safe_name("a\\b.pdf") and "/" not in _safe_name("a/b.pdf"))

    print("\npdf accepted")
    tg = FakeTelegram()
    prompt = await _receive_file(cfg, tg, incoming(caption="How much is it?"))
    check("downloads", tg.downloaded)
    check("prompt cites the path", "inbox/" in prompt, prompt)
    check("prompt carries the question", "How much is it?" in prompt, prompt)
    check("tells the model to open it", "Read" in prompt, prompt)
    files = list(cfg.inbox_dir.glob("*.pdf"))
    check("file on disk", len(files) == 1, str(files))
    check("name carries a timestamp", files and "_report.pdf" in files[0].name,
          str(files))

    print("\nno caption")
    tg = FakeTelegram()
    prompt = await _receive_file(cfg, tg, incoming(name="photo.jpg", mime="image/jpeg"))
    check("still asks something", "contains" in prompt.lower(), prompt)

    print("\nrejected types")
    for bad in ("virus.exe", "archive.zip", "script.sh"):
        tg = FakeTelegram()
        prompt = await _receive_file(cfg, tg, incoming(name=bad))
        check(f"{bad} rejected", prompt == "" and not tg.downloaded,
              f"prompt={prompt!r} downloaded={tg.downloaded}")

    print("\ntoo big")
    tg = FakeTelegram()
    prompt = await _receive_file(cfg, tg, incoming(size=99 * 1024 * 1024))
    check("rejected before downloading", prompt == "" and not tg.downloaded)
    check("says so", any("too big" in m for m in tg.sent), str(tg.sent))

    print("\ninbox expiry")
    old_file = cfg.inbox_dir / "old.pdf"
    old_file.write_bytes(b"x")
    import os
    ancient = time.time() - 30 * 86400
    os.utime(old_file, (ancient, ancient))
    recent = cfg.inbox_dir / "recent.pdf"
    recent.write_bytes(b"x")
    _sweep_inbox(cfg)
    check("the old one disappears", not old_file.exists())
    check("the recent one stays", recent.exists())

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED:")
        for f in FAILURES:
            print("  -", f)
        return 1
    print("attachments ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
