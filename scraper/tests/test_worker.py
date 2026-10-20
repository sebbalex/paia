#!/usr/bin/env python3
"""Worker tests. Run: .venv/bin/python scraper/tests/test_worker.py

No test framework on purpose -- same reason as tests/test_policy.py: this must
stay runnable on the server with nothing installed but the runtime deps. The
whole extraction chain except step 4 is stdlib, so most of this runs in a bare
checkout with no browser and no curl_cffi.
"""

from __future__ import annotations

import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from scraper import safety  # noqa: E402
from scraper.extract import extract  # noqa: E402
from scraper.ladder import Ladder  # noqa: E402
from scraper.money import MoneyError, parse, parse_amount, plausible  # noqa: E402
from scraper.recipes import Recipes, domain_of  # noqa: E402
from scraper.repair import Budget, candidates, repair  # noqa: E402
from scraper.tiers.base import Fetched  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FAILURES: list[str] = []


def check(name: str, got, want, reason: str = "") -> None:
    if got != want:
        FAILURES.append(f"{name}: got {got!r}, want {want!r} {reason}".rstrip())
    print(f"  {'ok  ' if got == want else 'FAIL'} {name}")


def refuses(name: str, fn, *args, **kwargs) -> None:
    try:
        result = fn(*args, **kwargs)
    except (MoneyError, safety.Refused, ValueError) as exc:
        print(f"  ok   {name} ({type(exc).__name__})")
        return
    FAILURES.append(f"{name}: should have refused, returned {result!r}")
    print(f"  FAIL {name}")


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# -- money ----------------------------------------------------------------

def test_money() -> None:
    print("money")
    # The pair that must never be confused: the rightmost separator decides.
    check("de/it 1.234,56", parse_amount("1.234,56"), 1234.56)
    check("us 1,234.56", parse_amount("1,234.56"), 1234.56)
    check("nbsp grouping", parse_amount("1 234,56"), 1234.56)
    check("two decimals is money", parse_amount("39,90"), 39.90)
    check("one decimal is money", parse_amount("39.9"), 39.9)
    # The factor-of-a-thousand case. Three trailing digits group thousands.
    check("1.299 is one thousand", parse_amount("1.299"), 1299.0)
    check("1,299 is one thousand", parse_amount("1,299"), 1299.0)
    check("repeated grouping", parse_amount("1.234.567"), 1234567.0)
    check("embedded in prose", parse_amount("ab 1.234,56 € inkl. MwSt"), 1234.56)

    check("currency from symbol", parse("39,90 €").currency, "EUR")
    check("currency from code", parse("USD 39.90").currency, "USD")
    check("currency falls back", parse("39,90", "EUR").currency, "EUR")
    check("page beats recipe", parse("$39.90", "EUR").currency, "USD")

    refuses("four trailing digits refused", parse_amount, "1.2345")
    refuses("inconsistent grouping refused", parse_amount, "1.23,456")
    refuses("no digits refused", parse_amount, "Price on request")

    # tradeinn.com's placeholder on 2026-09-04. It parses; it is not a price.
    check("zero parses", parse_amount("0"), 0.0)
    check("zero is not plausible", plausible(0.0), False)
    check("review count is plausible-shaped", plausible(482.0), True)


# -- extraction -----------------------------------------------------------

