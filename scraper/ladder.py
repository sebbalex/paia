"""Climbing the tiers: start where the domain last worked, stop as soon as it does.

Two failures look identical from the outside and must not be treated the same:

**The page would not open.** 403, Cloudflare, a timeout, a shell with nothing in
it. Anti-bot. The answer is a heavier tier -- a real browser, a real profile,
real input -- so the ladder climbs.

**The page opened and we could not read it.** HTTP 200, full markup, and no
price anywhere in the structured data or the selectors. A heavier browser
fetches the same bytes; climbing is pure waste. The answer is repair, and the
ladder stops and says so.

Getting that distinction wrong is what turns a broken selector into "the shop
blocks us" in a report, which is how `memory/wiki/price-sources.md` ends up
recommending a `site:` search for a domain that opens perfectly well.

The start tier is the one that last succeeded. It ratchets down only on
success, so a domain that needed a headful browser yesterday does not get
demoted to tier 1 by today's timeout.
"""

from __future__ import annotations

import importlib
import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from . import safety
from .extract import Found, extract
from .recipes import RETRY_AFTER_DAYS, TIERS, Recipes, domain_of, tier_index
from .tiers.base import Fetched, TierUnavailable

log = logging.getLogger(__name__)


@dataclass
class Attempt:
    tier: str
    ok: bool
    detail: str


@dataclass
class Outcome:
    """What the whole run did, tier by tier. This is the diagnosis."""

    url: str
    ok: bool = False
    fetched: Fetched | None = None
    found: Found | None = None
    attempts: list[Attempt] = field(default_factory=list)
    verdict: str = ""
    needs_repair: bool = False
    robots_allowed: bool = True
    elapsed: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "url": self.url,
            "ok": self.ok,
            "verdict": self.verdict,
            "needs_repair": self.needs_repair,
            "robots_allowed": self.robots_allowed,
            "elapsed": round(self.elapsed, 3),
            "attempts": [
                {"tier": a.tier, "ok": a.ok, "detail": a.detail} for a in self.attempts
            ],
        }
        if self.fetched is not None:
            body["fetch"] = self.fetched.as_dict()
        if self.found is not None:
            body["extracted"] = self.found.as_dict()
        return body


def _module(tier: str):
    """The module implementing one tier, or None if it is not built yet.

    Tiers 2-4 arrive in later phases. Until then the ladder reports them
    unavailable and stops at the top of what exists, which is a truthful
    answer rather than a crash.
    """
    try:
        return importlib.import_module(f".tiers.{tier}", package=__package__)
    except ImportError:
        return None


def available_tiers() -> dict[str, str]:
    """Which tiers this machine can actually run, and why not when it cannot."""
    status: dict[str, str] = {}
    for tier in TIERS:
        module = _module(tier)
        if module is None:
            status[tier] = "not implemented on this build"
        elif getattr(module, "AVAILABLE", False):
            status[tier] = "ready"
        else:
            status[tier] = getattr(module, "_WHY", "unavailable")
    return status


def _held(recipe: dict[str, Any]) -> str | None:
    """Is this domain inside its cooling-off period after being blocked?

    Same 14 days price-hunt SKILL.md section 3 already works to, so the skill
    and the worker do not disagree about when a retry is worth spending. The
    hold is reported, never silent: a caller who wants the retry passes force.
    """
    if recipe.get("status") != "blocked":
        return None
    seen = recipe.get("last_seen")
    if not seen:
        return None
    try:
        last = datetime.fromisoformat(str(seen)).date()
    except ValueError:
        return None
    due = last + timedelta(days=RETRY_AFTER_DAYS)
    if date.today() < due:
        return f"blocked on {seen}; next retry no earlier than {due.isoformat()}"
    return None


