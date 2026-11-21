"""Deterministic output validation and rendering (docs/design/agent-design.md#output-guardrails).

Nothing the model writes reaches a customer until `validate` passes on the exact text to send.
Prices appear only as `{{quote:<id>}}` placeholders, rendered here from stored quotes.
"""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from policypilot.agents import numbers
from policypilot.db.models import Quote
from policypilot.i18n.formatting import format_money, render_premium
from policypilot.services.catalog import ProductView, mentioned_products

PLACEHOLDER = re.compile(r"\{\{\s*quote:([0-9a-fA-F-]{36})\s*\}\}")
CITATION = re.compile(r"\[doc:(\d+)\]")
MONEY = re.compile(
    r"(?:US\$|\$|USD\s?)\s?(\d[\d,]*(?:\.\d{1,2})?)\s?(k|m|thousand|million)?\b"
    r"|\b(\d[\d,]*(?:\.\d{1,2})?)\s?(?:USD|dollars)\b",
    re.IGNORECASE,
)
BANNED = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bguarantee(?:d|s)?\b",
        r"\byou(?:'re| are) (?:fully |definitely )?covered\b",
        r"\bbest price\b",
        r"\b(?:is|are|has been|have been|will be) approved\b",
        r"\bcheapest on the market\b",
        r"\brisk[- ]free\b",
    )
]
# "whether": a question about a claim is not the claim ("on whether the price is guaranteed").
NEGATION = re.compile(
    r"\b(not|no|never|isn't|aren't|cannot|can't|won't|whether)\b[\w ,']{0,25}$", re.I
)
MAX_LENGTH = {"web": 3000, "whatsapp": 4000}

