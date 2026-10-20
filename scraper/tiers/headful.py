"""Tier 3: a real, headed browser on a virtual display, with real GL.

What this buys over tier 2 is the things a headless browser cannot fake because
they are properties of the process rather than of a JavaScript object: a window
with a size and a position, a compositor, a GPU process, and a WebGL context
whose vendor string comes from a driver. Shops that fingerprint the rendering
stack -- a canvas hash, a WebGL vendor, the list of supported extensions --
separate tier 2 from tier 3 without reading a single header.

GL backend, in order of preference:

  egl    the Pi's own V3D through /dev/dri. Honest, and the only option here
         that produces a vendor string a real machine also produces.
  angle  ANGLE over SwiftShader, software rendering. Works anywhere, and
         reports "Google SwiftShader", which is itself a detection signal --
         scraper/tiers/browser.py spoofs the vendor string when this is used,
         which is a patch over a hole rather than a fix.

Set SCRAPER_GL to force one. The default probes for /dev/dri and falls back.

Needs something to open a window on. On Linux that means an X server or a
Wayland compositor -- in the container, Xvfb on :99, started by the entrypoint.
On macOS and Windows the window system is always there and DISPLAY is an X11
variable neither of them sets, so requiring it would report this tier
unavailable on a machine that runs it fine. See _has_display().
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from .. import safety
from .browser import AVAILABLE as _BROWSER_OK, _WHY as _BROWSER_WHY, open_page
from .base import Fetched

NAME = "headful"


def _gl() -> str:
    forced = os.environ.get("SCRAPER_GL", "").strip()
    if forced:
        return forced
    if sys.platform != "linux":
        # macOS and Windows have a real GPU behind ANGLE already, and forcing a
        # backend there gets a worse context than the default -- SwiftShader in
        # particular is software rendering on a machine that does not need it,
        # and its vendor string is the exact thing tier 3 exists to avoid.
        return ""
    return "egl" if Path("/dev/dri").exists() else "angle"


def _has_display() -> bool:
    """Can a headed browser open a window here?

    DISPLAY is an X11 variable, not a general one. macOS draws through Quartz
    and Windows through its own compositor; neither sets it, and neither needs
    it. Gating on DISPLAY alone therefore reports tier 3 unavailable on a Mac
    that can run a headed Chromium perfectly well, which is wrong and makes
    the tier impossible to develop against locally.

    On Linux it is a real requirement: no X server and no Wayland compositor
    means no window, which is why the container runs Xvfb.
    """
    if sys.platform in ("darwin", "win32"):
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


AVAILABLE = _BROWSER_OK and _has_display()
_WHY = _BROWSER_WHY or (
    "" if _has_display() else
    "no DISPLAY and no WAYLAND_DISPLAY: a headed browser needs a compositor to "
    "open a window on. In the container that is Xvfb, started by "
    "docker/scraper-entrypoint.sh; on a bare Linux host, run "
    "`Xvfb :99 -screen 0 1440x900x24 &` and export DISPLAY=:99"
)


def fetch(
    url: str,
    recipe: dict | None = None,
    *,
    limiter: safety.RateLimiter | None = None,
) -> Fetched:
    return open_page(url, NAME, headless=False, gl=_gl(), recipe=recipe, limiter=limiter)
