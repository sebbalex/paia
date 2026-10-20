"""Recognising an anti-bot wall, and telling the two kinds apart.

A Cloudflare "Just a moment..." page is not a refusal. It is a test that takes
time: the interstitial runs its JavaScript, fingerprints the browser, sometimes
shows a Turnstile checkbox, and only then navigates to the real page and sets
`cf_clearance` in the profile. That takes five to fifteen seconds. A browser
that reads the page after its usual settle and closes has thrown away a test
it may well have been passing, and reports the interstitial's 403 as if the
shop had refused.

So a wall has a kind, and the kind decides what the tier does next:

  challenge  the page is still deciding. Keep the browser open and wait for
             it to navigate away, up to a deadline.
  block      the decision is made: Cloudflare's 1020, Akamai's "Access Denied",
             a hard captcha. Waiting is wasted; climb the ladder.

Detection is markers in the markup plus Cloudflare's own `cf-mitigated`
header. Every marker here is one that appears on the wall page and not on an
ordinary page behind the same vendor -- in particular, Cloudflare injects
`/cdn-cgi/challenge-platform/scripts/jsd/` into normal pages it protects, so
that path alone means nothing and is deliberately not a marker.

Stdlib only, so the tests run without a browser.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping

# Only the head of the page is searched. Wall pages are small, and a product
# page that quotes "just a moment" in a review further down is not one.
_SCAN_BYTES = 64_000

_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)


@dataclass(frozen=True)
class Wall:
    vendor: str
    kind: str  # "challenge" or "block"
    evidence: str

    @property
    def waitable(self) -> bool:
        return self.kind == "challenge"

    def __str__(self) -> str:
        return f"{self.vendor} {self.kind}"


# (vendor, kind, marker). Lowercase substrings of the page head, checked in
# order: blocks before challenges, because a 1020 page also carries some of the
# challenge platform's markup and must not be waited on.
_MARKERS: tuple[tuple[str, str, str], ...] = (
    ("cloudflare", "block", "cf-error-details"),
    ("cloudflare", "block", "error code: 1020"),
    ("cloudflare", "block", "sorry, you have been blocked"),
    ("akamai", "block", "errors.edgesuite.net"),
    ("datadome", "block", "geo.captcha-delivery.com"),
    ("datadome", "block", "ct.captcha-delivery.com"),
    ("imperva", "block", "_incapsula_resource"),
    ("sucuri", "block", "sucuri website firewall"),
    ("cloudflare", "challenge", "window._cf_chl_opt"),
    ("cloudflare", "challenge", "/cdn-cgi/challenge-platform/h/"),
    ("cloudflare", "challenge", "id=\"challenge-form\""),
    ("cloudflare", "challenge", "cf-browser-verification"),
    ("perimeterx", "challenge", "px-captcha"),
    ("aws-waf", "challenge", "awswafintegration"),
    ("generic", "challenge", "checking your browser before accessing"),
    ("generic", "challenge", "verifying you are human"),
    ("generic", "challenge", "verifica di essere un essere umano"),
)

# Interstitial titles, in the languages the profile asks for. Cloudflare
# localises the page to Accept-Language, and the browser tiers ask for it-IT.
_TITLES = (
    "just a moment",
    "un momento",
    "ci siamo quasi",
    "einen moment",
    "attention required",
    "please wait",
)


def detect(html: str, status: int = 0, headers: Mapping[str, str] | None = None) -> Wall | None:
    """The wall this page is, or None for an ordinary page."""
    mitigated = ""
    for key, value in (headers or {}).items():
        if key.lower() == "cf-mitigated":
            mitigated = str(value).lower()
            break
    if mitigated == "challenge":
        return Wall("cloudflare", "challenge", "cf-mitigated: challenge")

    head = (html or "")[:_SCAN_BYTES].lower()
    for vendor, kind, marker in _MARKERS:
        if marker in head:
            return Wall(vendor, kind, marker)

    # A title alone is weak evidence, so it only counts on a page that also
    # looks like a wall: a refusing status, or too small to be a shop.
    match = _TITLE.search(head)
    if match:
        title = " ".join(match.group(1).split())
        if any(t in title for t in _TITLES) and (status in (403, 429, 503) or len(head) < 20_000):
            return Wall("generic", "challenge", f"title {title!r}")
    return None