# Number accounting (review 02, finding 1). Prices reach customers only through
# {{quote:<id>}} placeholders; every other number in a reply must be accounted for by the turn's
# facts and by what kind of number it is. Cue words decide the kind, per sentence.
PREMIUM_CUE = re.compile(
    r"\b(premiums?|costs?|costing|priced?|prices?|pricing|quoted?|quotes|charged?|charges|"
    r"pay|pays|paying|payments?|runs?|fees?|rates?)\b",
    re.I,
)
PERIOD_AFTER = re.compile(
    # No "^": used with match(text, pos), where "^" would only match at the start of `text`.
    r"\s*(?:(?:a|per|each|every|/)\s?(?:month|mo|year|yr|week|wk)\b|monthly|annually|yearly|"
    r"weekly|p\.?m\b|pcm\b)",
    re.I,
)
BUDGET_CUE = re.compile(r"\b(budget|budgets|afford|affordable|spend|spending)\b", re.I)
COVER_CUE = re.compile(
    r"\b(cover|coverage|covered|sum insured|death benefit|benefit amount|insured amount)\b", re.I
)
MONTH = re.compile(r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\b", re.I)
# Marks: the words right next to a number say what kind it is. A mark only matters for a number
# that is already accounted for (a fact, a count, a date, the cover goal); it lets that number
# appear in a sentence that also talks about prices. A price word right before the number
# ("costs 80", "the premium is about 80", "9 is the price") or a period right after it ("80 a
# month") makes it a price whatever else is near it, except a unit or an attribute noun directly
# attached ("pay a $6,000 deductible", "pay within 30 days").
#
# A quantity: a unit after the number, or a label or month name before it ("31 days", "a 30-year
# term", "clause 4.1", "aged 18 to 64", "Dec 20, 2026"). A list or range shares the mark of its
# marked end ("10, 20 and 30 year", "18 to 64").
UNIT_AFTER = re.compile(
    r"\s?(?:-\s?)?(?:days?|weeks?|months?|years?|hours?|miles?|km|kilomet(?:er|re)s?|"
    r"call-?outs?|times)\b",
    re.I,
)
LABEL_BEFORE = re.compile(
    r"\b(?:age|aged|ages|clause|clauses|section|sections|page|pages|"
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*(?:\s\d{1,2},)?)"
    r"(?:\s+(?:between|from|of|up to|over|under))?\s*$",
    re.I,
)
LIST_LINK = re.compile(r"^\s*(?:,|-|\bto\b|\band\b|\bor\b|\bthrough\b)\s*(?:\band\b|\bor\b)?\s*$")
# An attribute or cover noun attached to a money amount ("a $6,000 deductible", "the deductible
# is $6,000", "an out-of-pocket maximum of $9,000", "$500,000 of cover").
_KIND = (
    r"deductibles?|out-of-pocket(?:\s+(?:maximum|max|limit|cap))?|excess|co-?pay(?:ment)?s?|"
    r"coinsurance|limits?|cover|coverage|sum insured|death benefit|benefit amount"
)
KIND_AFTER = re.compile(  # modifiers allowed between: "$500,000 of 20-year term life cover"
    rf"\)?\s*(?:-\s*)?(?:of\s+)?(?:(?:\d+-\w+|term|life|whole|level|family|health|auto|car)\s+)"
    rf"{{0,3}}(?:{_KIND})\b"
    # A deductible in plain words: "you pay the first $1,000 of a claim", "$750 per claim".
    r"|\s+(?:of|on|per|for)\s+(?:(?:a|an|each|any|every|the|your)\s+)?"
    r"(?:(?:crash|collision|accident)\s+)?claims?\b",
    re.I,
)
KIND_BEFORE = re.compile(
    rf"(?:\b(?:{_KIND})\b(?:[\s,:(]|\b(?:is|was|are|of|at|from|to|up|amount|would|will|be|only|"
    rf"just|about|around)\b)*"
    r"|\bthe\s+first\s*)$",  # "you pay the first $750": a deductible, never a premium
    re.I,
)
# A sentence about a deductible-type term with no price noun in it: its attribute amounts are
# that term ("the collision deductible, which is what you'd pay towards a claim: $1,000 with
# Tollgate and $750 with Evergreen").
TERM_WORD = re.compile(
    r"\b(?:deductibles?|excess|out-of-pocket|co-?pay(?:ment)?s?|coinsurance)\b", re.I
)
PRICE_NOUN = re.compile(
    r"\b(?:premiums?|prices?|pricing|costs?|rates?|fees?|charges?|quotes?)\b", re.I
)
# A count: a determiner before a small whole number, or a plural noun or "of/that/which" after it
# ("the two prices", "these two", "three family health plans", "two of them", "recommend one").
COUNT_BEFORE = re.compile(
    r"\b(?:these|those|the|all|both|other|which|any|first|top|only|another|of)\s*$", re.I
)
COUNT_AFTER = re.compile(  # up to three modifiers: "two 20-year term life options"
    r"\s+(?:(?:of|that|which|who)\b|(?:(?:\d+-)?[a-z][a-z-]*\s+){0,3}([a-z][a-z-]*s)\b)", re.I
)
NOT_PLURAL = frozenset(
    {"is", "was", "has", "this", "plus", "less", "thus", "its", "as", "us", "yes", "does", "his"}
)
# A price word right before the number, joined only by linking words, or a singular price noun
# right after it.
CUE_BEFORE = re.compile(
    r"\b(?:premiums?|costs?|costing|priced?|prices?|pricing|quoted?|charged?|charges|pay|pays|"
    r"paying|payments?|runs?|fees?|rates?)\b(?:[\s,:~(]|\b(?:is|are|was|were|be|been|would|will|"
    r"could|should|of|at|about|around|roughly|approximately|only|just|under|below|over|above|"
    r"from|starting|starts|start|as|low|little|than|less|more|up|to|the|an?)\b)*$",
    re.I,
)
CUE_AFTER = re.compile(
    r"\s*(?:(?:is|was|would be|will be|as)\s+)?(?:(?:the|a|an|your|its|their|monthly|annual|"
    r"yearly)\s+){0,2}(?:premium|cost|price|rate|fee|charge|payment)\b(?!s)",
    re.I,
)
SENTENCE_END = re.compile(r"[.!?](?=\s|$)|\n")
SEGMENT_START = re.compile(r"[.!?](?:\s|$)|\n")


