"""Turning what a shop printed into a number, or refusing to.

This is small and it is the most dangerous file in the worker. Everything else
either works or fails loudly; a money parser fails *quietly*, by returning a
number that is wrong by a factor of a thousand. "1.299" is 1299 euro in Italy
and 1.299 dollars nowhere. ROT.md rule 6 applies: a figure that does not add up
is information, and a figure quietly adjusted is a lie. So every ambiguous case
here resolves by refusing rather than guessing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# The symbols and codes worth knowing about, longest match first so "CHF" is
# not read as "C". Anything not here leaves the currency unknown, which is a
# reportable outcome and not an error.
_CURRENCIES: tuple[tuple[str, str], ...] = (
    ("EUR", "EUR"), ("USD", "USD"), ("GBP", "GBP"), ("CHF", "CHF"),
    ("€", "EUR"), ("$", "USD"), ("£", "GBP"), ("¥", "JPY"),
)

# Every space a web page might use between the number and the symbol:
# ordinary, non-breaking, narrow non-breaking, thin.
_SPACES = "    "
_SPACE_RE = re.compile(f"[{_SPACES}]+")

# A run of digits with optional . , or space grouping inside it. Deliberately
# does not anchor: prices arrive embedded in "ab 1.234,56 € inkl. MwSt".
_NUMBER_RE = re.compile(r"\d[\d.,    ]*\d|\d")


class MoneyError(ValueError):
    """The text could not be read as money without guessing."""


@dataclass(frozen=True)
class Money:
    amount: float
    currency: str | None

    def __str__(self) -> str:
        return f"{self.amount:.2f} {self.currency or '?'}"


def find_currency(text: str) -> str | None:
    """The currency named in the text, or None. First match wins."""
    upper = text.upper()
    best: tuple[int, str] | None = None
    for token, code in _CURRENCIES:
        index = upper.find(token)
        if index >= 0 and (best is None or index < best[0]):
            best = (index, code)
    return best[1] if best else None


def _digits_only(chunk: str) -> str:
    return _SPACE_RE.sub("", chunk).replace(" ", "")


def parse_amount(raw: str) -> float:
    """The number in `raw`, or MoneyError.

    The whole problem is which of `.` and `,` is the decimal point, and the
    answer is positional, not regional -- we do not know the shop's locale and
    guessing one is how a German page gets read with American rules.

    Both present: the rightmost is the decimal separator. "1.234,56" and
    "1,234.56" are both 1234.56 and no locale knowledge is needed to see it.

    One present, more than once: it groups thousands. "1.234.567" is 1234567.

    One present, once: the digit count after it decides. Two digits is money
    ("39,90" -> 39.90). Exactly three is a thousands group ("1.299" -> 1299),
    which is the case that costs a factor of a thousand if got wrong, and the
    reason a shop writing a genuine 1.299 dollars would not be understood --
    no shop does. One digit is money ("39.9"). Four or more is not a number a
    price is written as, so it is refused.
    """
    text = _digits_only(raw)
    if not text:
        raise MoneyError("no digits")
    match = _NUMBER_RE.search(text)
    if match is None:
        raise MoneyError(f"no number in {raw!r}")
    chunk = _digits_only(match.group(0))

    dots = chunk.count(".")
    commas = chunk.count(",")

    if dots and commas:
        decimal = "." if chunk.rfind(".") > chunk.rfind(",") else ","
        thousands = "," if decimal == "." else "."
        # The grouping separator must actually group: "1.23,456" is not money.
        if any(len(part) != 3 for part in chunk.split(decimal)[0].split(thousands)[1:]):
            raise MoneyError(f"inconsistent grouping in {raw!r}")
        chunk = chunk.replace(thousands, "").replace(decimal, ".")
    elif dots or commas:
        separator = "." if dots else ","
        parts = chunk.split(separator)
        tail = parts[-1]
        if len(parts) > 2:
            if any(len(part) != 3 for part in parts[1:]):
                raise MoneyError(f"inconsistent grouping in {raw!r}")
            chunk = chunk.replace(separator, "")
        elif len(tail) == 3:
            chunk = chunk.replace(separator, "")
        elif len(tail) in (1, 2):
            chunk = chunk.replace(separator, ".")
        else:
            raise MoneyError(f"cannot tell decimals from grouping in {raw!r}")

    try:
        value = float(chunk)
    except ValueError as exc:
        raise MoneyError(f"not a number: {raw!r}") from exc
    if value < 0:
        raise MoneyError(f"negative price in {raw!r}")
    return value


def parse(raw: str, currency: str | None = None) -> Money:
    """Money out of a string, or MoneyError.

    `currency` is the fallback when the text does not name one -- the recipe
    records what the domain trades in, so a bare "39,90" from a German shop is
    still EUR. A currency in the text always wins over it: if the page says USD
    and the recipe says EUR, the page is right and the recipe is stale.
    """
    amount = parse_amount(raw)
    return Money(amount, find_currency(raw) or currency)


def plausible(amount: float, low: float = 0.01, high: float = 1_000_000.0) -> bool:
    """Is this a number a shop could have meant?

    Used to validate an LLM-proposed selector before it is committed: a
    selector that matches a review count or a postcode yields a number, and
    "it returned a number" is not evidence that it returned a price. Zero is
    excluded deliberately -- tradeinn.com's placeholder "Price without code 0"
    on 2026-09-04 is exactly the failure this catches.
    """
    return low <= amount <= high
