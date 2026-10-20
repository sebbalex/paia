"""Pulling facts out of a page, cheapest and most stable source first.

The order is the whole design. A CSS selector is the *last* thing to reach for,
because it is the thing a redesign breaks: `.price-box__amount--3f2a` survives
until Friday. Structured data survives redesigns, because it exists to be read
by machines and the shop's own SEO depends on it staying correct.

    1. JSON-LD      <script type="application/ld+json">  Product/Offer
    2. microdata    itemprop="price"
    3. meta         og:price:amount, product:price:amount
    4. selectors    per-domain, from the recipe
    5. LLM repair   only when 1-4 all fail on a page that actually loaded

Steps 1-3 are stdlib-only, deliberately: they answer most shops, so the part
carrying most of the load has no dependency to break on a Raspberry Pi and is
testable in a bare checkout. Step 4 imports selectolax lazily and reports
itself unavailable rather than raising when it is missing.

Nothing in this module trusts what it reads. A page is text a shop wrote.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any

from .money import Money, MoneyError, parse as parse_money, plausible

# schema.org availability values, normalised to one word. The URL form, the
# bare form and the http/https spellings all occur in the wild.
_AVAILABILITY = {
    "instock": "in_stock", "onlineonly": "in_stock", "instoreonly": "in_stock",
    "limitedavailability": "low_stock",
    "outofstock": "out_of_stock", "soldout": "out_of_stock",
    "discontinued": "discontinued",
    "preorder": "preorder", "presale": "preorder", "backorder": "backorder",
}

_SCRIPT_LD = re.compile(
    r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
    re.I | re.S,
)


def _availability(value: Any) -> str | None:
    """schema.org availability -> one word, or None if it is not one."""
    if not isinstance(value, str):
        return None
    tail = value.rstrip("/").rsplit("/", 1)[-1].replace("_", "").lower()
    return _AVAILABILITY.get(tail)


@dataclass
class Found:
    """What one extraction step managed to pull out.

    `source` names the step, and it travels all the way back to the caller on
    purpose: a price from JSON-LD and a price from a repaired CSS selector are
    not equally trustworthy, and the assistant should be able to say which it
    got. `notes` collects why the earlier steps declined, which is what makes a
    failure diagnosable instead of a shrug.
    """

    price: Money | None = None
    availability: str | None = None
    title: str | None = None
    source: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.price is not None

    def as_dict(self) -> dict[str, Any]:
        return {
            "price": None if self.price is None else round(self.price.amount, 2),
            "currency": None if self.price is None else self.price.currency,
            "availability": self.availability,
            "title": self.title,
            "source": self.source,
            "notes": self.notes,
        }


class _Scanner(HTMLParser):
    """One pass over the page collecting everything steps 1-3 need.

    Subclassing HTMLParser rather than reaching for a DOM library keeps steps
    1-3 dependency-free. It is also forgiving in the way a real page requires:
    unclosed tags, duplicate attributes and stray `<` do not stop it.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.metas: dict[str, str] = {}
        self.itemprops: list[tuple[str, str | None]] = []
        self.title: str | None = None
        self._itemprop_stack: list[tuple[str, list[str]]] = []
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {k.lower(): (v or "") for k, v in attrs}
        if tag == "meta":
            key = (attr.get("property") or attr.get("name") or "").lower()
            if key and "content" in attr:
                self.metas.setdefault(key, attr["content"])
        elif tag == "title":
            self._in_title = True
        prop = attr.get("itemprop")
        if prop:
            # `content` wins over the element's text: shops put the machine
            # value there precisely because the text is formatted for humans
            # ("ab 39,90 €" with the number split across spans).
            self.itemprops.append((prop.lower(), attr.get("content") or None))
            self._itemprop_stack.append((prop.lower(), []))

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        if self._itemprop_stack:
            prop, chunks = self._itemprop_stack[-1]
            text = " ".join(chunks).strip()
            if text:
                self.itemprops.append((prop, text))
            self._itemprop_stack.pop()

    def handle_data(self, data: str) -> None:
        if self._in_title and self.title is None:
            stripped = data.strip()
            if stripped:
                self.title = stripped
        for _, chunks in self._itemprop_stack:
            chunks.append(data)


def _walk(node: Any):
    """Every dict in a JSON-LD document, however it is nested.

    Shops nest a Product inside @graph, inside an array, inside a WebPage's
    mainEntity, and every combination of those. Walking is shorter than
    enumerating the shapes, and does not break when a shop invents a new one.
    """
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def _types(node: dict) -> set[str]:
    raw = node.get("@type") or node.get("type") or ()
    if isinstance(raw, str):
        raw = (raw,)
    return {str(t).rsplit("/", 1)[-1].lower() for t in raw}