def money_to_minor(amount: str, suffix: str | None) -> int | None:
    try:
        value = Decimal(amount.replace(",", ""))
    except InvalidOperation:
        return None
    if suffix and suffix.lower() in ("k", "thousand"):
        value *= 1000
    if suffix and suffix.lower() in ("m", "million"):
        value *= 1_000_000
    return int((value * 100).to_integral_value())


def literal_amounts(text: str) -> list[int]:
    return [minor for minor, _, _ in literal_amount_spans(text)]


def literal_amount_spans(text: str) -> list[tuple[int, int, int]]:
    """(minor units, start, end) of every literal money amount outside placeholders."""
    blanked = PLACEHOLDER.sub(lambda m: " " * len(m.group(0)), text)  # keep positions
    spans: list[tuple[int, int, int]] = []
    for match in MONEY.finditer(blanked):
        raw, suffix = (match.group(1), match.group(2)) if match.group(1) else (match.group(3), None)
        minor = money_to_minor(raw, suffix)
        if minor is not None:
            spans.append((minor, match.start(), match.end()))
    return spans


def banned_claims(text: str) -> list[str]:
    found: list[str] = []
    for pattern in BANNED:
        for match in pattern.finditer(text):
            if NEGATION.search(text[max(0, match.start() - 40) : match.start()]):
                continue
            found.append(match.group(0).lower())
    return found


@dataclass
class TurnFacts:
    """What tools produced this turn: the only material a reply may rely on."""

    quote_ids: set[str]
    chunk_ids: set[int]
    chunk_products: dict[str, str]
    # Money (minor units) a reply may state outside a price sentence: product attributes such as
    # deductibles, and amounts in the policy text retrieved this turn.
    allowed_amounts: set[int]
    # Products the customer named: citations from any other product's wording are rejected.
    focus_product_ids: frozenset[str] = frozenset()
    policy_question: bool = False
    # The customer's own amounts: the budget only in a sentence about the budget, the cover goal
    # only next to cover words. Never as a statement about a product's price.
    budget_minor: int | None = None
    coverage_minor: int | None = None
    # Non-money numbers the turn can account for: profile values, attributes and texts of the
    # products involved, retrieved policy text, the field hints being asked.
    plain_numbers: frozenset[Decimal] = frozenset()
    # Templated drafts (fixed texts plus stored values) skip number accounting.
    check_numbers: bool = True
    # Quote id -> product id, and the catalog, to catch a price placed next to another product.
    quote_products: dict[str, str] = field(default_factory=dict)
    catalog: tuple[ProductView, ...] = ()


@dataclass
class Validation:
    problems: list[str] = field(default_factory=list)
    # The sentences behind the number problems, for the run trace (never shown to customers).
    evidence: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def validate(text: str, facts: TurnFacts, channel: str) -> Validation:
    result = Validation()
    for match in PLACEHOLDER.finditer(text):
        quote_id = match.group(1).lower()
        if quote_id not in facts.quote_ids:
            result.problems.append(f"unknown_quote_placeholder:{quote_id}")
        elif _misattributed(text, match.start(), quote_id, facts):
            result.problems.append(f"misattributed_quote:{quote_id}")
    if facts.check_numbers:
        result.problems += unaccounted_numbers(text, facts, result.evidence)
    cited = [int(c) for c in CITATION.findall(text)]
    for chunk_id in cited:
        if chunk_id not in facts.chunk_ids:
            result.problems.append(f"unknown_citation:{chunk_id}")
        elif facts.focus_product_ids and facts.chunk_products.get(str(chunk_id)) not in {
            None,
            *facts.focus_product_ids,
        }:
            result.problems.append(f"wrong_product_citation:{chunk_id}")
    if facts.policy_question and not any(c in facts.chunk_ids for c in cited):
        result.problems.append("policy_answer_without_citation")
    for claim in banned_claims(text):
        result.problems.append(f"banned_claim:{claim}")
    if len(text) > MAX_LENGTH.get(channel, 3000):
        result.problems.append("too_long")
    return result