def test_extract() -> None:
    print("extraction chain")
    found = extract(fixture("jsonld_shop.html"))
    check("json-ld wins", found.source, "json-ld")
    check("json-ld price", found.price.amount, 59.90)
    check("json-ld currency", found.price.currency, "EUR")
    check("json-ld availability", found.availability, "in_stock")
    check("json-ld title", found.title, "Garmin HRM-Dual")
    # og:price says 61.00 and is stale. Structured data outranks it, which is
    # the entire reason for the ordering.
    check("meta did not win", found.price.amount != 61.00, True)

    found = extract(fixture("microdata_shop.html"))
    check("microdata source", found.source, "microdata")
    check("microdata price", found.price.amount, 1234.56)
    check("microdata availability", found.availability, "out_of_stock")

    # Placeholder: JSON-LD present, price is 0, so no step may claim success.
    found = extract(fixture("placeholder_shop.html"))
    check("placeholder yields no price", found.ok, False)
    check("placeholder names no source", found.source, None)

    # Selector step. selectolax is container-side; skip cleanly without it.
    recipe = {
        "currency": "EUR",
        "extract": {
            "price": {"css": ".product-price .amount"},
            "availability": {"css": ".stock"},
        },
    }
    found = extract(fixture("selectors_shop.html"), recipe)
    try:
        import selectolax  # noqa: F401
    except ImportError:
        check("selectors skipped without selectolax", found.ok, False)
        print("  skip selector extraction (selectolax not installed)")
    else:
        check("selector price", found.price.amount, 1299.0)
        check("selector source", found.source, "selectors")

    # A page that is not HTML at all must not raise.
    found = extract("\x00\x01 not html <<<>>>")
    check("garbage does not raise", found.ok, False)


# -- recipes --------------------------------------------------------------

def test_recipes() -> None:
    print("recipe store")
    store = Recipes(Path(tempfile.mkdtemp(prefix="recipes-")))
    check("www stripped", domain_of("https://www.bike24.de/p/1"), "bike24.de")
    check("port stripped", domain_of("https://bike24.de:443/p"), "bike24.de")

    recipe = store.get("bike24.de")
    check("new recipe starts at tier 1", recipe["tier"], "http")
    recipe = store.record(recipe, url="https://bike24.de/p/1", tier="headless",
                          ok=True, source="json-ld")
    check("success sets the start tier", store.get("bike24.de")["tier"], "headless")

    for _ in range(3):
        recipe = store.record(recipe, url="https://bike24.de/p/1", tier=None,
                              ok=False, note="403")
    saved = store.get("bike24.de")
    check("three failures is blocked", saved["status"], "blocked")
    # The ratchet: failure must not demote the tier that once worked.
    check("failure does not demote", saved["tier"], "headless")

    # The tradeinn contradiction: one page differs from the domain.
    store.note_override("https://tradeinn.com/x/p/9?utm_source=x",
                        tier="headful", note="placeholder price")
    check("override applies", store.for_url("https://tradeinn.com/x/p/9")["tier"],
          "headful")
    check("domain default untouched", store.get("tradeinn.com")["tier"], "http")
    check("query ignored in the key",
          store.for_url("https://tradeinn.com/x/p/9?ref=other")["tier"], "headful")

    store.save(store.get("bike24.de"), keep_previous=True)
    check("rollback finds the previous file", store.rollback("bike24.de"), True)
    refuses("path traversal refused", store.get, "../../etc/passwd")


# -- safety ---------------------------------------------------------------

def test_safety() -> None:
    print("safety")
    refuses("metadata endpoint", safety.check, "http://169.254.169.254/")
    # The same host over https, so the scheme check cannot be what stops it.
    refuses("metadata over https", safety.check, "https://169.254.169.254/")
    refuses("loopback", safety.check, "https://127.0.0.1:8123/")
    refuses("private range", safety.check, "https://10.0.0.1/")
    refuses("link local", safety.check, "https://169.254.1.1/")
    refuses("plain http", safety.check, "http://example.com/")
    refuses("ftp", safety.check, "ftp://example.com/")
    refuses("no host", safety.check, "https:///path")
    refuses("post refused", safety.check, "https://example.com/", method="POST")
    # A redirect to plain http is allowed to be followed, but the address
    # check must still apply -- this is the hop that would otherwise slip past.
    refuses("http redirect to metadata still refused", safety.check,
            "http://169.254.169.254/", allow_http=True)

    target = safety.check_url("https://example.com/p", allow_http=False)
    check("public url parses", target.host, "example.com")

    limiter = safety.RateLimiter(default_seconds=0)
    check("first hit does not wait", limiter.wait("example.com") == 0.0, True)


