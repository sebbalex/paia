"""Shared Chromium machinery for tiers 2, 3 and 4.

Everything the three browser tiers have in common lives here: how the profile
persists, which launch flags are set, and which automation tells are patched
out. The tiers themselves differ in only three things -- headless or headed,
which GL backend, and whether input goes through CDP or through the kernel.

**The profile is the most underrated part of this file.** A browser that
arrives with no cookies, no history and no local storage is a first-time
visitor on every single request, and a first-time visitor asking directly for a
deep product URL is the exact shape of a scraper. A persistent profile per
domain means the second visit carries the consent cookie and the session the
first one earned, which is both far less detectable and considerably faster --
the challenge simply does not appear.

**On automation flags.** Playwright's default launch sets
`--enable-automation`, which makes `navigator.webdriver` true and is the single
cheapest thing a shop can test for. Turning it off and patching the property is
not enough on its own -- the checks below are a floor, not a disguise -- but
skipping them means being caught by the first line of every detection script.

Untested on darwin: this file is written for the Pi. Playwright ships no
Chromium build for linux/arm64, so the image installs Debian's `chromium` and
CHROMIUM_PATH points at it -- see docker/scraper.Dockerfile.
"""

from __future__ import annotations

import logging
import os
import random
import re
import sys
import time
from pathlib import Path
from typing import Any

from .. import challenge, safety
from .base import Fetched, TierUnavailable

log = logging.getLogger(__name__)

try:
    from playwright.sync_api import sync_playwright  # type: ignore
    AVAILABLE = True
    _WHY = ""
except ImportError:  # pragma: no cover - depends on the machine
    sync_playwright = None
    AVAILABLE = False
    _WHY = "playwright is not installed"

# Debian's chromium on arm64. Empty means "let Playwright find its own", which
# works on amd64 and fails on the Pi with a download error rather than a clear
# message -- hence the explicit env var and the check in launch().
CHROMIUM_PATH = os.environ.get("CHROMIUM_PATH", "")

PROFILE_ROOT = Path(os.environ.get("SCRAPER_STATE", "state/scraper")) / "profiles"

# A real window, not 800x600. Viewport size is fingerprinted, and the default
# headless size is a known one.
VIEWPORT = {"width": 1440, "height": 900}

