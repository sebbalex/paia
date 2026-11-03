#!/usr/bin/env python3
"""Write the daily digests in memory/digest/ that the bot is given at session start.

One model call per day that has a raw log and no up-to-date digest, today
excluded (it is not over). Run nightly by scripts/distill.py; by hand to
backfill: on first run every past day is written.

Usage: python3 scripts/digest.py [--model M] [--limit N]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from assistant import config as config_module  # noqa: E402
from assistant import digest  # noqa: E402


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", help="default: the assistant's model")
    ap.add_argument("--limit", type=int, help="at most this many days")
    args = ap.parse_args()
    cfg = config_module.load()
    pending = digest.stale_days(cfg.root, __import__("datetime").date.today())
    print(f"{len(pending)} day(s) to digest")
    done = await digest.catch_up(cfg.root, digest.summariser(cfg, args.model), limit=args.limit)
    print(f"digested {len(done)}: {', '.join(done) or '-'}")
    return 0 if len(done) == len(pending[:args.limit]) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