# -- ladder ---------------------------------------------------------------

class _FakeTier:
    """Stands in for a tier module. AVAILABLE plus fetch() is the whole contract."""

    AVAILABLE = True
    _WHY = ""

    def __init__(self, name: str, status: int, html: str) -> None:
        self.name, self.status, self.html = name, status, html
        self.calls = 0

    def fetch(self, url, recipe=None, **kw):
        self.calls += 1
        return Fetched(url=url, status=self.status, html=self.html, tier=self.name)


def test_ladder() -> None:
    print("ladder")
    import scraper.ladder as ladder_module

    store = Recipes(Path(tempfile.mkdtemp(prefix="ladder-")))
    page = fixture("jsonld_shop.html")
    tiers = {
        "http": _FakeTier("http", 403, ""),
        "headless": _FakeTier("headless", 200, page),
        "headful": _FakeTier("headful", 200, page),
        "human": _FakeTier("human", 200, page),
    }
    # Only the syntax half of the real check: these hostnames are invented, so
    # resolving them would fail for a reason that has nothing to do with what
    # is under test. The address half has its own tests in test_safety.
    def check_syntax_only(url, **kw):
        return safety.check_url(url, **kw)

    original = ladder_module._module
    ladder_module._module = lambda name: tiers.get(name)
    try:
        ladder = Ladder(store, check=check_syntax_only)
        outcome = ladder.run("https://shop.example.com/p/1")
        check("climbs past a 403", outcome.ok, True)
        check("stops at the tier that worked", outcome.fetched.tier, "headless")
        check("heavier tiers not spent", tiers["headful"].calls, 0)
        check("price came back", outcome.found.price.amount, 59.90)
        check("winning tier recorded",
              store.get("shop.example.com")["tier"], "headless")

        # Second run starts where the first succeeded: tier 1 is not re-spent.
        before = tiers["http"].calls
        ladder.run("https://shop.example.com/p/1")
        check("start tier remembered", tiers["http"].calls, before)

        # A page that opens but yields no price: the ladder still climbs, since
        # a shell that renders under a browser looks the same from here. Only
        # when the top of the ladder also opens it and still finds nothing is
        # the diagnosis "repair the selectors" rather than "the shop blocks us".
        placeholder = fixture("placeholder_shop.html")
        tiers["headless"] = _FakeTier("headless", 200, placeholder)
        tiers["headful"] = _FakeTier("headful", 200, fixture("jsonld_shop.html"))
        outcome = ladder.run("https://shop.example.com/p/2")
        check("climbs when a page opens but reads empty", outcome.ok, True)
        check("climbed to the next tier", outcome.fetched.tier, "headful")

        for name in tiers:
            tiers[name] = _FakeTier(name, 200, placeholder)
        outcome = ladder.run("https://empty.example.com/p")
        check("unreadable at every tier is not a block", outcome.ok, False)
        check("unreadable at every tier asks for repair", outcome.needs_repair, True)
        check("repair verdict says so", "repair" in outcome.verdict, True)

        # Everything refuses: no repair claim, and the verdict names the tiers.
        for name in tiers:
            tiers[name] = _FakeTier(name, 403, "")
        outcome = ladder.run("https://wall.example.com/p")
        check("total block is not a repair job", outcome.needs_repair, False)
        check("verdict names what was tried", "403" in outcome.verdict, True)

        # Blocked domains cool off for 14 days, and force overrides it.
        recipe = store.get("wall.example.com")
        recipe.update(status="blocked", last_seen=date.today().isoformat())
        store.save(recipe)
        attempts = tiers["http"].calls
        held = ladder.run("https://wall.example.com/p")
        check("blocked domain is held", tiers["http"].calls, attempts)
        check("hold is explained", "next retry no earlier" in held.verdict, True)
        ladder.run("https://wall.example.com/p", force=True)
        check("force overrides the hold", tiers["http"].calls > attempts, True)

        # And the hold expires rather than being permanent.
        recipe = store.get("wall.example.com")
        recipe.update(status="blocked",
                      last_seen=(date.today() - timedelta(days=20)).isoformat())
        store.save(recipe)
        attempts = tiers["http"].calls
        ladder.run("https://wall.example.com/p")
        check("expired hold retries", tiers["http"].calls > attempts, True)

        # max_tier is a ceiling the caller sets and the ladder respects.
        for name in tiers:
            tiers[name] = _FakeTier(name, 403, "")
        ladder.run("https://ceiling.example.com/p", max_tier="http")
        check("ceiling honoured", tiers["headless"].calls, 0)
    finally:
        ladder_module._module = original

    # SSRF is refused by the ladder before any tier is consulted.
    refuses("ladder refuses a private target", Ladder(store).run,
            "https://127.0.0.1/p")