def _blank(match: re.Match[str]) -> str:
    return " " * len(match.group(0))


def unaccounted_numbers(
    text: str, facts: TurnFacts, evidence: list[str] | None = None
) -> list[str]:
    """Every number in `text` that the turn's facts do not account for, as problem codes.

    Placeholders, citation markers and product names are blanked first (positions are kept),
    so the numbers in "Kestrel Term Life 20" or "[doc:12]" are not counted. The sentence behind
    each problem is added to `evidence` when given.
    """
    cleaned = CITATION.sub(_blank, PLACEHOLDER.sub(_blank, text))
    for product in facts.catalog:
        cleaned = re.sub(re.escape(product.name), _blank, cleaned, flags=re.IGNORECASE)
    problems: list[str] = []
    found = numbers.mentions(cleaned)
    quantities = _quantities(cleaned, found)
    for mention, quantity in zip(found, quantities, strict=True):
        known = len(problems)
        starts = [m.end() for m in SENTENCE_END.finditer(cleaned, 0, mention.start)]
        sentence_start = starts[-1] if starts else 0
        stop = SENTENCE_END.search(cleaned, mention.end)
        sentence_end = stop.start() if stop else len(cleaned)
        sentence = cleaned[sentence_start:sentence_end]
        before = cleaned[max(sentence_start, mention.start - 40) : mention.start]
        about_product = bool(
            facts.catalog and mentioned_products(text[sentence_start:sentence_end], facts.catalog)
        )
        value, minor = mention.value, mention.minor
        period = bool(PERIOD_AFTER.match(cleaned, mention.end))
        attached = bool(KIND_AFTER.match(cleaned, mention.end))  # "$6,000 deductible"
        in_terms = (
            minor in facts.allowed_amounts
            and bool(TERM_WORD.search(sentence))
            and not PRICE_NOUN.search(sentence)
        )
        kind = attached or in_terms or bool(KIND_BEFORE.search(before))
        cover_for = minor == facts.coverage_minor and bool(re.search(r"\bfor\s*$", before))
        count = not mention.currency and _is_count(cleaned, mention, before)
        direct = bool(CUE_BEFORE.search(before)) or bool(CUE_AFTER.match(cleaned, mention.end))
        price = (
            period
            or (direct and not (quantity or attached))
            or (bool(PREMIUM_CUE.search(sentence)) and not (quantity or kind or count or cover_for))
        )
        budget_sentence = bool(BUDGET_CUE.search(sentence)) and not about_product
        cover_goal = minor == facts.coverage_minor and (
            bool(COVER_CUE.search(sentence)) or kind or cover_for
        )

        if mention.percent:
            if value not in facts.plain_numbers:
                problems.append(f"unverified_percent:{value}")
        elif mention.currency or price:  # money
            if minor == facts.budget_minor:
                if not budget_sentence:
                    problems.append(f"budget_outside_budget_sentence:{minor}")
            elif price:
                problems.append(f"unverified_premium:{minor}")
            elif not cover_goal and minor not in facts.allowed_amounts:
                problems.append(f"unverified_amount:{minor}")
        elif not (
            value in facts.plain_numbers
            or (value == value.to_integral_value() and 0 <= value <= 12)  # a count
            or _is_date_part(cleaned, mention)
            or (minor == facts.budget_minor and budget_sentence)
            or cover_goal
            or minor in facts.allowed_amounts
        ):
            problems.append(f"unverified_number:{value}")
        if evidence is not None and len(problems) > known:
            evidence.append(text[sentence_start:sentence_end].strip()[:160])
    return problems


