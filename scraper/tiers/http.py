"""Tier 1: a plain request that does not look like Python.

This is the tier that matters most and the one easiest to underrate, because
the interesting part is invisible from the application layer. What gets a
scraper 403'd at this level is almost never the User-Agent -- it is the TLS
ClientHello. Cipher list, extension order, supported groups and ALPN together
make a JA3/JA4 fingerprint, and stock httpx or requests produce one that no
browser has ever emitted. Cloudflare's cheapest rule matches on it before a
single header is read.

memory/wiki/log.md:485 records the experiment that settles this: on 2026-08-19
bike-discount.de was retried "with fetch and with curl using a browser UA" and
403'd both times. The header was changed; the handshake was not. curl_cffi
links against a curl built to reproduce Chrome's handshake exactly, which is
the variable that has never been tried here.

Redirects are followed by hand, not by the client. Every hop is a new URL, and
a shop that redirects to 169.254.169.254 or to a LAN address must be caught at
the hop, not at the first request -- see scraper/safety.py for why that matters
more here than it does in the assistant.
"""

from __future__ import annotations

import logging
import time

from .. import challenge, safety
from .base import Fetched, TierUnavailable

log = logging.getLogger(__name__)

NAME = "http"

# The Chrome build curl_cffi impersonates. Pinned rather than "latest": the
# fingerprint is the point, and a floating target makes a regression
# untraceable. Bump it deliberately, and record in the recipe history when it
# changed, because a domain that starts failing the day after is evidence.
IMPERSONATE = "chrome124"

# Accept-Language is not decoration. A German shop serves a different page --
# sometimes a different price -- to a browser that asks for de-DE, and the owner
# ships to Italy, so it/de/en in that order is what they would actually see.
# curl_cffi supplies the rest of the header set and their order along with the
# fingerprint; overriding them piecemeal is how the two stop matching.
HEADERS = {
    "Accept-Language": "it-IT,it;q=0.9,de;q=0.8,en;q=0.7",
}

try:  # the real thing
    from curl_cffi import requests as _curl  # type: ignore
    AVAILABLE = True
    _WHY = ""
except ImportError:  # pragma: no cover - depends on the machine
    _curl = None
    AVAILABLE = False
    _WHY = "curl_cffi is not installed"


def _session():
    if _curl is None:
        raise TierUnavailable(_WHY)
    # allow_redirects=False everywhere: see the module docstring.
    return _curl.Session(impersonate=IMPERSONATE, allow_redirects=False)


def fetch(
    url: str,
    recipe: dict | None = None,
    *,
    session=None,
    limiter: safety.RateLimiter | None = None,
) -> Fetched:
    """Open `url` at tier 1, following redirects one checked hop at a time."""
    if not AVAILABLE and session is None:
        raise TierUnavailable(_WHY)

    recipe = recipe or {}
    started = time.monotonic()
    owned = session is None
    session = session or _session()
    redirects: list[str] = []
    notes: list[str] = []
    current = url
    allow_http = False

    try:
        for hop in range(safety.MAX_REDIRECTS + 1):
            target = safety.check(current, allow_http=allow_http)
            if limiter is not None:
                limiter.wait(target.host, recipe.get("rate_limit_seconds"))
            response = session.get(
                current,
                headers=HEADERS,
                timeout=(safety.CONNECT_TIMEOUT, safety.READ_TIMEOUT),
            )
            status = int(response.status_code)
            if status in (301, 302, 303, 307, 308):
                location = response.headers.get("location") or ""
                if not location:
                    notes.append(f"{status} with no Location header")
                    break
                current = _absolute(current, location)
                redirects.append(current)
                # A shop redirecting https -> http is downgrading the
                # connection. Follow it so the run reports what the shop did,
                # but the SSRF check still applies to where it points.
                allow_http = current.startswith("http://")
                if allow_http:
                    notes.append("shop redirected to plain http")
                continue

            html = _body(response, notes)
            # Tier 1 cannot run a challenge's JavaScript, so a wall here is
            # final for this tier whatever its kind. Naming it is what tells
            # the ladder to climb rather than hand the interstitial to repair.
            wall = challenge.detect(html, status, dict(getattr(response, "headers", None) or {}))
            if wall is not None:
                notes.append(f"{wall} ({wall.evidence})")
            return Fetched(
                url=current, status=status, html=html, tier=NAME,
                elapsed=time.monotonic() - started,
                redirects=tuple(redirects),
                fingerprint=IMPERSONATE,
                notes=notes,
                wall=str(wall) if wall else "",
            )
        notes.append(f"more than {safety.MAX_REDIRECTS} redirects")
        return Fetched(
            url=current, status=0, html="", tier=NAME,
            elapsed=time.monotonic() - started, redirects=tuple(redirects),
            fingerprint=IMPERSONATE, notes=notes,
        )
    finally:
        if owned:
            try:
                session.close()
            except Exception:
                pass


def _body(response, notes: list[str]) -> str:
    """The page as text, truncated rather than allowed to exhaust the Pi."""
    raw = response.content or b""
    if len(raw) > safety.MAX_BYTES:
        notes.append(f"body truncated at {safety.MAX_BYTES} bytes")
        raw = raw[: safety.MAX_BYTES]
    encoding = getattr(response, "encoding", None) or "utf-8"
    try:
        return raw.decode(encoding, errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def _absolute(base: str, location: str) -> str:
    from urllib.parse import urljoin

    return urljoin(base, location.strip())