# -- repair ---------------------------------------------------------------

def test_repair() -> None:
    print("repair")
    page = fixture("selectors_shop.html")
    store = Recipes(Path(tempfile.mkdtemp(prefix="repair-")))
    recipe = store.get("shop.example.com")
    recipe["currency"] = "EUR"
    # Saved first, the way the ladder leaves it: record() persists the failure
    # before repair is ever reached, so there is always a file to roll back to.
    store.save(recipe)

    found = candidates(page)
    check("pruning finds the price text", len(found) >= 1, True)
    check("pruning keeps the path", "span.amount" in found[0]["path"], True)
    # The model must not be shown the whole page.
    check("pruned much smaller than the page",
          len(str(found)) < len(page), True)

    try:
        import selectolax  # noqa: F401
    except ImportError:
        print("  skip repair validation (selectolax not installed)")
        return

    def answers(text):
        return '{"price": ".product-price .amount", "reasoning": "the amount span"}'

    result = repair(page, recipe, store, ask=answers)
    check("good selector accepted", result.ok, True)
    check("validated against the page", result.price, 1299.0)
    saved = store.get("shop.example.com")
    check("recipe committed", saved["extract"]["price"]["css"], ".product-price .amount")
    check("previous kept for rollback",
          (store.directory / "shop.example.com.json.prev").exists(), True)

    # Everything below must leave the recipe alone.
    before = store.get("shop.example.com")["extract"]

    def matches_nothing(text):
        return '{"price": ".does-not-exist"}'

    result = repair(page, recipe, store, ask=matches_nothing)
    check("selector matching nothing refused", result.ok, False)

    def matches_reviews(text):
        return '{"price": ".reviews"}'

    result = repair(page, recipe, store, ask=matches_reviews)
    check("selector matching a review count refused", result.ok, False)

    def reports_a_price(text):
        return '{"price": 1299.0}'

    result = repair(page, recipe, store, ask=reports_a_price)
    check("a number instead of a selector refused", result.ok, False)

    def not_json(text):
        return "I think the price is 1.299 euro."

    result = repair(page, recipe, store, ask=not_json)
    check("prose reply refused", result.ok, False)

    def gives_up(text):
        return '{"price": null}'

    result = repair(page, recipe, store, ask=gives_up)
    check("an honest null refused", result.ok, False)
    check("no bad repair was committed",
          store.get("shop.example.com")["extract"], before)

    # Budget.
    budget = Budget(Path(tempfile.mkdtemp(prefix="budget-")) / "b.json", limit=2)
    check("budget starts full", budget.remaining(), 2)
    check("first spend allowed", budget.spend(), True)
    check("second spend allowed", budget.spend(), True)
    check("third spend refused", budget.spend(), False)
    result = repair(page, recipe, store, budget=budget, ask=answers)
    check("exhausted budget stops the repair", result.ok, False)
    check("and says why", "budget" in result.reason, True)



