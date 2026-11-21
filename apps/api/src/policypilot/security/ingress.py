"""Ingress sanitisation: the single step every inbound text goes through first.

It runs before anything is stored, logged, traced, checkpointed or sent to a model
(docs/design/security-and-privacy.md). It produces:

- ``stored_text``: sensitive identifiers and volunteered health details removed;
- ``model_text``: ``stored_text`` with emails and phone numbers also masked - the only text the
  graph and the models ever see;
- ``redactions``: types and counts, never values.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from typing import Literal

from policypilot.security.pii import redact_sensitive

Source = Literal["customer", "agent", "webhook", "mcp", "integration", "review_note"]

_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_PHONE = re.compile(r"(?<![\w\[])(\+?\d[\d ().-]{6,}\d)(?![\w\]])")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SENTENCE = re.compile(r"[^.!?\n]+[.!?]*\n?|\n")

_MEDICAL = re.compile(
    r"(?i)\b("
    r"diabet\w*|cancer|tumou?r\w*|chemo\w*|asthma\w*|copd|hypertension|high blood pressure|"
    r"heart (?:disease|condition|attack|failure)|stroke|hiv|aids|hepatitis|depress\w*|"
    r"anxiety disorder|bipolar|schizophreni\w*|epilep\w*|seizures?|kidney disease|dialysis|"
    r"multiple sclerosis|parkinson\w*|alzheimer\w*|dementia|arthritis|pregnan\w*|insulin|"
    r"metformin|cholesterol|sleep apnea|crohn\w*|coeliac|celiac|lupus|smoker|smoking|"
    r"diagnos\w*|medication|prescri\w*|surgery|operation"
    r")\b"
)
_DISCLOSURE = re.compile(
    r"(?i)\b(i|i'm|im|i've|ive|i am|i have|i had|we|my|our|he|she|his|her|they|their)\b"
)


@dataclass(frozen=True)
class SanitizedText:
    stored_text: str
    model_text: str
    redactions: dict[str, int] = field(default_factory=dict)

    @property
    def redacted(self) -> bool:
        return bool(self.redactions)

    @property
    def health_removed(self) -> bool:
        return self.redactions.get("health", 0) > 0

    @property
    def sensitive_removed(self) -> bool:
        return any(k in self.redactions for k in ("card", "iban", "national_id"))


def _normalise(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    text = _CONTROL.sub("", text)
    return text.strip()


def _remove_health_disclosures(text: str, counts: Counter[str]) -> str:
    """Replace each sentence that discloses a personal health detail with a marker.

    Deliberately conservative: a medical term AND a personal reference in the same sentence.
    General coverage questions ("does Gold cover cancer treatment?") are kept.
    """
    parts: list[str] = []
    for match in _SENTENCE.finditer(text):
        sentence = match.group(0)
        if _MEDICAL.search(sentence) and _DISCLOSURE.search(sentence):
            counts["health"] += 1
            trailing = "\n" if sentence.endswith("\n") else " "
            parts.append("[REDACTED:health]" + trailing)
        else:
            parts.append(sentence)
    return "".join(parts).strip()


def sanitize(text: str, *, source: Source = "customer") -> SanitizedText:
    """Sanitise inbound text from any source (customer, webhook, agent, MCP, integration, review
    note): identifiers and personal health disclosures are removed before anything is stored."""
    normalised = _normalise(text)
    sensitive = redact_sensitive(normalised)
    counts: Counter[str] = Counter(sensitive.counts)
    stored = _remove_health_disclosures(sensitive.text, counts)

    model = _EMAIL.sub("[EMAIL]", stored)
    model = _PHONE.sub(
        lambda m: "[PHONE]" if len(re.sub(r"\D", "", m.group(1))) >= 7 else m.group(1), model
    )
    return SanitizedText(stored_text=stored, model_text=model, redactions=dict(counts))
