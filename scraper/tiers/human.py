"""Tier 4: a real browser, driven through the kernel rather than through CDP.

This tier exists for one specific wall, and it is worth being precise about
what that wall is, because the tier is expensive and using it anywhere else is
waste.

Every click Playwright or CDP synthesises arrives at the page with
`event.isTrusted === false`. That flag is set by the browser itself and cannot
be forged from JavaScript, so any script can ask. Challenge widgets --
Cloudflare Turnstile's checkbox, "press and hold" buttons, some cookie walls --
ask. No amount of stealth patching changes the answer, because the answer is
true: nothing physical happened.

ydotool writes to /dev/uinput, which creates a virtual input device in the
kernel. The event then travels the same path a USB keyboard's does: through the
input subsystem, into the compositor, into Chromium, and out to the page with
`isTrusted === true`. There is no flag to check, because at that level nothing
is being simulated.

What makes it look human is not the mechanism but the motion. A pointer that
teleports to a checkbox and clicks in the same millisecond is a robot with
trusted events. So the cursor travels a curve, at a speed that varies, with a
pause before the click and a slight miss of dead centre.

Requirements, all of which fail loudly rather than silently:
  - /dev/uinput present and writable  (addon devices: ["/dev/uinput"])
  - ydotoold running                  (started by the container entrypoint)
  - a display                         (Xvfb, as tier 3)

Untested on darwin: uinput is a Linux interface and this file cannot run here.
The verification plan is a checkbox on a local page asserting isTrusted.
"""

from __future__ import annotations

import logging
import math
import os
import random
import shutil
import subprocess
import sys
import time
from pathlib import Path

from .. import safety
from .browser import AVAILABLE as _BROWSER_OK, _WHY as _BROWSER_WHY, open_page
from .base import Fetched

log = logging.getLogger(__name__)

NAME = "human"

YDOTOOL = os.environ.get("YDOTOOL_BIN", "ydotool")
UINPUT = Path("/dev/uinput")

# Selectors worth a human click, in the order they are tried. Deliberately a
# short, named list rather than "anything clickable": this tier moves a real
# pointer, and a scraper that clicks whatever it finds on a shop's page is one
# misidentified element away from doing something that is not reading.
# price-hunt section 4 -- buy nothing, no cart, no form -- is the rule, and a
# narrow list is how it survives contact with a page nobody reviewed.
CHALLENGE_SELECTORS = (
    "iframe[src*='challenges.cloudflare.com']",
    "#challenge-stage input[type=checkbox]",
    "input[type=checkbox]#cf-turnstile-response",
    ".cf-turnstile",
    "#onetrust-accept-btn-handler",
    "button#didomi-notice-agree-button",
    "[data-testid='uc-accept-all-button']",
)


def _why() -> str:
    if _BROWSER_WHY:
        return _BROWSER_WHY
    # Linux first, and said plainly: uinput is a Linux kernel interface, so on
    # a Mac this tier is not misconfigured, it is inapplicable. Reporting "no
    # DISPLAY" there sends you looking for an X server that would not help.
    if sys.platform != "linux":
        return (
            f"tier 4 is Linux-only: it works by writing /dev/uinput, a kernel "
            f"interface {sys.platform} does not have. Tiers 1-3 are unaffected"
        )
    from .headful import _has_display

    if not _has_display():
        return ("no DISPLAY and no WAYLAND_DISPLAY: tier 4 drives a real "
                "browser, which needs a compositor to open a window on")
    if not UINPUT.exists():
        return ("/dev/uinput is not present: the container needs "
                'devices: ["/dev/uinput"] and SYS_ADMIN, see addon/scraper/config.yaml')
    if not os.access(UINPUT, os.W_OK):
        return "/dev/uinput is present but not writable by this user"
    if shutil.which(YDOTOOL) is None:
        return f"{YDOTOOL} is not on PATH"
    return ""


_WHY = _why()
AVAILABLE = _BROWSER_OK and not _WHY


def _run(args: list[str]) -> None:
    """One ydotool invocation, argv only, never a shell."""
    subprocess.run([YDOTOOL, *args], check=True, capture_output=True, timeout=15)