# -- tier 4 motion --------------------------------------------------------

def test_human_motion() -> None:
    """The parts of tier 4 that are arithmetic rather than uinput.

    The kernel half cannot run here -- /dev/uinput is Linux -- but the path
    the pointer takes is pure geometry, and it is the half that decides whether
    a trusted event also looks like a hand made it.
    """
    print("tier 4 motion")
    import math

    from scraper.tiers.human import _bezier

    path = list(_bezier(0, 0, 400, 300, 24))
    check("path has the requested steps", len(path), 24)
    check("path ends on the target",
          (round(path[-1][0]), round(path[-1][1])), (400, 300))

    # Curved, not straight: every point on a straight line has zero distance
    # from it, so a maximum deviation of ~0 would mean a machine-drawn line.
    # Sampled many times rather than once -- the path is random, and a single
    # draw passing proves nothing about the draw that happens in production.
    # This is how the original bug showed up: the offset was drawn from a range
    # centred on zero, so roughly one path in ten came out effectively straight.
    def deviation(px, py):
        return abs(300 * px - 400 * py) / math.hypot(400, 300)

    straightest = min(
        max(deviation(px, py) for px, py in _bezier(0, 0, 400, 300, 24))
        for _ in range(400)
    )
    check("every path is curved, not just most of them", straightest > 5.0, True,
          f"straightest of 400 deviated by {straightest:.2f}px")

    # Eased: the last steps are shorter than the middle ones, so the pointer
    # decelerates into the target instead of arriving at full speed.
    def step_len(i):
        return math.hypot(path[i][0] - path[i - 1][0], path[i][1] - path[i - 1][1])

    check("decelerates into the target", step_len(23) < step_len(12), True)

    # The click list is a short named allowlist, not "anything clickable" --
    # this tier moves a real pointer, so it must never be aimed at a cart.
    from scraper.tiers.human import CHALLENGE_SELECTORS

    joined = " ".join(CHALLENGE_SELECTORS).lower()
    for forbidden in ("cart", "buy", "checkout", "submit", "order"):
        check(f"no {forbidden} in the click allowlist", forbidden in joined, False)



# -- anti-bot walls -------------------------------------------------------

_CF_CHALLENGE = (
    "<!DOCTYPE html><html><head><title>Just a moment...</title></head><body>"
    "<script>window._cf_chl_opt={cvId:'3'};</script>"
    "<script src='/cdn-cgi/challenge-platform/h/b/orchestrate/chl_page/v1'></script>"
    "</body></html>"
)
_CF_BLOCK = (
    "<html><head><title>Attention Required! | Cloudflare</title></head><body>"
    "<div id='cf-error-details'>Sorry, you have been blocked</div></body></html>"
)


class _FakePage:
    """Serves a challenge for `polls` reads, then the shop's page."""

    def __init__(self, polls: int, after: str, document: dict) -> None:
        self.polls, self.after, self.document = polls, after, document
        self.reads = 0

    def wait_for_timeout(self, ms):
        pass

    def wait_for_load_state(self, *a, **kw):
        pass

    def content(self):
        self.reads += 1
        if self.reads == self.polls:
            # The interstitial navigating away: mid-navigation reads raise.
            raise RuntimeError("Execution context was destroyed")
        if self.reads > self.polls:
            self.document.update(status=200, headers={})
            return self.after
        return _CF_CHALLENGE


