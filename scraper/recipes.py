"""Per-domain fetch and extraction rules: the deterministic memory.

A recipe is what makes the second hunt cheaper than the first. It records the
cheapest tier that has actually worked for a domain, the selectors that pulled
the price out when the structured data did not, and what the domain has been
doing lately. A run starts from it and writes back to it.

Two things it is deliberately not:

**Not the wiki.** Recipes live under state/, which assistant/policy.py:83 makes
unreadable and unwritable to the model. Nobody hand-edits a selector, and an
injected page cannot rewrite one. memory/wiki/price-sources.md stays the
human-readable mirror the assistant does write, for the owner to read.

**Not keyed by domain alone.** price-sources.md is, and it has already produced
a contradiction: tradeinn.com is recorded `renders` on 2026-08-27 and `js` on
2026-09-04, because one product page on that domain rendered and another
returned the placeholder "Price without code 0". A domain is the right key for
*which tier gets you in*. It is the wrong key for *whether extraction worked on
this page*. Hence `url_overrides`, so the two facts stop overwriting each other.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

log = logging.getLogger(__name__)

TIERS: tuple[str, ...] = ("http", "headless", "headful", "human")

# A domain is a filename here, so it has to be one that cannot escape the
# directory. Anything else is refused rather than sanitised: a sanitiser that
# silently maps two domains onto one file is worse than a refusal.
_DOMAIN = re.compile(r"\A[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?\Z")

# How many runs in a row may fail before the domain is called blocked rather
# than unlucky. Three is a shop that is refusing, not a shop having a bad day.
BLOCK_AFTER = 3

# How long a `blocked` verdict stands before one retry is worth spending.
# Matches the 14 days price-hunt SKILL.md section 3 already works to, so the
# skill and the worker do not disagree about when to try again.
RETRY_AFTER_DAYS = 14

_HISTORY_KEPT = 20


def tier_index(name: str) -> int:
    try:
        return TIERS.index(name)
    except ValueError:
        return 0


def domain_of(url: str) -> str:
    """The registrable-ish host: lowercase, no port, no leading www.

    Not a public-suffix lookup. `www.bike24.de` and `bike24.de` must share a
    recipe; `shop.example.co.uk` keeping its own is the harmless outcome of not
    having a suffix list, and one wrong guess about co.uk would not be.
    """
    host = (urlparse(url).hostname or "").lower().strip(".")
    return host[4:] if host.startswith("www.") else host


def _blank(domain: str) -> dict[str, Any]:
    return {
        "domain": domain,
        "tier": "http",
        "max_tier": "human",
        "currency": None,
        "extract": {},
        "url_overrides": {},
        "rate_limit_seconds": 8,
        "status": "unknown",
        "last_ok": None,
        "last_seen": None,
        "consecutive_failures": 0,
        "history": [],
    }


class Recipes:
    """The store. One JSON file per domain, plus a .prev for rollback."""

    def __init__(self, directory: Path | str) -> None:
        self._dir = Path(directory)
        self._guard = threading.Lock()

    @property
    def directory(self) -> Path:
        return self._dir

    def _path(self, domain: str) -> Path:
        if not _DOMAIN.match(domain):
            raise ValueError(f"not a usable domain name: {domain!r}")
        return self._dir / f"{domain}.json"

    # -- reading -----------------------------------------------------------

    def get(self, domain: str) -> dict[str, Any]:
        """The recipe for a domain, or a blank one. Never raises on content.

        A corrupt recipe reads as blank rather than taking the fetch down: the
        worst case is one run at tier 1 that should have started higher, and
        the run then writes a good file back over it.
        """
        path = self._path(domain)
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return _blank(domain)
        except (OSError, ValueError) as exc:
            log.warning("recipe for %s unreadable (%s), treating as new", domain, exc)
            return _blank(domain)
        if not isinstance(loaded, dict):
            return _blank(domain)
        recipe = _blank(domain)
        recipe.update(loaded)
        recipe["domain"] = domain
        if recipe.get("tier") not in TIERS:
            recipe["tier"] = "http"
        if recipe.get("max_tier") not in TIERS:
            recipe["max_tier"] = "human"
        return recipe

    def for_url(self, url: str) -> dict[str, Any]:
        """The recipe with any per-URL override folded in.

        The override is the answer to the tradeinn problem: the domain says
        which tier opens it, this says that *this page* needs a higher one or a
        different selector.
        """
        recipe = self.get(domain_of(url))
        override = (recipe.get("url_overrides") or {}).get(_url_key(url))
        if not isinstance(override, dict):
            return recipe
        merged = dict(recipe)
        for key, value in override.items():
            if key == "extract" and isinstance(value, dict):
                merged["extract"] = {**(recipe.get("extract") or {}), **value}
            else:
                merged[key] = value
        merged["overridden"] = True
        return merged

    def known(self) -> list[str]:
        try:
            return sorted(p.stem for p in self._dir.glob("*.json"))
        except OSError:
            return []

    # -- writing -----------------------------------------------------------

    def save(self, recipe: dict[str, Any], *, keep_previous: bool = False) -> None:
        """Write atomically, optionally keeping the old file for rollback.

        Atomic because the worker is threaded and a half-written recipe read by
        another request is a domain that silently drops to tier 1. `.prev` is
        what makes an LLM repair reversible: scraper/repair.py sets it, so a
        bad selector that passed validation can still be undone by hand.
        """
        domain = str(recipe.get("domain") or "")
        path = self._path(domain)
        with self._guard:
            self._dir.mkdir(parents=True, exist_ok=True)
            if keep_previous and path.exists():
                try:
                    path.replace(path.with_suffix(".json.prev"))
                except OSError as exc:
                    log.warning("could not keep previous recipe for %s: %s", domain, exc)
            body = json.dumps(recipe, indent=2, ensure_ascii=False, sort_keys=True)
            handle, temporary = tempfile.mkstemp(dir=self._dir, suffix=".tmp")
            try:
                with os.fdopen(handle, "w", encoding="utf-8") as fh:
                    fh.write(body + "\n")
                os.replace(temporary, path)
            except BaseException:
                Path(temporary).unlink(missing_ok=True)
                raise

    def rollback(self, domain: str) -> bool:
        """Put the previous recipe back. True if there was one."""
        path = self._path(domain)
        previous = path.with_suffix(".json.prev")
        with self._guard:
            if not previous.exists():
                return False
            os.replace(previous, path)
            return True

    # -- recording an outcome ---------------------------------------------

    def record(
        self,
        recipe: dict[str, Any],
        *,
        url: str,
        tier: str | None,
        ok: bool,
        source: str | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        """Fold the result of one run into the recipe and persist it.

        The tier that worked becomes the tier the next run starts at -- that is
        the whole economy of the ladder. It ratchets *down* only on success:
        a domain that needed a headful browser today is not demoted to tier 1
        tomorrow because tomorrow's failure happened to be a timeout.
        """
        today = date.today().isoformat()
        recipe = dict(recipe)
        recipe.pop("overridden", None)
        recipe["last_seen"] = today
        if ok:
            recipe["consecutive_failures"] = 0
            recipe["last_ok"] = today
            recipe["status"] = tier or recipe.get("tier") or "http"
            if tier:
                recipe["tier"] = tier
        else:
            failures = int(recipe.get("consecutive_failures") or 0) + 1
            recipe["consecutive_failures"] = failures
            if failures >= BLOCK_AFTER:
                recipe["status"] = "blocked"
            elif recipe.get("status") not in ("blocked", "needs-repair"):
                recipe["status"] = "failing"

        history = list(recipe.get("history") or [])
        history.insert(0, {
            "date": today,
            "url": _url_key(url),
            "tier": tier,
            "ok": ok,
            "source": source,
            "note": note[:200],
        })
        recipe["history"] = history[:_HISTORY_KEPT]
        self.save(recipe)
        return recipe

    def note_override(self, url: str, **fields: Any) -> dict[str, Any]:
        """Record that one page on a domain behaves differently from the rest."""
        domain = domain_of(url)
        recipe = self.get(domain)
        overrides = dict(recipe.get("url_overrides") or {})
        key = _url_key(url)
        entry = dict(overrides.get(key) or {})
        entry.update(fields)
        entry["noted"] = date.today().isoformat()
        overrides[key] = entry
        recipe["url_overrides"] = overrides
        self.save(recipe)
        return recipe


def _url_key(url: str) -> str:
    """A URL reduced to what identifies the page: no query, no fragment.

    Tracking parameters would otherwise make every visit a different page and
    the override map would grow without ever matching. Shops that put the
    product id in the query are the exception, and they lose the override --
    which costs one tier of escalation, not a wrong answer.
    """
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return f"{host}{parsed.path or '/'}"
