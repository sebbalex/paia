"""memory_search: recall over the raw log and the wiki, as a tool.

The in-process replacement for claude-mem's search. One tool, read-only, over
files the assistant already owns: memory/raw/ (every exchange, written by the
transport) and memory/wiki/ (the distilled facts). The index behind it is
assistant/recall.py; tools/memory_eval.py measures it.

Search and embedding are CPU work, so they run in a worker thread: a recall
must not stall the Telegram loop.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Annotated, Any

from claude_agent_sdk import tool

from .recall import Recall

MAX_QUERIES = 3
MAX_K = 10


def _text(body: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": body}]}


def render(hits: list[dict]) -> str:
    if not hits:
        return ("No match in the raw log or the wiki. That means nothing was found "
                "with these phrasings -- not that it was never said. Try other words, "
                "the other language, or a date.")
    lines = []
    for h in hits:
        if h["id"].startswith("wiki:"):
            name = h["id"][5:]
            lines.append(f"## wiki [[{name}]] (updated {h['when'][:10]}) -- "
                         f"memory/wiki/{name}.md\n{h['excerpt']}")
        else:
            day, clock = h["when"][:10], h["when"][11:16]
            lines.append(f"## exchange {day} {clock} -- memory/raw/{day}.md\n{h['excerpt']}")
    return "\n\n".join(lines)


def build_tools(recall: Recall, clock=datetime.now) -> list[Any]:
    @tool(
        "memory_search",
        "Search everything the owner and you have said (memory/raw/, one entry per "
        "exchange) and the wiki (memory/wiki/). Use it before saying you don't "
        "know or don't remember, when the owner refers to something from before "
        "this conversation ('come ti avevo detto', 'quella volta', 'the hotel we "
        "booked'), and before asking them something they may already have told "
        "you. Matches both exact words (names, codes, prices) and meaning, in "
        "Italian and English. Give 1-3 phrasings of the same question: the "
        "owner's own words, a short keyword form, and the other language if the "
        "log is likely in it. Put dates in words or numbers ('21 agosto', "
        "'ieri', 'last week', '30/09') -- they restrict the days searched; "
        "'l'ultima volta' / 'last time' ranks by date. Returns up to k exchanges "
        "plus up to two wiki pages, each with its date and file. The wiki is "
        "the truth when the two disagree; a hit is a lead to open the file for, "
        "not a fact by itself. Read-only.",
        {
            "queries": Annotated[list[str], "1-3 phrasings of one question."],
            "k": Annotated[int, "Exchanges to return, 1-10. Default 5."],
        },
    )
    async def memory_search(args: dict[str, Any]) -> dict[str, Any]:
        queries = args.get("queries") or []
        if isinstance(queries, str):
            # Models sometimes send the list JSON-encoded; take it apart
            # rather than search for '["a", "b"]' as one phrase.
            try:
                decoded = json.loads(queries)
            except ValueError:
                decoded = None
            queries = decoded if isinstance(decoded, list) else [queries]
        queries = [str(q).strip() for q in queries if str(q).strip()][:MAX_QUERIES]
        if not queries:
            return {**_text("give at least one query"), "is_error": True}
        try:
            k = max(1, min(MAX_K, int(args.get("k") or 5)))
        except (TypeError, ValueError):
            k = 5
        hits = await asyncio.to_thread(recall.search_many, queries, k, clock())
        return _text(render(hits))

    return [memory_search]