def _quantities(text: str, found: list[numbers.Mention]) -> list[bool]:
    """Per mention: a quantity, by its own unit or label, or by a list or range it belongs to."""
    marks = [
        not m.currency
        and (
            bool(UNIT_AFTER.match(text, m.end))
            or bool(LABEL_BEFORE.search(text, max(0, m.start - 30), m.start))
        )
        for m in found
    ]
    linked = [
        not a.currency and not b.currency and bool(LIST_LINK.match(text[a.end : b.start]))
        for a, b in itertools.pairwise(found)
    ]
    for i in reversed(range(len(linked))):  # "10, 20 and 30 year": the unit at the end
        marks[i] = marks[i] or (linked[i] and marks[i + 1])
    for i in range(len(linked)):  # "aged 18 to 64": the label at the start
        marks[i + 1] = marks[i + 1] or (linked[i] and marks[i])
    return marks


def _is_count(text: str, mention: numbers.Mention, before: str) -> bool:
    """A small whole number used as a count: "the two prices", "three plans", "recommend one"."""
    if mention.value != mention.value.to_integral_value() or not 0 <= mention.value <= 12:
        return False
    if mention.words and mention.value == 1:
        return True
    if COUNT_BEFORE.search(before):
        return True
    after = COUNT_AFTER.match(text, mention.end)
    return after is not None and (after.group(1) or "of").lower() not in NOT_PLURAL


def _is_date_part(text: str, mention: numbers.Mention) -> bool:
    if mention.value == mention.value.to_integral_value() and 1900 <= mention.value <= 2100:
        return True  # a year
    window = text[max(0, mention.start - 12) : mention.end + 12]
    return bool(MONTH.search(window)) and 1 <= mention.value <= 31


def _misattributed(text: str, position: int, quote_id: str, facts: TurnFacts) -> bool:
    """A placeholder whose line or sentence names products, none of them the quote's product."""
    if not facts.catalog or quote_id not in facts.quote_products:
        return False
    starts = [m.end() for m in SEGMENT_START.finditer(text, 0, position)]
    segment = PLACEHOLDER.sub(" ", text[starts[-1] if starts else 0 : position])
    named = {str(p.id) for p in mentioned_products(segment, facts.catalog)}
    return bool(named) and facts.quote_products[quote_id] not in named


def render_quote(quote: Quote, locale: str) -> str:
    headline = render_premium(
        quote.premium_minor,
        quote.monthly_equivalent_minor,
        quote.currency,
        quote.billing_period,
        locale,
    )
    variants = {v["key"]: v["premium_minor"] for v in quote.variants.get("items", [])}
    if "smoker" in variants and variants.get("non_smoker") == quote.premium_minor:
        smoker = format_money(variants["smoker"], quote.currency, locale)
        return f"{headline} for non-smokers ({smoker} per month for smokers)"
    return headline


def render(
    text: str, quotes: dict[str, Quote], chunks: dict[int, dict[str, Any]], locale: str
) -> tuple[str, list[dict[str, Any]]]:
    """Fill price placeholders and turn [doc:N] markers into numbered references."""
    rendered = PLACEHOLDER.sub(lambda m: render_quote(quotes[m.group(1).lower()], locale), text)
    order: list[int] = []

    def number(match: re.Match[str]) -> str:
        chunk_id = int(match.group(1))
        if chunk_id not in order:
            order.append(chunk_id)
        return f"[{order.index(chunk_id) + 1}]"

    rendered = CITATION.sub(number, rendered)
    citations = [
        {
            "chunkId": chunk_id,
            "documentTitle": chunks[chunk_id]["documentTitle"],
            "section": chunks[chunk_id]["section"],
            "pages": chunks[chunk_id]["pages"],
        }
        for chunk_id in order
        if chunk_id in chunks
    ]
    return re.sub(r"[ \t]+\n", "\n", rendered).strip(), citations
