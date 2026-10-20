"""The scraper worker: a deterministic fetch ladder with an LLM only in fallback.

Runs as its own process, next to the assistant but not inside it. The assistant
reaches it over HTTP through assistant/scrape.py; routines reach the same one.
Nothing here imports from `assistant`, and nothing here has an opinion about
prices -- it fetches pages and pulls structured facts out of them.

Why a ladder rather than "use a browser":

    tier 1  http      curl_cffi, Chrome TLS fingerprint        ~200 ms
    tier 2  headless  Playwright, stealth, persistent profile  ~3 s
    tier 3  headful   headed under Xvfb, real GL, CDP          ~6 s
    tier 4  human     tier 3 + ydotool through /dev/uinput     ~15 s

A run starts at the tier the domain's recipe last succeeded on and climbs only
when the cheaper one fails, so the ladder gets cheaper with use. A Raspberry Pi
cannot afford to open Chromium for a shop that answers a plain GET, and most
shops do once the TLS handshake stops announcing itself as Python.
"""
