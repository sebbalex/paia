"""What every tier returns, and what it raises when it cannot run."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class TierUnavailable(RuntimeError):
    """This machine cannot run this tier. Not a failure of the fetch."""


@dataclass
class Fetched:
    """One page, and the evidence about how it was obtained.

    `tier` and `fingerprint` travel back to the caller because they are the
    diagnosis. A 403 at tier 1 and a 403 at tier 4 mean different things, and a
    page that only opens under a real GL context is a fact worth recording
    against the domain -- some shops block on the SwiftShader vendor string
    alone.
    """

    url: str
    status: int
    html: str
    tier: str
    elapsed: float = 0.0
    redirects: tuple[str, ...] = ()
    fingerprint: str = ""
    notes: list[str] = field(default_factory=list)
    # The anti-bot wall still standing when the tier gave up, e.g.
    # "cloudflare challenge". Empty for an ordinary page. See scraper/challenge.py.
    wall: str = ""

    @property
    def ok(self) -> bool:
        # 200 with an empty body is what a JS-only shell looks like to tier 1,
        # and it is a failure of this tier even though HTTP calls it success.
        # So is a challenge page served with 200: it is not the shop's page,
        # and reading it for a price would send it to repair instead of up
        # the ladder.
        return self.status == 200 and len(self.html) > 500 and not self.wall

    def as_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "status": self.status,
            "tier": self.tier,
            "bytes": len(self.html),
            "elapsed": round(self.elapsed, 3),
            "redirects": list(self.redirects),
            "fingerprint": self.fingerprint,
            "wall": self.wall,
            "notes": self.notes,
        }
