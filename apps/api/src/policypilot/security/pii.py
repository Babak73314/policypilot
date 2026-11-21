"""Deterministic detectors for sensitive identifiers and PII masking.

See docs/design/security-and-privacy.md.

Two uses:
- `redact_sensitive` replaces card, IBAN and national-ID-like numbers with `[REDACTED:<type>]`
  before text is stored, logged, traced or sent to a model (guard_input).
- `mask_pii` additionally masks emails and phone numbers; it is used for logs and traces only.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

_CARD_CANDIDATE = re.compile(r"(?<![\w-])(?:\d[ -]?){12,18}\d(?![\w-])")
_IBAN_CANDIDATE = re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){2,7}(?:[ ]?[A-Z0-9]{1,4})?\b")
_SSN = re.compile(r"\b(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b")
_ID_KEYWORD = re.compile(
    r"(?i)\b(passport|national id|id number|identity number|driver'?s licen[cs]e|ssn|"
    r"social security)\b[^\d\n\[]{0,20}(?!\[REDACTED)"
    r"((?=[A-Z0-9 -]*\d)[A-Z0-9][A-Z0-9 -]{5,18}[A-Z0-9])"
)
_EMAIL = re.compile(r"\b([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")
_PHONE = re.compile(r"(?<![\w])(\+?\d[\d ().-]{7,}\d)(?![\w])")

_IBAN_LENGTHS = {
    "DE": 22, "FR": 27, "GB": 22, "NL": 18, "ES": 24, "IT": 27, "BE": 16, "AT": 20,
    "CH": 21, "IE": 22, "PT": 25, "SE": 24, "NO": 15, "DK": 18, "FI": 18, "PL": 28,
    "TR": 26, "LU": 20,
}  # fmt: skip


def luhn_valid(digits: str) -> bool:
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def iban_valid(candidate: str) -> bool:
    iban = candidate.replace(" ", "").upper()
    expected = _IBAN_LENGTHS.get(iban[:2])
    if expected is None or len(iban) != expected:
        return False
    rearranged = iban[4:] + iban[:4]
    numeric = "".join(str(int(ch, 36)) for ch in rearranged)
    return int(numeric) % 97 == 1


@dataclass
class RedactionResult:
    text: str
    counts: Counter[str] = field(default_factory=Counter)

    @property
    def redacted(self) -> bool:
        return bool(self.counts)


def redact_sensitive(text: str) -> RedactionResult:
    """Replace sensitive identifiers. Never returns or logs the original values."""
    counts: Counter[str] = Counter()

    def card(match: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", match.group(0))
        if 13 <= len(digits) <= 19 and digits[0] != "0" and luhn_valid(digits):
            counts["card"] += 1
            return "[REDACTED:card]"
        return match.group(0)

    def iban(match: re.Match[str]) -> str:
        if iban_valid(match.group(0)):
            counts["iban"] += 1
            return "[REDACTED:iban]"
        return match.group(0)

    def ssn(_: re.Match[str]) -> str:
        counts["national_id"] += 1
        return "[REDACTED:national_id]"

    def keyword_id(match: re.Match[str]) -> str:
        counts["national_id"] += 1
        return match.group(0).replace(match.group(2), "[REDACTED:national_id]")

    result = _IBAN_CANDIDATE.sub(iban, text)
    result = _CARD_CANDIDATE.sub(card, result)
    result = _SSN.sub(ssn, result)
    result = _ID_KEYWORD.sub(keyword_id, result)
    return RedactionResult(text=result, counts=counts)


def mask_email(match: re.Match[str]) -> str:
    return f"{match.group(1)}***@{match.group(2)}"


def mask_phone(match: re.Match[str]) -> str:
    raw = match.group(1)
    digits = re.sub(r"\D", "", raw)
    if len(digits) < 8 or "REDACTED" in raw:
        return raw
    prefix = "+" if raw.startswith("+") else ""
    return f"{prefix}{'*' * (len(digits) - 4)}{digits[-4:]}"


def mask_pii(text: str) -> str:
    """For logs and traces: sensitive identifiers plus emails and phone numbers."""
    masked = redact_sensitive(text).text
    masked = _EMAIL.sub(mask_email, masked)
    return _PHONE.sub(mask_phone, masked)
