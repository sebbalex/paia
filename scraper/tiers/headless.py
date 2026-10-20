"""Tier 2: a headless Chromium that has been told not to advertise itself.

The step up from tier 1 is JavaScript. Roughly a third of the shops in
memory/wiki/price-sources.md are recorded as `js` rather than `blocked`: the
request succeeded, the page came back, and the price simply was not in it
because it is painted after load. No amount of TLS impersonation fixes that.

What tier 2 does not fix is a shop that specifically detects headless
Chromium -- `--headless=new` closed most of the old giveaways, but a headless
browser still has no GPU worth the name and no window manager. That is tier 3.
"""

from __future__ import annotations

from .. import safety
from .browser import AVAILABLE, _WHY, open_page  # noqa: F401
from .base import Fetched

NAME = "headless"


def fetch(
    url: str,
    recipe: dict | None = None,
    *,
    limiter: safety.RateLimiter | None = None,
) -> Fetched:
    return open_page(url, NAME, headless=True, recipe=recipe, limiter=limiter)
