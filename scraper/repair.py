"""When a selector breaks, propose a new one -- and prove it before keeping it.

This is the only place a model touches the deterministic path, and the whole
design is about keeping that touch small. The model is asked one question, on
pruned input, and its answer is not believed: the proposed selector is re-run
against the very page that prompted the repair and must produce a number that
could be a price. A selector that matches a review count, a postcode, a
delivery time in days or tradeinn.com's "Price without code 0" is discarded,
and the recipe is left exactly as it was.

Three properties this file exists to guarantee:

**The model never reports a price.** It returns a selector. The number always
comes from scraper/extract.py re-reading the page, so a model that hallucinates
"the price is 39.90" produces nothing at all rather than a plausible lie.

**Every repair is reversible.** The previous recipe is kept as .json.prev, so a
bad selector that happened to pass validation can be undone without guessing
what it replaced.

**It cannot run away.** A daily budget caps how many repairs happen, because
the failure mode of an auto-healing system is healing the same thing forty
times an hour while the real problem is elsewhere.

The credential question is settled by the environment: the worker shells out to
the `claude` CLI, which is already installed in the image and already
authenticated. The owner's token is an sk-ant-oat OAuth token, not an API key, so a
raw Anthropic client would not work with it (see .env.example) -- and reusing
the CLI means the worker needs no second credential of its own.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from .extract import from_selectors
from .money import plausible
from .recipes import Recipes

log = logging.getLogger(__name__)

# Text that might be money: a digit group, optionally with separators, near a
# currency marker or on its own. Deliberately loose -- this decides what the
# model is *shown*, and being shown a review count costs nothing while missing
# the price costs the repair.
_MONEYISH = re.compile(r"(?:[€$£]|EUR|USD|GBP|CHF)\s*\d|\d[\d.,\s]*(?:[€$£]|EUR|USD|GBP|CHF)|\d{1,3}(?:[.,]\d{3})*[.,]\d{2}\b")

# Attributes worth showing: the ones a selector can be built from. class and id
# first, then the data-* hooks modern shops actually use.
_USEFUL_ATTRS = ("id", "class", "itemprop", "data-testid", "data-test", "data-qa",
                 "data-price", "data-role", "aria-label")

_CANDIDATE_LIMIT = 60
_TEXT_LIMIT = 120
_TIMEOUT = 120

PROMPT = """\
You are repairing a web scraper's CSS selector for one shop page.

Below are the candidate elements from a product page: every element whose text
looks like it could be money, with its ancestor chain and attributes. One of
them is the product's current selling price.

Reply with JSON and nothing else:

  {"price": "<css selector>", "availability": "<css selector or null>",
   "reasoning": "<one short sentence>"}

Rules:
- Return a CSS selector, never a price. The number is read by the scraper.
- Prefer stable hooks: itemprop, data-testid, data-* attributes, semantic
  class names. Avoid hashed classes like .css-1x2y3z and avoid :nth-child.
- The selling price, not a crossed-out list price, not a monthly instalment,
  not "from" pricing for a different variant, not shipping, not a review count.
- If nothing in the candidates is a product price, return {"price": null}.
"""


@dataclass
class Repair:
    ok: bool
    reason: str
    selector: str | None = None
    price: float | None = None
    reasoning: str = ""


class _Candidates(HTMLParser):
    """Every money-looking text node, with enough context to build a selector.

    Pruning is the point. A product page is two megabytes, almost all of it
    script and markup the question does not depend on; what a selector-writing
    model needs is the handful of elements that contain a number and the path
    to reach them. This typically emits well under 10 KB.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.found: list[dict[str, Any]] = []
        self._stack: list[tuple[str, dict[str, str]]] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in ("script", "style", "noscript", "svg", "template"):
            self._skip += 1
            return
        if self._skip:
            return
        useful = {}
        for key, value in attrs:
            key = (key or "").lower()
            if key in _USEFUL_ATTRS and value:
                useful[key] = value[:120]
        self._stack.append((tag, useful))

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "noscript", "svg", "template"):
            self._skip = max(0, self._skip - 1)
            return
        if self._skip:
            return
        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index][0] == tag:
                del self._stack[index:]
                break

    def handle_data(self, data: str) -> None:
        if self._skip or len(self.found) >= _CANDIDATE_LIMIT:
            return
        text = data.strip()
        if not text or not _MONEYISH.search(text):
            return
        self.found.append({
            "text": text[:_TEXT_LIMIT],
            "path": [_describe(tag, attrs) for tag, attrs in self._stack[-6:]],
        })


def _describe(tag: str, attrs: dict[str, str]) -> str:
    parts = [tag]
    if "id" in attrs:
        parts.append(f"#{attrs['id']}")
    if "class" in attrs:
        parts.extend(f".{c}" for c in attrs["class"].split()[:4])
    for key in ("itemprop", "data-testid", "data-test", "data-qa", "data-price"):
        if key in attrs:
            parts.append(f"[{key}='{attrs[key]}']")
    return "".join(parts)


def candidates(html: str) -> list[dict[str, Any]]:
    parser = _Candidates()
    try:
        parser.feed(html)
        parser.close()
    except Exception as exc:
        log.warning("candidate scan stopped early: %s", exc)
    return parser.found


