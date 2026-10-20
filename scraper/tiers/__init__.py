"""The four ways of opening a page, cheapest first.

Each module exports `fetch(url, recipe, **kw) -> Fetched` and a `AVAILABLE`
flag saying whether this machine can actually run it. A tier that cannot run
reports why rather than raising: on a laptop with no Chromium, tiers 2-4 are
simply unavailable, and the ladder skips them instead of failing the request.
"""

from .base import Fetched, TierUnavailable

__all__ = ["Fetched", "TierUnavailable"]