def from_json_ld(html: str, currency: str | None = None) -> Found:
    """Step 1. schema.org Product/Offer, the most stable source there is."""
    found = Found(source="json-ld")
    blocks = 0
    for raw in _SCRIPT_LD.findall(html):
        try:
            document = json.loads(raw.strip())
        except (ValueError, TypeError):
            # A single malformed block is normal and not worth failing over --
            # shops emit trailing commas. Keep going; another block may be fine.
            found.notes.append("json-ld: a block did not parse")
            continue
        blocks += 1
        for node in _walk(document):
            types = _types(node)
            if "product" in types and found.title is None:
                name = node.get("name")
                if isinstance(name, str) and name.strip():
                    found.title = name.strip()
            if not types & {"offer", "aggregateoffer", "unitpricespecification"}:
                continue
            if found.availability is None:
                found.availability = _availability(node.get("availability"))
            if found.price is not None:
                continue
            for key in ("price", "lowPrice", "highPrice", "priceValue"):
                value = node.get(key)
                if value is None:
                    continue
                try:
                    money = parse_money(str(value), node.get("priceCurrency") or currency)
                except MoneyError:
                    continue
                if plausible(money.amount):
                    found.price = money
                    break
    if not blocks:
        found.notes.append("json-ld: no blocks on the page")
    elif found.price is None:
        found.notes.append("json-ld: blocks present but no usable Offer price")
    return found


def from_microdata(scanner: _Scanner, currency: str | None = None) -> Found:
    """Step 2. itemprop="price", the pre-JSON-LD way of saying the same thing."""
    found = Found(source="microdata")
    for prop, value in scanner.itemprops:
        if value is None:
            continue
        if prop == "availability" and found.availability is None:
            found.availability = _availability(value)
        elif prop in ("price", "lowprice") and found.price is None:
            try:
                money = parse_money(value, currency)
            except MoneyError:
                continue
            if plausible(money.amount):
                found.price = money
        elif prop == "pricecurrency" and found.price is not None and not found.price.currency:
            found.price = Money(found.price.amount, value.strip().upper() or None)
    if found.price is None:
        found.notes.append("microdata: no itemprop price")
    return found


def from_meta(scanner: _Scanner, currency: str | None = None) -> Found:
    """Step 3. OpenGraph. Weakest of the structured three and often stale, but
    it costs nothing to read once the page is already parsed."""
    found = Found(source="meta")
    code = (
        scanner.metas.get("og:price:currency")
        or scanner.metas.get("product:price:currency")
        or currency
    )
    for key in ("og:price:amount", "product:price:amount", "product:price"):
        value = scanner.metas.get(key)
        if not value:
            continue
        try:
            money = parse_money(value, code)
        except MoneyError:
            continue
        if plausible(money.amount):
            found.price = money
            break
    if found.price is None:
        found.notes.append("meta: no og:price:amount")
    return found


def from_selectors(html: str, rules: dict[str, Any], currency: str | None = None) -> Found:
    """Step 4. The per-domain recipe. The first step that a redesign breaks,
    and therefore the first one the repair path is allowed to rewrite."""
    found = Found(source="selectors")
    if not rules:
        found.notes.append("selectors: none in the recipe")
        return found
    try:
        from selectolax.parser import HTMLParser as Dom  # lazy: see module docstring
    except ImportError:
        found.notes.append("selectors: selectolax not installed, step skipped")
        return found

    dom = Dom(html)

    def pick(rule: Any) -> str | None:
        if isinstance(rule, str):
            rule = {"css": rule}
        if not isinstance(rule, dict) or not rule.get("css"):
            return None
        node = dom.css_first(str(rule["css"]))
        if node is None:
            return None
        attr = str(rule.get("attr") or "text")
        if attr == "text":
            return (node.text() or "").strip() or None
        return (node.attributes.get(attr) or "").strip() or None

    raw_price = pick(rules.get("price"))
    if raw_price:
        try:
            money = parse_money(raw_price, currency)
        except MoneyError as exc:
            found.notes.append(f"selectors: price selector matched but {exc}")
        else:
            if plausible(money.amount):
                found.price = money
            else:
                found.notes.append(
                    f"selectors: price selector yielded {money.amount}, not a price"
                )
    else:
        found.notes.append("selectors: price selector matched nothing")

    raw_stock = pick(rules.get("availability"))
    if raw_stock:
        found.availability = _availability(raw_stock) or raw_stock[:80]
    raw_title = pick(rules.get("title"))
    if raw_title:
        found.title = raw_title
    return found


def extract(html: str, recipe: dict[str, Any] | None = None) -> Found:
    """The chain, in order, stopping at the first step that yields a price.

    Later steps still contribute what the winner did not have: a JSON-LD price
    with no availability is completed from microdata rather than reported half
    empty. Only the price decides where to stop, because the price is the
    number the owner spends money on and the rest is context around it.
    """
    recipe = recipe or {}
    currency = recipe.get("currency")
    scanner = _Scanner()
    scan_notes: list[str] = []
    try:
        scanner.feed(html)
        scanner.close()
    except Exception as exc:  # a malformed page must not take the worker down
        # Steps 2 and 3 lose whatever came after the break; step 1 works off
        # the raw text and is unaffected, which is the other reason JSON-LD
        # goes first.
        scan_notes.append(f"html scan stopped early: {exc}")

    result = Found(notes=scan_notes)
    steps = (
        from_json_ld(html, currency),
        from_microdata(scanner, currency),
        from_meta(scanner, currency),
        from_selectors(html, recipe.get("extract") or {}, currency),
    )
    for step in steps:
        result.notes.extend(step.notes)
        if result.price is None and step.price is not None:
            result.price = step.price
            result.source = step.source
        if result.availability is None:
            result.availability = step.availability
        if result.title is None:
            result.title = step.title
    if result.title is None:
        result.title = scanner.title
    if result.price is None:
        result.source = None
    return result