def _bezier(x0: float, y0: float, x1: float, y1: float, steps: int):
    """A curved path between two points, with the control point off-axis.

    A straight line at constant speed is as machine-like as a teleport; what
    separates a hand from a stepper motor is that it overshoots slightly and
    arrives slowly. The control point is offset perpendicular to the direction
    of travel by a fraction of the distance, and the step spacing is eased so
    the pointer decelerates into the target.
    """
    span = math.hypot(x1 - x0, y1 - y0) or 1.0
    # The magnitude is bounded away from zero, not merely random around it.
    # Drawing the offset from (-0.22, 0.22) puts a straight line inside the
    # range, and a straight line is the one path this function exists to avoid
    # -- a pointer that travels exactly along the axis between two points is as
    # machine-like as a teleport. Pick the side at random, the amount from a
    # range that starts well clear of nothing.
    offset = random.choice((-1.0, 1.0)) * random.uniform(0.08, 0.22) * span
    mx = (x0 + x1) / 2 - (y1 - y0) / span * offset
    my = (y0 + y1) / 2 + (x1 - x0) / span * offset
    for step in range(1, steps + 1):
        raw = step / steps
        t = raw * raw * (3 - 2 * raw)  # ease in and out
        u = 1 - t
        yield (u * u * x0 + 2 * u * t * mx + t * t * x1,
               u * u * y0 + 2 * u * t * my + t * t * y1)


def move_and_click(x: float, y: float, *, from_x: float = 0, from_y: float = 0) -> None:
    """Travel to (x, y) along a curve and click, the way a hand would."""
    steps = random.randint(18, 32)
    for px, py in _bezier(from_x, from_y, x, y, steps):
        _run(["mousemove", "--absolute", "-x", str(int(px)), "-y", str(int(py))])
        time.sleep(random.uniform(0.006, 0.022))
    # Nobody clicks the instant the pointer lands.
    time.sleep(random.uniform(0.12, 0.38))
    _run(["click", "0xC0"])  # left press and release
    time.sleep(random.uniform(0.4, 1.1))


def type_text(text: str) -> None:
    """Type with a per-key delay, because a paste is one event and typing is many."""
    for character in text:
        _run(["type", "--key-delay", str(random.randint(40, 130)), character])


def _screen_offset(page) -> tuple[float, float]:
    """Where the page's viewport sits on the screen.

    bounding_box() is in viewport coordinates and ydotool moves in screen
    coordinates; between them are the window's position and the browser's own
    toolbar. Missing that offset puts every click about 80px too high, above
    the checkbox it was aimed at.
    """
    try:
        x, y = page.evaluate(
            "() => [window.screenX + (window.outerWidth - window.innerWidth) / 2,"
            " window.screenY + (window.outerHeight - window.innerHeight)]"
        )
        return float(x), float(y)
    except Exception:
        return 0.0, 0.0


def _turnstile_box(page) -> dict | None:
    """The Turnstile widget's box, found through its frame.

    Cloudflare mounts the widget's iframe inside a closed shadow root, which
    no CSS selector pierces -- so CHALLENGE_SELECTORS can stare straight at a
    challenge page and match nothing. The frame itself is still in
    page.frames, and its element still has a box.
    """
    for frame in page.frames:
        if "challenges.cloudflare.com" not in (frame.url or ""):
            continue
        try:
            box = frame.frame_element().bounding_box()
        except Exception:
            continue
        if box and box["width"] >= 4 and box["height"] >= 4:
            return box
    return None


def _click_box(page, box: dict, rel_x: tuple[float, float], what: str) -> str:
    ox, oy = _screen_offset(page)
    # Not dead centre: a hand does not hit the middle of a checkbox.
    x = ox + box["x"] + box["width"] * random.uniform(*rel_x)
    y = oy + box["y"] + box["height"] * random.uniform(0.35, 0.65)
    log.info("tier 4 clicking %s at %.0f,%.0f", what, x, y)
    move_and_click(x, y, from_x=ox + random.randint(80, 400),
                   from_y=oy + random.randint(80, 400))
    try:
        page.wait_for_load_state("networkidle", timeout=8000)
    except Exception:
        pass
    return f"clicked a challenge or consent widget matching {what}"


def clear_challenges(page) -> str:
    """Find a challenge or consent widget and click it like a person.

    Returns a note for the fetch record, or "" when there was nothing to do --
    which is the common case, and the reason this tier is only reached when the
    ones below it have already failed.
    """
    box = _turnstile_box(page)
    if box is not None:
        # The checkbox is the left edge of a 300x65 widget, not its middle.
        return _click_box(page, box, (0.07, 0.12), "turnstile frame")
    for selector in CHALLENGE_SELECTORS:
        try:
            element = page.query_selector(selector)
        except Exception:
            continue
        if element is None:
            continue
        try:
            box = element.bounding_box()
        except Exception:
            box = None
        if not box or box["width"] < 4 or box["height"] < 4:
            continue
        return _click_box(page, box, (0.35, 0.65), selector)
    return ""


def fetch(
    url: str,
    recipe: dict | None = None,
    *,
    limiter: safety.RateLimiter | None = None,
) -> Fetched:
    if not AVAILABLE:
        from .base import TierUnavailable

        raise TierUnavailable(_WHY)
    from .headful import _gl

    return open_page(
        url, NAME, headless=False, gl=_gl(), recipe=recipe, limiter=limiter,
        after_load=clear_challenges,
    )
