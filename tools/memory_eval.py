#!/usr/bin/env python3
"""Score a memory retriever against the owner's own recall questions.

Replacing claude-mem is only safe if the replacement finds what was said at
least as well. This measures it: a fixed set of questions written from the
real raw log, each tied to the exchange (or wiki page) that answers it, and a
set of "what is going on lately" cases for the context given at session
start. The questions are the owner's data, so they live in the instance
(`memory/eval/`, gitignored here); this file is only the harness.

Units, so a gold id means the same thing to every retriever:

  raw:YYYY-MM-DDTHH:MM:SS   one exchange: an owner message and every
                            assistant entry after it, up to the next owner
                            message. Assistant entries with no owner message
                            before them that day (a routine) open their own.
  wiki:<page>               one page of memory/wiki/.
  day:YYYY-MM-DD            gold only: any exchange of that day counts.

Files (JSON Lines):

  recall.jsonl    {"id", "q", "category", "gold": [...], "now"?}
  context.jsonl   {"id", "now", "expect": ["regex", ...]}  every regex must
                  match (case-insensitive) the context the bot would get at
                  `now`.

A retriever is `module:function`:
  search(query: str, k: int, now: datetime | None) -> list[str]  (unit ids)
      scored on its first k exchange ids plus its first WIKI_K wiki ids
  context(now: datetime) -> str      optional, same module, for context.jsonl

Usage:
  tools/memory_eval.py --check                    validate gold ids only
  tools/memory_eval.py                            built-in keyword baseline
  tools/memory_eval.py --retriever pkg.mod:search
"""

from __future__ import annotations

import argparse
import importlib
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# The transport writes "owner" and "assistant"; older logs name the owner
# instead. Anything that is not the assistant is the owner speaking.
ASSISTANT_ROLE = "assistant"
# Only a role and a clock time: assistant replies carry their own "### X — Y"
# markdown headings, which must not split an exchange.
HEADER = re.compile(r"^### (\w+) — (\d\d:\d\d:\d\d)\s*$", re.M)

# The bar a replacement has to clear before claude-mem goes.
PASS_RECALL_AT_5 = 0.90
PASS_CATEGORY_AT_5 = 0.80
PASS_CONTEXT = 0.75
# Wiki pages a retriever may return beside its k exchanges.
WIKI_K = 2


@dataclass
class Unit:
    id: str
    day: str
    text: str


def exchanges(raw_dir: Path) -> list[Unit]:
    units: list[Unit] = []
    for path in sorted(raw_dir.glob("*.md")):
        day = path.stem
        text = path.read_text(encoding="utf-8")
        heads = list(HEADER.finditer(text))
        current: Unit | None = None
        for i, head in enumerate(heads):
            end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
            role, clock = head.group(1), head.group(2)
            body = text[head.end():end].strip()
            if role != ASSISTANT_ROLE or current is None:
                current = Unit(f"raw:{day}T{clock}", day, "")
                units.append(current)
            current.text += f"\n[{role} {clock}] {body}"
    return units


def wiki_pages(wiki_dir: Path) -> list[Unit]:
    return [Unit(f"wiki:{p.stem}", "", p.read_text(encoding="utf-8"))
            for p in sorted(wiki_dir.glob("*.md")) if p.stem not in ("index", "log")]


def corpus(root: Path) -> list[Unit]:
    return exchanges(root / "memory" / "raw") + wiki_pages(root / "memory" / "wiki")


def _load(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("//")]