class Ladder:
    def __init__(
        self,
        recipes: Recipes,
        *,
        limiter: safety.RateLimiter | None = None,
        robots: safety.Robots | None = None,
        check=safety.check,
    ) -> None:
        self._recipes = recipes
        self._limiter = limiter or safety.RateLimiter()
        self._robots = robots
        # Injected for the same reason Robots takes its fetcher: the tests
        # exercise escalation, not DNS, and a suite that only passes with a
        # working resolver is a suite that gets skipped on the server. The
        # default is the real check, and scraper/app.py never overrides it.
        self._check = check

    def run(
        self,
        url: str,
        *,
        max_tier: str = "human",
        force: bool = False,
        want_html: bool = False,
    ) -> Outcome:
        started = time.monotonic()
        self._check(url)  # raises Refused; the caller reports it
        domain = domain_of(url)
        recipe = self._recipes.for_url(url)
        outcome = Outcome(url=url)

        hold = None if force else _held(recipe)
        if hold:
            outcome.verdict = hold
            outcome.elapsed = time.monotonic() - started
            return outcome

        if self._robots is not None:
            allowed, delay = self._robots.verdict(url)
            outcome.robots_allowed = allowed
            if delay:
                recipe = {**recipe, "rate_limit_seconds": max(
                    float(recipe.get("rate_limit_seconds") or 0), float(delay)
                )}

        ceiling = min(
            tier_index(max_tier),
            tier_index(str(recipe.get("max_tier") or "human")),
        )
        start = min(tier_index(str(recipe.get("tier") or "http")), ceiling)

        fetched: Fetched | None = None
        found: Found | None = None
        for index in range(start, ceiling + 1):
            tier = TIERS[index]
            module = _module(tier)
            if module is None or not getattr(module, "AVAILABLE", False):
                reason = (
                    "not implemented on this build" if module is None
                    else getattr(module, "_WHY", "unavailable")
                )
                outcome.attempts.append(Attempt(tier, False, reason))
                continue
            try:
                fetched = module.fetch(url, recipe, limiter=self._limiter)
            except TierUnavailable as exc:
                outcome.attempts.append(Attempt(tier, False, str(exc)))
                continue
            except safety.Refused:
                raise
            except Exception as exc:
                outcome.attempts.append(Attempt(tier, False, f"{type(exc).__name__}: {exc}"))
                continue

            if not fetched.ok:
                if fetched.wall:
                    detail = f"HTTP {fetched.status}, {fetched.wall} did not clear"
                elif fetched.status != 200:
                    detail = f"HTTP {fetched.status}"
                else:
                    detail = f"200 but only {len(fetched.html)} bytes -- a shell"
                outcome.attempts.append(Attempt(tier, False, detail))
                continue

            found = extract(fetched.html, recipe)
            if found.ok:
                outcome.attempts.append(
                    Attempt(tier, True, f"price from {found.source}")
                )
                break
            outcome.attempts.append(
                Attempt(tier, False, "page opened, no price found in it")
            )

        outcome.fetched = fetched
        outcome.found = found
        outcome.elapsed = time.monotonic() - started

        if found is not None and found.ok:
            outcome.ok = True
            outcome.verdict = f"read at tier {fetched.tier} from {found.source}"
            self._recipes.record(
                self._recipes.get(domain),
                url=url, tier=fetched.tier, ok=True, source=found.source,
            )
        elif fetched is not None and fetched.ok:
            # The page is there and we cannot read it. Climbing is not the fix.
            outcome.needs_repair = True
            outcome.verdict = (
                f"page opened at tier {fetched.tier} but no price could be "
                "extracted -- the selectors need repair, not a heavier browser"
            )
            self._recipes.record(
                self._recipes.get(domain),
                url=url, tier=fetched.tier, ok=False, note="extraction failed",
            )
        else:
            if outcome.attempts:
                tried = "; ".join(f"{a.tier}: {a.detail}" for a in outcome.attempts)
                outcome.verdict = f"no tier opened the page -- {tried}"
            else:
                outcome.verdict = "no tier was available to try"
            self._recipes.record(
                self._recipes.get(domain),
                url=url, tier=None, ok=False, note=outcome.verdict[:200],
            )

        if want_html and fetched is not None:
            outcome.fetched = fetched
        return outcome
