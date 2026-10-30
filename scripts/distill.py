#!/usr/bin/env python3
"""Nightly distillation: promote durable facts from the raw log into the wiki.

Without this the raw log grows forever and the wiki stays empty, which means
the assistant's only real memory is claude-mem's derived index — exactly the
thing ROT.md says not to depend on. Run it from a systemd timer.

Usage: python3 scripts/distill.py [YYYY-MM-DD]   (default: today)
"""

from __future__ import annotations

import asyncio
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from claude_agent_sdk import (  # noqa: E402
    AssistantMessage,
    ClaudeAgentOptions,
    TextBlock,
    query,
)

from assistant import config as config_module  # noqa: E402

PROMPT = """Distil today's conversation log into durable memory.

1. Read `{log}`.
2. For each fact in it that is durable and worth keeping — decisions, stable
   preferences, commitments with dates, names and identifiers, things that
   would cost me effort to re-derive — find or create the matching page under
   `memory/wiki/`.
3. Apply the wiki rules in ROT.md exactly: one entity per kebab-case file,
   frontmatter with `name` and `updated`, newest facts first, absolute dates,
   `[[links]]` between related pages.
4. When a new fact contradicts an existing line, replace that line. Do not let
   both stand.
5. Skip anything transient, anything already recorded, and anything that only
   made sense inside one exchange.
6. Update `memory/wiki/index.md`: one line per page, grouped by theme. It
   must list exactly the pages that really exist -- no entries for pages never
   written, no pages missing from the index (`proposals/` excepted, see 9).
7. Add an entry at the top of `memory/wiki/log.md` in the format
   `## [YYYY-MM-DD HH:MM] wiki | what you changed`. This one accumulates: do
   not replace earlier entries. If the file goes past three months, move the
   oldest ones to the bottom under `## Archive`.
8. Write everything in English, whatever language the log is in.
9. Leave `memory/wiki/proposals/` alone: those are brief changes waiting
   for the owner's /accept, not wiki pages. Do not edit them, do not list
   them in the index, do not create new ones.

Then reply with a one-line summary: how many pages you created and updated.
If the log has nothing worth keeping, write nothing and say so."""


async def main() -> int:
    cfg = config_module.load()
    day = sys.argv[1] if len(sys.argv) > 1 else date.today().isoformat()
    log_path = cfg.raw_memory_dir / f"{day}.md"
    if not log_path.exists():
        print(f"no log for {day}, nothing to distil")
        return 0

    options = ClaudeAgentOptions(
        cwd=str(cfg.root),
        model=cfg.model,
        setting_sources=["project", "local"],
        system_prompt={"type": "preset", "preset": "claude_code"},
        allowed_tools=["Read", "Write", "Edit", "Glob", "Grep"],
        disallowed_tools=list(cfg.disallowed_tools),
        permission_mode="acceptEdits",
    )

    prompt = PROMPT.format(log=log_path.relative_to(cfg.root))
    if cfg.backend == "openrouter":
        # No CLI to run it on: the OpenRouter loop, with the file tools only,
        # and the policy gate in front of them -- stricter than acceptEdits.
        from assistant.openrouter import run_once

        turn = await run_once(cfg, prompt, ("Read", "Write", "Edit", "Glob", "Grep"))
        print(turn.text)
        return 0 if turn.ok else 1

    async for message in query(prompt=prompt, options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock) and block.text.strip():
                    print(block.text.strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
