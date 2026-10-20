"""What the worker refuses to do, decided in Python before anything opens.

The worker is a fetcher that lives *inside* the owner's network, and that inverts a
guarantee the assistant already had. assistant/policy.py::check_host rejects a
URL resolving to a private address, which is what stops the model being talked
into proxying for the LAN or for a cloud metadata endpoint. Route the fetch
through a worker and that check now sees only the worker's own address -- the
target it was written to protect is never examined.

So the check is made twice, independently. Once in the assistant's PreToolUse
gate, on the url the model passed. Once here, on the url actually about to be
opened, after every redirect. The second one is the one that always runs:
routines and any future client reach the worker directly and never pass through
the first.

Nothing here consults the model, for the same reason nothing in
assistant/policy.py does.
"""

from __future__ import annotations

import ipaddress
import logging
import random
import socket
import threading
import time
import urllib.robotparser
from dataclasses import dataclass
from urllib.parse import urlparse, urlunparse

log = logging.getLogger(__name__)

# GET only. HEAD is allowed because it is a cheaper GET; everything else is a
# side effect somewhere. price-hunt SKILL.md section 4 says buy nothing, add
# nothing to a cart, fill in no form -- that is a rule the model is told, and
# this is the same rule where text cannot argue with it.
_METHODS = frozenset({"GET", "HEAD"})

_SCHEMES = frozenset({"http", "https"})

# Long enough that a slow shop still answers, short enough that a hung socket
# cannot sit inside the assistant's 300 s turn budget.
CONNECT_TIMEOUT = 10.0
READ_TIMEOUT = 25.0

MAX_URL = 2048
MAX_REDIRECTS = 5
MAX_BYTES = 8 * 1024 * 1024


class Refused(Exception):
    """A request the worker will not make. The reason travels to the caller."""


@dataclass(frozen=True)
class Target:
    url: str
    host: str
    scheme: str

    @property
    def origin(self) -> str:
        return f"{self.scheme}://{self.host}"


def check_url(raw: str, *, method: str = "GET", allow_http: bool = False) -> Target:
    """Syntax and scheme. Where the name points is check_host's job."""
    if method.upper() not in _METHODS:
        raise Refused(
            f"{method.upper()} is not available: the worker reads pages and "
            "nothing else -- no form, no cart, no login"
        )
    if not isinstance(raw, str) or not raw.strip():
        raise Refused("no url given")
    if len(raw) > MAX_URL:
        raise Refused("url is implausibly long")
    try:
        parsed = urlparse(raw.strip())
    except ValueError as exc:
        raise Refused(f"url could not be parsed: {exc}") from exc
    if parsed.scheme not in _SCHEMES:
        raise Refused(f"scheme '{parsed.scheme}' is not fetchable")
    # http is refused by default for the reason assistant/policy.py refuses it:
    # anyone in the middle can rewrite a page the assistant then reads as fact.
    # A shop that is https-only in practice sometimes redirects from http, and
    # the redirect follower sets allow_http for that first hop only.
    if parsed.scheme == "http" and not allow_http:
        raise Refused("only https is fetched: plain http can be rewritten in transit")
    host = (parsed.hostname or "").lower()
    if not host:
        raise Refused("url has no host")
    return Target(raw.strip(), host, parsed.scheme)


def check_host(host: str, port: int = 443) -> list[str]:
    """Resolve, and refuse if the name points anywhere but the public internet.

    Every returned address must be public, not merely the first: a name that
    answers with one public and one private address is a DNS rebinding attempt
    wearing a hat. 169.254.169.254 is the case that matters most -- on a cloud
    instance it hands out credentials to anything that asks -- but a Raspberry
    Pi on a home LAN has its own version of the problem, and it is every other
    box on the LAN.

    Blocking, like its counterpart in assistant/policy.py. Callers on an event
    loop run it in a thread.
    """
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise Refused(f"host '{host}' does not resolve") from None
    except Exception as exc:
        raise Refused(f"host '{host}' could not be resolved: {exc}") from exc
    if not infos:
        raise Refused(f"host '{host}' resolves to nothing")

    addresses: list[str] = []
    for info in infos:
        raw = info[4][0]
        try:
            ip = ipaddress.ip_address(raw.split("%")[0])
        except ValueError:
            raise Refused(f"unparseable address for '{host}': {raw!r}") from None
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                or ip.is_multicast or ip.is_unspecified):
            raise Refused(
                f"host '{host}' resolves to a non-public address ({ip}) -- "
                "the worker fetches the internet, not the network it sits on"
            )
        addresses.append(str(ip))
    return addresses


