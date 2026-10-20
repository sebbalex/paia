"""Tier 3 memory: the dumb append-only log.

claude-mem holds the semantic index and memory/wiki holds the curated facts.
This module owns neither. It writes one plain markdown file per day so that
the full conversation survives even if the database is lost or the plugin
changes shape underneath us. Nothing reads this at runtime; it exists for the
nightly distiller and for grep.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

_lock = asyncio.Lock()


async def append(raw_dir: Path, role: str, text: str) -> None:
    raw_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    path = raw_dir / f"{now:%Y-%m-%d}.md"
    entry = f"\n### {role} — {now:%H:%M:%S}\n\n{text.strip()}\n"
    async with _lock:
        new_file = not path.exists()
        with path.open("a", encoding="utf-8") as fh:
            if new_file:
                fh.write(f"# {now:%Y-%m-%d}\n")
            fh.write(entry)