class Budget:
    """A repair counter that resets daily.

    Kept in a file rather than in memory because the worker restarts, and a
    budget that resets on restart is not a budget.
    """

    def __init__(self, path: Path | str, limit: int = 20) -> None:
        self._path = Path(path)
        self._limit = limit

    def _read(self) -> tuple[str, int]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return str(data.get("date") or ""), int(data.get("used") or 0)
        except (OSError, ValueError, TypeError):
            return "", 0

    def remaining(self) -> int:
        day, used = self._read()
        return self._limit if day != date.today().isoformat() else max(0, self._limit - used)

    def spend(self) -> bool:
        day, used = self._read()
        today = date.today().isoformat()
        used = 0 if day != today else used
        if used >= self._limit:
            return False
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps({"date": today, "used": used + 1}), encoding="utf-8"
        )
        return True


def ask_claude(prompt: str, binary: str = "claude", timeout: int = _TIMEOUT) -> str:
    """One non-interactive turn through the CLI, argv only, no shell.

    Same arrangement as assistant/gws.py's subprocess call: a list, never a
    command line, so nothing in the page text can extend the command.
    """
    result = subprocess.run(
        [binary, "-p", "--output-format", "json", prompt],
        capture_output=True, text=True, timeout=timeout, shell=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"claude exited {result.returncode}: {(result.stderr or '').strip()[:300]}"
        )
    try:
        envelope = json.loads(result.stdout)
    except ValueError:
        return result.stdout
    if isinstance(envelope, dict):
        return str(envelope.get("result") or envelope.get("text") or result.stdout)
    return result.stdout


def _parse_answer(raw: str) -> dict[str, Any]:
    """The JSON out of a reply that may be wrapped in prose or a fence."""
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in the reply")
    parsed = json.loads(text[start:end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("reply was not a JSON object")
    return parsed


def _selector_text(html: str, selector: str) -> str | None:
    """The raw text one selector matches, or None if it cannot be checked."""
    try:
        from selectolax.parser import HTMLParser as Dom
    except ImportError:
        return None
    try:
        node = Dom(html).css_first(selector)
    except Exception:
        return None
    return None if node is None else (node.text() or "")


def repair(
    html: str,
    recipe: dict[str, Any],
    recipes: Recipes,
    *,
    budget: Budget | None = None,
    ask=ask_claude,
) -> Repair:
    """Propose a selector, validate it against this page, commit only if it holds."""
    domain = str(recipe.get("domain") or "")
    if budget is not None and not budget.spend():
        return Repair(False, "daily repair budget is spent")

    found = candidates(html)
    if not found:
        return Repair(False, "no money-looking text on the page to build a selector from")

    prompt = (
        PROMPT
        + f"\nShop: {domain}\nCurrency expected: {recipe.get('currency') or 'unknown'}\n"
        + "\nCandidates:\n"
        + json.dumps(found, ensure_ascii=False, indent=1)
    )
    try:
        answer = _parse_answer(ask(prompt))
    except Exception as exc:
        return Repair(False, f"could not read the model's reply: {exc}")

    selector = answer.get("price")
    if not selector or not isinstance(selector, str):
        return Repair(False, "the model found no price selector on this page")
    selector = selector.strip()
    if len(selector) > 300:
        return Repair(False, "proposed selector is implausibly long")

    # Validation. This is the load-bearing step: the selector is re-run against
    # the page that prompted the repair, and only a plausible price keeps it.
    rules: dict[str, Any] = {"price": {"css": selector}}
    availability = answer.get("availability")
    if isinstance(availability, str) and availability.strip():
        rules["availability"] = {"css": availability.strip()}

    # Does the selector point at text that is recognisably money? This check
    # exists because plausible() cannot do it: "482 reviews" parses to 482.0,
    # and 482.0 is a perfectly plausible price. Magnitude cannot separate a
    # price from a review count, a product code or a delivery time -- only the
    # presence of a currency marker can, which is the same test used to choose
    # the candidates the model was shown.
    matched = _selector_text(html, selector)
    if matched is not None and not _MONEYISH.search(matched):
        return Repair(
            False,
            f"the proposed selector matched {matched.strip()[:60]!r}, which is "
            "a number but not money -- no currency anywhere in it",
            selector=selector,
        )

    verified = from_selectors(html, rules, recipe.get("currency"))
    if verified.price is None:
        return Repair(
            False,
            "the proposed selector did not yield a price when re-run on the "
            f"same page: {'; '.join(verified.notes) or 'no match'}",
            selector=selector,
        )
    if not plausible(verified.price.amount):
        return Repair(
            False,
            f"the proposed selector yielded {verified.price.amount}, which is "
            "not a price",
            selector=selector,
        )

    updated = dict(recipe)
    updated["extract"] = {**(recipe.get("extract") or {}), **rules}
    updated["status"] = "repaired"
    updated["consecutive_failures"] = 0
    history = list(updated.get("history") or [])
    history.insert(0, {
        "date": date.today().isoformat(),
        "event": "repair",
        "selector": selector,
        "validated_price": round(verified.price.amount, 2),
        "reasoning": str(answer.get("reasoning") or "")[:200],
    })
    updated["history"] = history[:20]
    recipes.save(updated, keep_previous=True)

    return Repair(
        True,
        f"selector repaired and validated at {verified.price.amount}",
        selector=selector,
        price=verified.price.amount,
        reasoning=str(answer.get("reasoning") or "")[:200],
    )