# Only the headless tier overrides the User-Agent, because only headless
# Chromium says "HeadlessChrome" in it. A headed browser keeps its own: an
# override claiming Linux on a Mac, or Chrome 124 on a Chromium whose Sec-CH-UA
# client hints say 140, is a contradiction Cloudflare checks for, and the
# challenge fails on the spot. The OS token at least follows the machine; the
# version cannot be read before launch.
_UA_OS = {
    "darwin": "Macintosh; Intel Mac OS X 10_15_7",
    "win32": "Windows NT 10.0; Win64; x64",
}.get(sys.platform, "X11; Linux aarch64")
USER_AGENT = (
    f"Mozilla/5.0 ({_UA_OS}) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

LOCALE = "it-IT"
TIMEZONE = "Europe/Rome"

# What the page must have settled into before the price is read. "load" fires
# before a shop's JavaScript has painted the number; networkidle waits for the
# XHR that fetches it. The timeout is what stops a shop with a long-poll open
# from holding the tier forever.
WAIT_UNTIL = "domcontentloaded"
SETTLE_MS = 2500
NAV_TIMEOUT_MS = 30_000

# How long an anti-bot interstitial is given to decide. Cloudflare's managed
# challenge usually navigates on within 5-15 s; 25 s covers a slow Pi without
# letting three browser tiers in a row run past the assistant's own timeout.
# A recipe can change it per domain with `challenge_wait_seconds`.
CHALLENGE_WAIT_S = float(os.environ.get("SCRAPER_CHALLENGE_WAIT", "25"))
CHALLENGE_POLL_MS = 750

_BASE_ARGS = [
    # The one that matters most: without it navigator.webdriver is true and
    # every detection script in existence sees it.
    "--disable-blink-features=AutomationControlled",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-background-networking",
    "--disable-features=Translate,MediaRouter,OptimizationHints",
    # A Pi does not have shared memory to spare, and Chromium crashes rather
    # than degrading when it runs out.
    "--disable-dev-shm-usage",
]

# Patched into every page before any of the shop's own script runs. These undo
# the differences Chromium exposes when it is driven, in the order detection
# scripts test for them.
_STEALTH = """
Object.defineProperty(Navigator.prototype, 'webdriver', {get: () => undefined});
if (!window.chrome) { window.chrome = {}; }
if (!window.chrome.runtime) { window.chrome.runtime = {}; }
Object.defineProperty(Navigator.prototype, 'languages',
  {get: () => ['it-IT', 'it', 'en-US', 'en']});
Object.defineProperty(Navigator.prototype, 'plugins', {get: () => {
  const fake = [{name: 'PDF Viewer'}, {name: 'Chrome PDF Viewer'},
                {name: 'Chromium PDF Viewer'}];
  fake.item = i => fake[i]; fake.namedItem = n => fake.find(p => p.name === n);
  return fake;
}});
const _query = window.navigator.permissions && window.navigator.permissions.query;
if (_query) {
  window.navigator.permissions.query = p => (
    p && p.name === 'notifications'
      ? Promise.resolve({state: Notification.permission})
      : _query.call(window.navigator.permissions, p));
}
"""

# Only applied when the GL backend is SwiftShader. A software renderer reports
# vendor "Google Inc." / renderer "Google SwiftShader", which no real machine
# ever does -- so on a Pi without /dev/dri the honest answer is itself the tell.
# See docker/scraper.Dockerfile: passing the device through is the better fix,
# and this is the fallback.
_WEBGL_SPOOF = """
const _get = WebGLRenderingContext.prototype.getParameter;
WebGLRenderingContext.prototype.getParameter = function (p) {
  if (p === 37445) { return 'Broadcom'; }
  if (p === 37446) { return 'V3D 4.2'; }
  return _get.apply(this, arguments);
};
"""

_SAFE_NAME = re.compile(r"[^a-z0-9.-]+")


def profile_dir(host: str) -> Path:
    """One profile per domain, so cookies and consent survive between runs."""
    return PROFILE_ROOT / (_SAFE_NAME.sub("_", host.lower()) or "default")


def _block_heavy(route) -> None:
    """Drop images, fonts and media.

    Two reasons, and the second is the important one. A Pi has neither the
    bandwidth nor the memory for a shop's hero imagery, and a price lives in
    text. But blocking is also visible -- so stylesheets and XHR are left
    alone, because a page whose CSS never loads reflows in ways a shop's own
    analytics notices.
    """
    # Never inside a challenge: Turnstile draws its widget from its own origin,
    # and a challenge that cannot load what it asked for fails as a bot.
    if "challenges.cloudflare.com" in route.request.url:
        route.continue_()
    elif route.request.resource_type in ("image", "media", "font"):
        route.abort()
    else:
        route.continue_()


def launch(
    playwright,
    host: str,
    *,
    headless: bool,
    gl: str = "",
    extra_args: list[str] | None = None,
):
    """A persistent context for one domain. The caller closes it."""
    args = list(_BASE_ARGS)
    if gl:
        args.append(f"--use-gl={gl}")
    if gl == "angle":
        args.append("--use-angle=swiftshader")
    args.extend(extra_args or [])

    directory = profile_dir(host)
    directory.mkdir(parents=True, exist_ok=True)

    options: dict[str, Any] = {
        "user_data_dir": str(directory),
        "headless": headless,
        "args": args,
        "viewport": VIEWPORT,
        "locale": LOCALE,
        "timezone_id": TIMEZONE,
        "ignore_default_args": ["--enable-automation"],
    }
    if headless:
        options["user_agent"] = USER_AGENT
    if CHROMIUM_PATH:
        options["executable_path"] = CHROMIUM_PATH
    return playwright.chromium.launch_persistent_context(**options)


def _read(page) -> str:
    """The page's HTML, or "" while it is between documents.

    A challenge that clears navigates, and content() called mid-navigation
    raises "execution context was destroyed". That is progress, not failure.
    """
    try:
        return page.content()
    except Exception:
        return ""


def wait_out(page, wall: challenge.Wall, document: dict, *, seconds: float,
             after_load=None) -> tuple[challenge.Wall | None, str]:
    """Keep the browser open until the interstitial navigates away.

    The page is re-read every poll. `document` is kept current by the response
    listener in open_page with the latest main-frame status and headers, so
    the 403 the interstitial was served with is replaced by the 200 of the
    page it hands over to. `after_load`, when given, is tried on every poll
    until it does something: a Turnstile checkbox is drawn a few seconds into
    the challenge, not at load.
    """
    started = time.monotonic()
    deadline = started + seconds
    clicked = ""
    while time.monotonic() < deadline:
        page.wait_for_timeout(CHALLENGE_POLL_MS + random.randint(0, 400))
        if after_load is not None and not clicked:
            try:
                clicked = str(after_load(page) or "")
            except Exception as exc:
                clicked = f"input step failed: {type(exc).__name__}: {exc}"
        html = _read(page)
        if not html:
            continue
        current = challenge.detect(html, document.get("status", 0), document.get("headers"))
        if current is None:
            try:
                page.wait_for_load_state("networkidle", timeout=SETTLE_MS)
            except Exception:
                page.wait_for_timeout(SETTLE_MS)
            took = time.monotonic() - started
            return None, f"{wall} cleared after {took:.1f}s" + (f"; {clicked}" if clicked else "")
        if not current.waitable:
            return current, f"{wall} turned into {current} ({current.evidence})"
        wall = current
    return wall, f"{wall} still up after {seconds:.0f}s" + (f"; {clicked}" if clicked else "")


def open_page(
    url: str,
    tier: str,
    *,
    headless: bool,
    gl: str = "",
    recipe: dict | None = None,
    limiter: safety.RateLimiter | None = None,
    after_load=None,
) -> Fetched:
    """Open one page and return its rendered HTML.

    `after_load` is where tier 4 does its work: it is handed the page once the
    document exists, and whatever it does with the keyboard and mouse happens
    before the HTML is read.
    """
    if not AVAILABLE:
        raise TierUnavailable(_WHY)

    recipe = recipe or {}
    target = safety.check(url)
    if limiter is not None:
        limiter.wait(target.host, recipe.get("rate_limit_seconds"))

    started = time.monotonic()
    notes: list[str] = []
    with sync_playwright() as playwright:
        context = launch(playwright, target.host, headless=headless, gl=gl)
        try:
            context.add_init_script(_STEALTH)
            if gl == "angle":
                context.add_init_script(_WEBGL_SPOOF)
                notes.append("GL is SwiftShader; vendor string spoofed to V3D")
            context.route("**/*", _block_heavy)
            page = context.new_page()
            page.set_default_navigation_timeout(NAV_TIMEOUT_MS)

            # The status that matters is the last main-frame document's, not
            # the first: a challenge is served as 403 and, once passed,
            # navigates on to the real page with 200.
            document: dict[str, Any] = {"status": 0, "headers": {}}

            def _on_response(response) -> None:
                try:
                    if (response.request.resource_type == "document"
                            and response.frame == page.main_frame
                            and not 300 <= response.status < 400):
                        document["status"] = response.status
                        document["headers"] = response.headers
                except Exception:
                    pass

            page.on("response", _on_response)
            try:
                response = page.goto(url, wait_until=WAIT_UNTIL)
            except Exception as exc:
                notes.append(f"navigation failed: {exc}")
                return Fetched(url=url, status=0, html="", tier=tier,
                               elapsed=time.monotonic() - started, notes=notes)
            if not document["status"] and response is not None:
                document.update(status=response.status, headers=response.headers)

            # A shop paints the price after the document exists. Waiting on
            # network idle is the reliable signal; a shop that keeps a socket
            # open makes that never fire, so it is best-effort with a floor.
            try:
                page.wait_for_load_state("networkidle", timeout=SETTLE_MS)
            except Exception:
                page.wait_for_timeout(SETTLE_MS)
            page.wait_for_timeout(random.randint(250, 900))

            # An interstitial still deciding: closing now is what turns a
            # challenge being passed into a 403. Keep the window open.
            wall = challenge.detect(_read(page), document["status"], document["headers"])
            if wall is not None and wall.waitable:
                seconds = float(recipe.get("challenge_wait_seconds") or CHALLENGE_WAIT_S)
                wall, detail = wait_out(page, wall, document, seconds=seconds,
                                        after_load=after_load)
                notes.append(detail)
            elif wall is not None:
                notes.append(f"{wall} ({wall.evidence})")
            status = document["status"]

            if after_load is not None:
                try:
                    detail = after_load(page)
                    if detail:
                        notes.append(str(detail))
                except Exception as exc:
                    notes.append(f"input step failed: {type(exc).__name__}: {exc}")

            html = _read(page)
            if len(html) > safety.MAX_BYTES:
                notes.append(f"body truncated at {safety.MAX_BYTES} bytes")
                html = html[: safety.MAX_BYTES]
            return Fetched(
                url=page.url, status=status, html=html, tier=tier,
                elapsed=time.monotonic() - started,
                fingerprint=f"chromium/{'headless' if headless else 'headful'}"
                            + (f"/{gl}" if gl else ""),
                notes=notes,
                wall=str(wall) if wall else "",
            )
        finally:
            try:
                context.close()
            except Exception:
                pass
