"""Every number in a text, and what kind of number it is.

The output guardrail accounts for each number a model writes (guardrails.validate). That only
works if nothing escapes extraction, so this module finds digits with or without a currency sign
("$80", "80", "80k", "1,200.50", "80 bucks", "20%") and numbers written in words ("eighty",
"one hundred and twenty", "five hundred thousand").
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19,
}  # fmt: skip
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
    "eighty": 80, "ninety": 90,
}  # fmt: skip
_SCALES = {"hundred": 100, "thousand": 1_000, "million": 1_000_000}
_WORD = "|".join(sorted([*_UNITS, *_TENS, *_SCALES], key=len, reverse=True))
_WORDS = re.compile(rf"\b(?:{_WORD})(?:[\s-]+(?:and[\s-]+)?(?:{_WORD}))*\b", re.I)
# Deliberately greedy: a number glued to a comma or a word ("18,65", "80bucks") is still found.
# Only digits inside an identifier (a letter directly before them) are skipped.
_DIGITS = re.compile(
    r"(?<![A-Za-z$])(?P<cur>US\$|\$|USD\s?)?\s?"
    r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"(?:\s?(?P<suf>k|m|thousand|million)\b)?",
    re.I,
)
_CURRENCY_AFTER = re.compile(r"^\s?(?:\$|dollars?|bucks|cents?|usd)", re.I)
_PERCENT_AFTER = re.compile(r"^\s?(?:%|percent\b|per cent\b)", re.I)
_SUFFIX = {"k": 1_000, "thousand": 1_000, "m": 1_000_000, "million": 1_000_000}


@dataclass(frozen=True)
class Mention:
    value: Decimal
    start: int
    end: int
    currency: bool
    percent: bool
    words: bool

    @property
    def minor(self) -> int:
        """The value as money in minor units (cents)."""
        return int((self.value * 100).to_integral_value())


def _words_value(phrase: str) -> Decimal:
    total, current = 0, 0
    for word in re.split(r"[\s-]+", phrase.lower()):
        if word in _UNITS:
            current += _UNITS[word]
        elif word in _TENS:
            current += _TENS[word]
        elif word in _SCALES and _SCALES[word] == 100:
            current = max(current, 1) * 100
        elif word in _SCALES:
            total += max(current, 1) * _SCALES[word]
            current = 0
    return Decimal(total + current)


def mentions(text: str) -> list[Mention]:
    """All numbers in `text`, in order. Positions refer to `text`."""
    found: list[Mention] = []
    for match in _DIGITS.finditer(text):
        try:
            value = Decimal(match.group("num").replace(",", ""))
        except InvalidOperation:  # pragma: no cover - the pattern only matches numbers
            continue
        if match.group("suf"):
            value *= _SUFFIX[match.group("suf").lower()]
        after = text[match.end() : match.end() + 12]
        found.append(
            Mention(
                value=value,
                start=match.start(),
                end=match.end(),
                currency=bool(match.group("cur")) or bool(_CURRENCY_AFTER.match(after)),
                percent=bool(_PERCENT_AFTER.match(after)),
                words=False,
            )
        )
    for match in _WORDS.finditer(text):
        after = text[match.end() : match.end() + 12]
        found.append(
            Mention(
                value=_words_value(match.group(0)),
                start=match.start(),
                end=match.end(),
                currency=bool(_CURRENCY_AFTER.match(after)),
                percent=bool(_PERCENT_AFTER.match(after)),
                words=True,
            )
        )
    return sorted(found, key=lambda m: m.start)


def numbers_in(text: str) -> tuple[set[Decimal], set[int]]:
    """(every number, the money amounts in minor units) stated in a trusted text."""
    plain: set[Decimal] = set()
    money: set[int] = set()
    for mention in mentions(text):
        plain.add(mention.value)
        if mention.currency:
            money.add(mention.minor)
    return plain, money