def _now(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def hit(gold: list[str], found: str) -> bool:
    for g in gold:
        if g.startswith("day:"):
            if found.startswith(f"raw:{g[4:]}T"):
                return True
        elif g == found:
            return True
    return False


def check(questions: list[dict], cases: list[dict], units: list[Unit]) -> list[str]:
    ids = {u.id for u in units}
    days = {u.day for u in units}
    problems = []
    seen = set()
    for q in questions:
        if q["id"] in seen:
            problems.append(f"{q['id']}: duplicate id")
        seen.add(q["id"])
        if not q.get("gold"):
            problems.append(f"{q['id']}: no gold")
        for g in q.get("gold", []):
            ok = g[4:] in days if g.startswith("day:") else g in ids
            if not ok:
                problems.append(f"{q['id']}: gold {g} is not a unit in the corpus")
    for c in cases:
        if not c.get("expect"):
            problems.append(f"{c['id']}: no expect")
        for pattern in c.get("expect", []):
            try:
                re.compile(pattern)
            except re.error as exc:
                problems.append(f"{c['id']}: bad regex {pattern!r}: {exc}")
    return problems


# -- the floor: what plain keyword matching gets -----------------------------

_WORD = re.compile(r"\w{3,}", re.U)


def keyword_retriever(units: list[Unit]):
    """Count shared words. No stemming, no meaning: the number to beat."""
    bags = [(u.id, set(_WORD.findall(u.text.lower()))) for u in units]

    def search(query: str, k: int, now: datetime | None = None) -> list[str]:
        words = set(_WORD.findall(query.lower()))
        scored = [(len(words & bag), uid) for uid, bag in bags]
        scored = [s for s in scored if s[0] > 0]
        scored.sort(key=lambda s: -s[0])
        return [uid for _, uid in scored[:k]]

    return search


def _import(spec: str):
    module, _, name = spec.partition(":")
    mod = importlib.import_module(module)
    return getattr(mod, name or "search"), getattr(mod, "context", None)


def run(questions, cases, search, context, k: int = 5) -> bool:
    by_cat: dict[str, list[tuple[bool, bool, float]]] = defaultdict(list)
    misses = []
    for q in questions:
        found = search(q["q"], k, _now(q.get("now")))
        # Exchanges and wiki pages are ranked apart: k of the first, WIKI_K of
        # the second, the way the bot is shown them.
        found = ([f for f in found if not f.startswith("wiki:")][:k]
                 + [f for f in found if f.startswith("wiki:")][:WIKI_K])
        rank = next((i for i, f in enumerate(found) if hit(q["gold"], f)), None)
        by_cat[q["category"]].append((rank == 0, rank is not None,
                                      1 / (rank + 1) if rank is not None else 0.0))
        if rank is None:
            misses.append(f"  miss {q['id']} [{q['category']}] {q['q']}")

    print(f"{'category':<12} {'n':>3} {'@1':>6} {'@5':>6} {'MRR':>6}")
    total = [r for rows in by_cat.values() for r in rows]
    ok = True
    for cat in sorted(by_cat) + ["ALL"]:
        rows = total if cat == "ALL" else by_cat[cat]
        at1 = sum(r[0] for r in rows) / len(rows)
        at5 = sum(r[1] for r in rows) / len(rows)
        mrr = sum(r[2] for r in rows) / len(rows)
        bar = PASS_RECALL_AT_5 if cat == "ALL" else PASS_CATEGORY_AT_5
        flag = "" if at5 >= bar else "  < bar"
        ok &= at5 >= bar
        print(f"{cat:<12} {len(rows):>3} {at1:>6.2f} {at5:>6.2f} {mrr:>6.2f}{flag}")
    if misses:
        print("\n".join(["", *misses]))

    if cases:
        passed = 0
        print()
        for c in cases:
            text = context(datetime.fromisoformat(c["now"])) if context else ""
            missing = [p for p in c["expect"] if not re.search(p, text, re.I)]
            passed += not missing
            print(f"  {'ok  ' if not missing else 'FAIL'} context {c['id']}"
                  + (f": missing {missing}" if missing else ""))
        rate = passed / len(cases)
        ok &= rate >= PASS_CONTEXT
        print(f"context {passed}/{len(cases)} = {rate:.2f}"
              + ("" if rate >= PASS_CONTEXT else "  < bar"))

    print(f"\n{'PASS' if ok else 'FAIL'} (bar: @5 >= {PASS_RECALL_AT_5} overall, "
          f">= {PASS_CATEGORY_AT_5} per category, context >= {PASS_CONTEXT})")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", type=Path, default=ROOT)
    ap.add_argument("--retriever", help="module:function; default is the keyword floor")
    ap.add_argument("--check", action="store_true", help="validate gold ids and stop")
    ap.add_argument("-k", type=int, default=5)
    args = ap.parse_args()

    eval_dir = args.root / "memory" / "eval"
    questions = _load(eval_dir / "recall.jsonl")
    cases = _load(eval_dir / "context.jsonl")
    if not questions:
        print(f"no questions in {eval_dir / 'recall.jsonl'}")
        return 1
    units = corpus(args.root)
    problems = check(questions, cases, units)
    print(f"{len(units)} units, {len(questions)} questions, {len(cases)} context cases")
    if problems:
        print("\n".join(problems))
        return 1
    if args.check:
        print("gold ok")
        return 0

    if args.retriever:
        sys.path.insert(0, str(args.root))
        search, context = _import(args.retriever)
    else:
        search, context = keyword_retriever(units), None
        print("retriever: keyword floor (no context)")
    return 0 if run(questions, cases, search, context, args.k) else 1


if __name__ == "__main__":
    sys.exit(main())
