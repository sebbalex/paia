#!/usr/bin/env python3
"""Scraper tool and gate tests. Run: .venv/bin/python tests/test_scrape.py

No test framework, same reason as tests/test_policy.py.

The tools are exercised against a fake worker rather than a real one: what is
under test is the argument checking, the shape of what comes back to the model,
and the gate -- not HTTP.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from assistant.policy import Policy  # noqa: E402
from assistant.scrape import Scraper, ScraperError, build_tools  # noqa: E402

FAILURES: list[str] = []


def check(name: str, got, want, reason: str = "") -> None:
    if got != want:
        FAILURES.append(f"{name}: got {got!r}, want {want!r} {reason}".rstrip())
    print(f"  {'ok  ' if got == want else 'FAIL'} {name}")


def body(result: dict) -> str:
    return result["content"][0]["text"]


class FakeScraper(Scraper):
    """Records what would have gone over the wire and answers from a script."""

    def __init__(self, answers: dict[str, dict]) -> None:
        super().__init__("http://worker.invalid", "tok", 5)
        self.answers = answers
        self.calls: list[tuple[str, str, dict]] = []

    async def call(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        answer = self.answers.get(path)
        if isinstance(answer, Exception):
            raise answer
        return answer or {}


PRICED = {
    "url": "https://shop.example.com/p/1",
    "ok": True,
    "verdict": "read at tier http from json-ld",
    "robots_allowed": True,
    "fetch": {"tier": "http", "status": 200},
    "extracted": {
        "price": 59.9, "currency": "EUR", "availability": "in_stock",
        "title": "Garmin HRM-Dual", "source": "json-ld",
    },
}

BLOCKED = {
    "url": "https://wall.example.com/p",
    "ok": False,
    "verdict": "no tier opened the page -- http: HTTP 403",
    "needs_repair": False,
    "robots_allowed": True,
    "attempts": [{"tier": "http", "ok": False, "detail": "HTTP 403"}],
}


def tools_by_name(scraper: Scraper) -> dict:
    built = build_tools(scraper)
    out = {}
    for fn in built:
        name = getattr(fn, "name", None) or getattr(fn, "__name__", "")
        handler = getattr(fn, "handler", None) or fn
        out[name] = handler
    return out


def test_price_tool() -> None:
    print("page_price")
    scraper = FakeScraper({"/extract": PRICED})
    tools = tools_by_name(scraper)
    result = asyncio.run(tools["page_price"]({"url": "https://shop.example.com/p/1"}))
    text = body(result)
    check("price reported", "price: 59.9 EUR" in text, True, text[:120])
    check("provenance reported", "read from: json-ld" in text, True)
    check("tier reported", "opened at tier: http" in text, True)
    check("untrusted banner present", text.startswith("Data read from a web page"), True)
    check("default ceiling is human", scraper.calls[0][2]["json"]["max_tier"], "human")

    # The failure path must never read as a price.
    scraper = FakeScraper({"/extract": BLOCKED})
    tools = tools_by_name(scraper)
    text = body(asyncio.run(tools["page_price"]({"url": "https://wall.example.com/p"})))
    check("failure says not verified", "NOT READ" in text, True)
    check("failure explains why", "403" in text, True)

    # robots.txt is surfaced, not silently obeyed and not silently ignored.
    scraper = FakeScraper({"/extract": {**PRICED, "robots_allowed": False}})
    tools = tools_by_name(scraper)
    text = body(asyncio.run(tools["page_price"]({"url": "https://shop.example.com/p/1"})))
    check("robots disallow is reported", "robots.txt" in text, True)

    # Argument checking happens before anything leaves the process.
    scraper = FakeScraper({"/extract": PRICED})
    tools = tools_by_name(scraper)
    for bad, why in [
        ({"url": "http://shop.example.com/p"}, "plain http"),
        ({"url": ""}, "empty url"),
        ({"url": "https://x.com/", "max_tier": "root"}, "invented tier"),
        ({"url": "https://x.com/" + "a" * 3000}, "absurd url"),
    ]:
        result = asyncio.run(tools["page_price"](bad))
        check(f"refused: {why}", result.get("is_error"), True)
    check("nothing reached the worker", scraper.calls, [])

    # A worker that is down is reported, not raised.
    scraper = FakeScraper({"/extract": ScraperError("could not reach the worker")})
    tools = tools_by_name(scraper)
    result = asyncio.run(tools["page_price"]({"url": "https://x.com/p"}))
    check("worker down is an error result", result.get("is_error"), True)


def test_read_tool() -> None:
    print("page_read")
    html = ("<html><head><style>.a{color:red}</style></head><body>"
            "<h1>Spec</h1><script>track()</script><p>Weight 61 &amp; up</p>"
            "</body></html>")
    scraper = FakeScraper({"/fetch": {
        "url": "https://x.com/spec", "ok": True, "verdict": "read",
        "robots_allowed": True, "fetch": {"tier": "headless"}, "html": html,
    }})
    tools = tools_by_name(scraper)
    text = body(asyncio.run(tools["page_read"]({"url": "https://x.com/spec"})))
    check("script stripped", "track()" not in text, True)
    check("style stripped", "color:red" not in text, True)
    check("entities decoded", "61 & up" in text, True)
    check("heading kept", "Spec" in text, True)

    scraper = FakeScraper({"/fetch": {**BLOCKED, "html": ""}})
    tools = tools_by_name(scraper)
    text = body(asyncio.run(tools["page_read"]({"url": "https://wall.example.com/p"})))
    check("empty page says it did not open", "did not open" in text, True)


def test_sources_tool() -> None:
    print("page_sources")
    scraper = FakeScraper({"/status": {"domain": "bike24.de", "status": "headless"}})
    tools = tools_by_name(scraper)
    text = body(asyncio.run(tools["page_sources"]({"domain": "bike24.de"})))
    check("status returned", json.loads(text)["status"], "headless")
    result = asyncio.run(tools["page_sources"]({"domain": ""}))
    check("empty domain refused", result.get("is_error"), True)


def test_gate() -> None:
    print("policy gate")
    root = Path(tempfile.mkdtemp(prefix="scrape-")).resolve()
    (root / "state").mkdir()
    policy = Policy(root, frozenset())

    check("page_price allowed",
          policy.check("mcp__scrape__page_price", {"url": "https://x.com/p"}).allow, True)
    check("page_read allowed",
          policy.check("mcp__scrape__page_read", {"url": "https://x.com/p"}).allow, True)
    check("page_sources allowed",
          policy.check("mcp__scrape__page_sources", {"domain": "x.com"}).allow, True)
    check("plain http denied",
          policy.check("mcp__scrape__page_price", {"url": "http://x.com/p"}).allow, False)
    # Default-deny still holds for anything not pinned by name.
    check("unpinned scrape tool denied",
          policy.check("mcp__scrape__page_buy", {"url": "https://x.com/p"}).allow, False)
    check("unpinned scrape tool denied even without a url",
          policy.check("mcp__scrape__shell", {}).allow, False)

    # The allowlist, when set, narrows the scraper as well as WebFetch --
    # otherwise the narrower path is theatre.
    narrow = Policy(root, frozenset({"bike24.de"}))
    check("allowlisted domain allowed",
          narrow.check("mcp__scrape__page_price",
                       {"url": "https://www.bike24.de/p"}).allow, True)
    check("other domain denied",
          narrow.check("mcp__scrape__page_price",
                       {"url": "https://evil.com/p"}).allow, False)
    check("label boundary respected",
          narrow.check("mcp__scrape__page_price",
                       {"url": "https://evil-bike24.de/p"}).allow, False)

    # The SSRF half. This is the check that a worker-based fetch would
    # otherwise slip past entirely.
    for url, why in [
        ("https://127.0.0.1/p", "loopback"),
        ("https://169.254.169.254/p", "cloud metadata"),
        ("https://10.0.0.1/p", "private range"),
    ]:
        check(f"check_host refuses {why}", policy.check_host(url).allow, False)


def main() -> int:
    for suite in (test_price_tool, test_read_tool, test_sources_tool, test_gate):
        suite()
        print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED:")
        for failure in FAILURES:
            print("  -", failure)
        return 1
    print("all scrape tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