def test_challenge() -> None:
    print("challenge")
    from scraper import challenge
    from scraper.tiers import browser

    wall = challenge.detect(_CF_CHALLENGE, 403)
    check("cloudflare interstitial is a challenge", str(wall), "cloudflare challenge")
    check("a challenge is waited on", wall.waitable, True)
    wall = challenge.detect(_CF_BLOCK, 403)
    check("1020 is a block, not a challenge", str(wall), "cloudflare block")
    check("a block is not waited on", wall.waitable, False)
    check("cf-mitigated header alone is enough",
          str(challenge.detect("<html></html>", 403, {"CF-Mitigated": "challenge"})),
          "cloudflare challenge")
    # The bot-management script Cloudflare injects into every protected page
    # is not a wall: the shop's own page carries it too.
    page = fixture("jsonld_shop.html").replace(
        "</head>", "<script src='/cdn-cgi/challenge-platform/scripts/jsd/main.js'></script></head>")
    check("jsd script on a real page is not a wall", challenge.detect(page, 200), None)
    check("ordinary page is not a wall", challenge.detect(fixture("jsonld_shop.html"), 200), None)
    check("localised title on a 403 counts",
          str(challenge.detect("<title>Solo un momento...</title>", 403)), "generic challenge")

    # The 200 a challenge is served with must not read as the shop's page.
    check("challenge page is not ok",
          Fetched(url="u", status=200, html=_CF_CHALLENGE * 10, tier="t",
                  wall="cloudflare challenge").ok, False)

    # The wait: challenge, a navigation in flight, then the shop.
    document = {"status": 403, "headers": {}}
    fake = _FakePage(3, fixture("jsonld_shop.html"), document)
    wall, detail = browser.wait_out(fake, challenge.detect(_CF_CHALLENGE, 403),
                                    document, seconds=5)
    check("challenge that clears leaves no wall", wall, None)
    check("status follows the navigation", document["status"], 200)
    check("note says it cleared", "cleared" in detail, True)

    # And the deadline holds when it never clears.
    original = browser.CHALLENGE_POLL_MS
    browser.CHALLENGE_POLL_MS = 0
    try:
        stuck = _FakePage(10**9, "", {"status": 403, "headers": {}})
        wall, detail = browser.wait_out(stuck, challenge.detect(_CF_CHALLENGE, 403),
                                        stuck.document, seconds=0.2)
    finally:
        browser.CHALLENGE_POLL_MS = original
    check("uncleared challenge is still reported", str(wall), "cloudflare challenge")
    check("note says it timed out", "still up" in detail, True)

    # A tier 4 clicker is tried until it does something, then left alone.
    clicks: list[int] = []

    def clicker(page):
        clicks.append(1)
        return "clicked" if len(clicks) == 2 else ""

    document = {"status": 403, "headers": {}}
    fake = _FakePage(5, fixture("jsonld_shop.html"), document)
    browser.wait_out(fake, challenge.detect(_CF_CHALLENGE, 403), document,
                     seconds=5, after_load=clicker)
    check("clicker stops once it clicked", len(clicks), 2)

    # The ladder names the wall instead of a bare status.
    import scraper.ladder as ladder_module

    class _Walled(_FakeTier):
        def fetch(self, url, recipe=None, **kw):
            self.calls += 1
            return Fetched(url=url, status=403, html=_CF_CHALLENGE, tier=self.name,
                           wall="cloudflare challenge")

    store = Recipes(Path(tempfile.mkdtemp(prefix="wall-")))
    tiers = {name: _Walled(name, 403, "") for name in ("http", "headless", "headful", "human")}
    original_module = ladder_module._module
    ladder_module._module = lambda name: tiers.get(name)
    try:
        outcome = Ladder(store, check=lambda url, **kw: safety.check_url(url, **kw)).run(
            "https://walled.example.com/p")
    finally:
        ladder_module._module = original_module
    check("walled everywhere is not a repair job", outcome.needs_repair, False)
    check("verdict names the wall", "cloudflare challenge did not clear" in outcome.verdict, True)


def main() -> int:
    for suite in (test_money, test_extract, test_recipes, test_safety,
                  test_ladder, test_repair, test_human_motion, test_challenge):
        suite()
        print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED:")
        for failure in FAILURES:
            print("  -", failure)
        return 1
    print("all worker tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
