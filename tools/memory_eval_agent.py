#!/usr/bin/env python3
"""End-to-end recall: the real model, the real memory_search tool, the owner's questions.

tools/memory_eval.py scores the retriever on the question as written. In use,
the model writes the queries -- rephrases, translates, adds a date -- the way
it did with claude-mem. This measures that: each question goes to the model
with memory_search as its only tool and the clock set to the question's
`now`, and a question counts as found when any result the tool showed the
model is gold (exchanges and wiki pages alike, as in memory_eval.py).

Costs one short model turn per question, on the credential the SDK finds
(the local `claude` login, or CLAUDE_CODE_OAUTH_TOKEN / ANTHROPIC_API_KEY).

Usage:
  .venv/bin/python tools/memory_eval_agent.py [--model M] [--only id,id] [--words-only]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from claude_agent_sdk import (  # noqa: E402
    AssistantMessage,
    ClaudeAgentOptions,
    TextBlock,
    ToolUseBlock,
    create_sdk_mcp_server,
    query,
)

import memory_eval as E  # noqa: E402
from assistant.memory_tools import build_tools  # noqa: E402
from assistant.recall import Recall  # noqa: E402

PROMPT = """You are the owner's personal assistant. It is {now}. The owner asks \
you something about your past conversations. Use memory_search to find it -- as \
many searches as you need, up to three -- then answer in one short line in the \
owner's language. If you cannot find it, say so."""


class Recorder:
    """Wraps Recall.search_many to keep every id the model was shown."""

    def __init__(self, recall: Recall):
        self.recall = recall
        self.shown: list[list[str]] = []

    def search_many(self, queries, k=5, now=None):
        hits = self.recall.search_many(queries, k, now)
        self.shown.append([h["id"] for h in hits])
        return hits

    def __getattr__(self, name):
        return getattr(self.recall, name)


async def ask(q: dict, recall: Recall, model: str | None, now: datetime) -> dict:
    rec = Recorder(recall)
    server = create_sdk_mcp_server(name="memory", version="1.0.0",
                                   tools=build_tools(rec, clock=lambda: now))
    options = ClaudeAgentOptions(
        tools=[],
        mcp_servers={"memory": server},
        allowed_tools=["mcp__memory__memory_search"],
        system_prompt=PROMPT.format(now=now.strftime("%A %Y-%m-%d %H:%M")),
        setting_sources=[],
        max_turns=6,
        model=model,
        cwd=str(ROOT),
    )
    queries, answer = [], ""
    async for msg in query(prompt=q["q"], options=options):
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, ToolUseBlock):
                    queries.append(block.input.get("queries"))
                elif isinstance(block, TextBlock):
                    answer = block.text.strip()
    shown = [i for run in rec.shown for i in run]
    first = rec.shown[0] if rec.shown else []
    return {"id": q["id"], "category": q["category"], "calls": len(rec.shown),
            "found": any(E.hit(q["gold"], i) for i in shown),
            "first_call": any(E.hit(q["gold"], i) for i in first),
            "queries": queries, "answer": answer}


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model")
    ap.add_argument("--only")
    ap.add_argument("--words-only", action="store_true")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    questions = E._load(ROOT / "memory" / "eval" / "recall.jsonl")
    if args.only:
        wanted = set(args.only.split(","))
        questions = [q for q in questions if q["id"] in wanted]
    name = "words" if args.words_only else "multilingual-e5-small"
    recall = Recall(ROOT, ROOT / "state" / f"recall-{name}.db", ROOT / "models",
                    embed=not args.words_only)
    recall.refresh()
    last = recall.db.execute("SELECT max(ts) FROM units WHERE kind = 'raw'").fetchone()[0]
    default_now = datetime.fromisoformat(last) + timedelta(hours=1)

    rows = []
    for q in questions:
        now = datetime.fromisoformat(q["now"]) if q.get("now") else default_now
        try:
            row = await ask(q, recall, args.model, now)
        except Exception as exc:  # noqa: BLE001 - one failed turn is a miss, not a stop
            row = {"id": q["id"], "category": q["category"], "calls": 0, "found": False,
                   "first_call": False, "queries": [], "answer": f"ERROR {exc}"}
        rows.append(row)
        print(f"  {'ok  ' if row['found'] else 'miss'} {row['id']} calls={row['calls']} "
              f"{json.dumps(row['queries'], ensure_ascii=False)[:160]}", flush=True)

    by = defaultdict(list)
    for r in rows:
        by[r["category"]].append(r)
    print(f"\n{'category':<12} {'n':>3} {'found':>6} {'1st call':>9} {'calls':>6}")
    for cat in sorted(by) + ["ALL"]:
        rs = rows if cat == "ALL" else by[cat]
        print(f"{cat:<12} {len(rs):>3} {sum(r['found'] for r in rs) / len(rs):>6.2f} "
              f"{sum(r['first_call'] for r in rs) / len(rs):>9.2f} "
              f"{sum(r['calls'] for r in rs) / len(rs):>6.1f}")
    if args.out:
        args.out.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
