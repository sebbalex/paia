#!/usr/bin/env python3
"""Markdown -> Telegram HTML conversion.

Run: .venv/bin/python tests/test_format.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from assistant.format import to_telegram_html  # noqa: E402
from assistant.telegram import _split  # noqa: E402

FAILURES: list[str] = []


def check(name: str, got: str, must_contain=(), must_not_contain=()) -> None:
    ok = True
    for needle in must_contain:
        if needle not in got:
            FAILURES.append(f"{name}: missing {needle!r} in {got!r}")
            ok = False
    for needle in must_not_contain:
        if needle in got:
            FAILURES.append(f"{name}: found {needle!r} in {got!r}")
            ok = False
    print(f"  {'ok  ' if ok else 'FAIL'} {name}")


def main() -> int:
    print("\ninline")
    check("bold", to_telegram_html("**hello**"),
          ["<b>hello</b>"], ["**"])
    check("italic", to_telegram_html("some *emphasis* here"),
          ["<i>emphasis</i>"])
    check("inline code", to_telegram_html("use `rm -rf`"),
          ["<code>rm -rf</code>"], ["`"])
    check("link", to_telegram_html("[doc](https://example.com)"),
          ['<a href="https://example.com">doc</a>'])
    check("heading", to_telegram_html("## Memory"), ["<b>Memory</b>"], ["#"])
    check("list", to_telegram_html("- first\n- second"), ["• first"], ["- first"])

    print("\nthe case that breaks MarkdownV2")
    # snake_case and the double underscores of MCP tools must not turn italic
    check("snake_case intact", to_telegram_html("open test_policy.py now"),
          ["test_policy.py"], ["<i>"])
    check("MCP tool intact", to_telegram_html("mcp__mcp-search__search"),
          ["mcp__mcp-search__search"], ["<i>"])

    print("\nblock code")
    out = to_telegram_html("```bash\nrm -f *.md\n```")
    check("fence with language", out,
          ['<pre><code class="language-bash">', "rm -f *.md"], ["```"])
    check("markup inside code left alone",
          to_telegram_html("```\n**not bold**\n```"),
          ["**not bold**"], ["<b>"])

    print("\nescaping")
    check("html escaped", to_telegram_html("a < b & c > d"),
          ["&lt;", "&amp;", "&gt;"])
    check("injected tag neutralised",
          to_telegram_html("<script>alert(1)</script>"),
          ["&lt;script&gt;"], ["<script>"])

    print("\ntables")
    out = to_telegram_html("| a | b |\n|---|---|\n| 1 | 2 |")
    check("table as a fixed block", out, ["<pre>", "a", "b"], ["|---|"])

    print("\nsplit and fences")
    long_code = "```python\n" + "x = 1\n" * 3000 + "```"
    chunks = _split(long_code, 4096)
    balanced = all(c.count("```") % 2 == 0 for c in chunks)
    check("every chunk has balanced fences",
          "ok" if balanced else "unbalanced", ["ok"])
    check("first chunk closed", chunks[0][-3:], ["```"])

    print("\nfallback when Telegram rejects the markup")
    import asyncio

    from assistant.telegram import Telegram

    tg = Telegram("token", frozenset({1}))
    calls: list[dict] = []

    async def fake_call(method, **params):
        calls.append(params)
        # first attempt (with parse_mode) rejected, as Telegram would do with
        # entities it cannot interpret
        if "parse_mode" in params:
            raise RuntimeError("can't parse entities")
        return {}

    tg._call = fake_call
    asyncio.run(tg.send(1, "**hello** `x`"))
    check("retries", "two" if len(calls) == 2 else f"{len(calls)}", ["two"])
    check("the second send has no markup",
          "clean" if "parse_mode" not in calls[-1] else "still html", ["clean"])
    check("the text gets there anyway", calls[-1].get("text", ""), ["hello"])

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED:")
        for f in FAILURES:
            print("  -", f)
        return 1
    print("formatting ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
