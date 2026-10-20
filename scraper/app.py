"""The worker's front door: four routes over http.server, on loopback.

No web framework on purpose. This serves one user, binds an address nothing
outside the machine can reach, and has four routes; FastAPI and uvicorn would
be more dependencies than the entire assistant has (requirements.txt is four
real lines). The whole repo is built on that kind of restraint -- see the
comment above Gws._call in assistant/gws.py about argv lists and shells.

    GET  /health              is it up, and which tiers can it run
    GET  /status?domain=x     what the recipe store knows about a domain
    POST /fetch               open a page, return text and the tier used
    POST /extract             open a page, return price/availability/title

Every route needs `Authorization: Bearer <SCRAPER_TOKEN>`. The token is not the
security boundary -- binding to loopback is -- it is what stops another process
on the same host using the worker by accident.

Requests are queued: MAX_CONCURRENCY at a time overall and, through
RateLimiter, one per domain. That ceiling is not politeness, it is a Raspberry
Pi with 4 GB and a whisper model already resident.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from . import safety
from .extract import extract
from .ladder import Ladder, available_tiers
from .recipes import Recipes, domain_of
from .repair import Budget, repair

log = logging.getLogger(__name__)

MAX_BODY = 64 * 1024
# Two at a time. Tier 1 is cheap, but a queued tier-3 request holds a Chromium,
# and two of those is already most of the free memory on a 4 GB Pi.
MAX_CONCURRENCY = 2


class Worker:
    """Everything the handler needs, built once at start."""

    def __init__(self, recipes: Recipes, token: str, *, repair_budget: int = 20) -> None:
        self.recipes = recipes
        self.token = token
        self.limiter = safety.RateLimiter()
        self.robots = safety.Robots(self._fetch_robots)
        self.ladder = Ladder(recipes, limiter=self.limiter, robots=self.robots)
        self.slots = threading.Semaphore(MAX_CONCURRENCY)
        self.budget = Budget(recipes.directory.parent / "repair-budget.json",
                             limit=repair_budget)

    def try_repair(self, outcome) -> dict[str, Any] | None:
        """One repair attempt on a page that opened but could not be read.

        Only reached when the ladder has already ruled out "the shop blocks
        us": a heavier browser fetches the same bytes, so the selectors are
        what is wrong. On success the page is re-extracted immediately, so the
        caller gets the price on this run rather than the next one.
        """
        html = outcome.fetched.html if outcome.fetched else ""
        if not html:
            return None
        recipe = self.recipes.get(domain_of(outcome.url))
        result = repair(html, recipe, self.recipes, budget=self.budget)
        report = {"attempted": True, "ok": result.ok, "reason": result.reason}
        if not result.ok:
            log.info("repair declined for %s: %s", outcome.url, result.reason)
            return report
        report["selector"] = result.selector
        found = extract(html, self.recipes.get(domain_of(outcome.url)))
        if found.ok:
            outcome.found = found
            outcome.ok = True
            outcome.needs_repair = False
            outcome.verdict = (
                f"read at tier {outcome.fetched.tier} after repairing the "
                f"selector for {domain_of(outcome.url)}"
            )
            self.recipes.record(
                self.recipes.get(domain_of(outcome.url)),
                url=outcome.url, tier=outcome.fetched.tier, ok=True,
                source=found.source, note="after repair",
            )
        return report

    def _fetch_robots(self, url: str) -> str:
        """robots.txt through tier 1, so it shares the fingerprint and the
        rate limiter rather than announcing a second, different client."""
        from .tiers import http as tier_http

        if not tier_http.AVAILABLE:
            return ""
        result = tier_http.fetch(url, {"rate_limit_seconds": 0})
        return result.html if result.status == 200 else ""


class Handler(BaseHTTPRequestHandler):
    worker: Worker  # set by serve()

    server_version = "paia-scraper"
    sys_version = ""

    # -- plumbing ----------------------------------------------------------

    def log_message(self, fmt: str, *args: Any) -> None:
        log.info("%s %s", self.address_string(), fmt % args)

    def _send(self, code: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass

    def _authorised(self) -> bool:
        if not self.worker.token:
            return True
        header = self.headers.get("Authorization", "")
        prefix = "Bearer "
        if not header.startswith(prefix):
            return False
        import hmac

        return hmac.compare_digest(header[len(prefix):].strip(), self.worker.token)

    def _json_body(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise ValueError("bad Content-Length")
        if length <= 0:
            return {}
        if length > MAX_BODY:
            raise ValueError("request body too large")
        raw = self.rfile.read(length)
        loaded = json.loads(raw.decode("utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError("body must be a JSON object")
        return loaded

    # -- routes ------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 - http.server's spelling
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            # Deliberately before the auth check: a container health probe has
            # no token, and "is the process alive" is not a secret.
            self._send(200, {"ok": True, "tiers": available_tiers()})
            return
        if not self._authorised():
            self._send(401, {"error": "bad or missing bearer token"})
            return
        if parsed.path == "/status":
            query = parse_qs(parsed.query)
            domain = (query.get("domain") or [""])[0].strip().lower()
            if not domain:
                self._send(400, {"error": "domain is required"})
                return
            if "/" in domain or ":" in domain:
                domain = domain_of(domain if "//" in domain else f"https://{domain}")
            try:
                recipe = self.worker.recipes.get(domain)
            except ValueError as exc:
                self._send(400, {"error": str(exc)})
                return
            self._send(200, {
                "domain": recipe["domain"],
                "status": recipe.get("status"),
                "tier": recipe.get("tier"),
                "last_ok": recipe.get("last_ok"),
                "last_seen": recipe.get("last_seen"),
                "consecutive_failures": recipe.get("consecutive_failures"),
                # The selectors themselves stay in. They are machine state, and
                # a caller that could read them would be one edit from writing
                # them -- see the note in scraper/recipes.py about state/.
                "has_selectors": bool(recipe.get("extract")),
                "url_overrides": sorted((recipe.get("url_overrides") or {}).keys()),
                "history": (recipe.get("history") or [])[:5],
            })
            return
        self._send(404, {"error": f"no route {parsed.path}"})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if not self._authorised():
            self._send(401, {"error": "bad or missing bearer token"})
            return
        if parsed.path not in ("/fetch", "/extract"):
            self._send(404, {"error": f"no route {parsed.path}"})
            return
        try:
            body = self._json_body()
        except (ValueError, UnicodeDecodeError) as exc:
            self._send(400, {"error": str(exc)})
            return

        url = str(body.get("url") or "").strip()
        max_tier = str(body.get("max_tier") or "human")
        force = bool(body.get("force"))

        if not self.worker.slots.acquire(timeout=120):
            self._send(503, {"error": "worker busy; every slot is in use"})
            return
        try:
            outcome = self.worker.ladder.run(
                url, max_tier=max_tier, force=force, want_html=True
            )
        except safety.Refused as exc:
            # A refusal is an answer, not a server error: the caller is told
            # exactly what was declined and why, the way policy.py does.
            self._send(403, {"error": str(exc), "url": url})
            return
        except Exception as exc:
            log.exception("fetch failed")
            self._send(500, {"error": f"{type(exc).__name__}: {exc}"})
            return
        finally:
            self.worker.slots.release()

        # A page that opened but could not be read is a broken selector, not a
        # blocked shop -- see the note at the top of scraper/ladder.py. Repair
        # is the one place a model touches the deterministic path, and it is
        # opt-out rather than opt-in because the alternative is a hunt silently
        # reporting "blocked" for a shop that opens perfectly well.
        report = None
        if outcome.needs_repair and body.get("repair", True):
            try:
                report = self.worker.try_repair(outcome)
            except Exception as exc:
                log.exception("repair failed")
                report = {"attempted": True, "ok": False,
                          "reason": f"{type(exc).__name__}: {exc}"}

        payload = outcome.as_dict()
        if report is not None:
            payload["repair"] = report
        if parsed.path == "/fetch":
            html = outcome.fetched.html if outcome.fetched else ""
            limit = int(body.get("max_bytes") or 400_000)
            payload["truncated"] = len(html) > limit
            payload["html"] = html[:limit]
        self._send(200 if outcome.ok or outcome.fetched else 502, payload)


def serve(
    recipes_dir: Path | str,
    *,
    host: str = "127.0.0.1",
    port: int = 8099,
    token: str = "",
    repair_budget: int = 20,
) -> ThreadingHTTPServer:
    worker = Worker(Recipes(recipes_dir), token, repair_budget=repair_budget)
    handler = type("BoundHandler", (Handler,), {"worker": worker})
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    return server


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("SCRAPER_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    root = Path(os.environ.get("SCRAPER_STATE", "state/scraper"))
    server = serve(
        root / "recipes",
        # Loopback by default. In the add-on this becomes 0.0.0.0 on the
        # container's own network, which is not the host's -- and still no
        # published port, so nothing outside Docker can reach it.
        host=os.environ.get("SCRAPER_HOST", "127.0.0.1"),
        port=int(os.environ.get("SCRAPER_PORT", "8099")),
        token=os.environ.get("SCRAPER_TOKEN", ""),
        repair_budget=int(os.environ.get("SCRAPER_REPAIR_BUDGET", "20")),
    )
    log.info("scraper worker on %s:%s", *server.server_address[:2])
    log.info("tiers: %s", available_tiers())
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