def check(raw: str, *, method: str = "GET", allow_http: bool = False) -> Target:
    """Both checks, in the order a fetch needs them."""
    target = check_url(raw, method=method, allow_http=allow_http)
    check_host(target.host, 443 if target.scheme == "https" else 80)
    return target


class RateLimiter:
    """One domain at a time, with a gap between hits and jitter on the gap.

    This is politeness, and politeness is also camouflage: a fixed interval is
    itself a signature, so the wait is the recipe's interval plus up to a third
    of it again. The lock per domain means two callers asking for the same shop
    queue rather than arriving together, which is the pattern that gets an IP
    rate-limited even when the total volume is trivial.
    """

    def __init__(self, default_seconds: float = 8.0) -> None:
        self._default = default_seconds
        self._last: dict[str, float] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    def _lock_for(self, host: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(host, threading.Lock())

    def wait(self, host: str, seconds: float | None = None) -> float:
        """Block until this host may be hit again. Returns how long it waited."""
        gap = self._default if seconds is None else max(0.0, float(seconds))
        lock = self._lock_for(host)
        with lock:
            previous = self._last.get(host)
            waited = 0.0
            if previous is not None and gap:
                due = previous + gap * random.uniform(1.0, 1.33)
                waited = max(0.0, due - time.monotonic())
                if waited:
                    time.sleep(waited)
            self._last[host] = time.monotonic()
            return waited


class Robots:
    """robots.txt, fetched once per origin and cached.

    Deliberately advisory. ROT.md leaves outward-facing judgement to the owner, and
    a Disallow on a product page is a fact they should be told rather than a
    decision this file makes silently in either direction: obeying it quietly
    turns into "the shop is blocked" in a report, and ignoring it quietly is
    the assistant doing something on their behalf they never agreed to. So `allows`
    reports, `delay` is honoured, and scraper/app.py puts the verdict in the
    response where it reaches them.
    """

    def __init__(self, fetch, ttl_seconds: float = 6 * 3600) -> None:
        # `fetch` is injected rather than imported so robots.txt is read by the
        # same tier-1 client as everything else -- and so tests need no network.
        self._fetch = fetch
        self._ttl = ttl_seconds
        self._cache: dict[str, tuple[float, urllib.robotparser.RobotFileParser | None]] = {}
        self._guard = threading.Lock()

    def _parser(self, origin: str):
        with self._guard:
            cached = self._cache.get(origin)
            if cached and time.monotonic() - cached[0] < self._ttl:
                return cached[1]
        parser: urllib.robotparser.RobotFileParser | None = None
        try:
            body = self._fetch(f"{origin}/robots.txt")
        except Exception as exc:
            # No robots.txt, or it would not open. Not an error: the great
            # majority of shops that block a fetch also block this.
            log.info("robots.txt for %s unavailable: %s", origin, exc)
        else:
            if body:
                parser = urllib.robotparser.RobotFileParser()
                try:
                    parser.parse(body.splitlines())
                except Exception:
                    parser = None
        with self._guard:
            self._cache[origin] = (time.monotonic(), parser)
        return parser

    def verdict(self, url: str, user_agent: str = "*") -> tuple[bool, float | None]:
        """(allowed, crawl_delay). Unknown robots.txt reads as allowed."""
        parsed = urlparse(url)
        origin = urlunparse((parsed.scheme, parsed.netloc, "", "", "", ""))
        parser = self._parser(origin)
        if parser is None:
            return True, None
        try:
            allowed = parser.can_fetch(user_agent, url)
        except Exception:
            allowed = True
        delay = None
        try:
            raw = parser.crawl_delay(user_agent)
            delay = float(raw) if raw is not None else None
        except Exception:
            delay = None
        return bool(allowed), delay
