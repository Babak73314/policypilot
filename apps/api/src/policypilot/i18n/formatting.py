"""Locale-aware formatting (ADR-0013).

Amounts travel as minor units plus a currency code; customer text is built here.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from babel.dates import format_date
from babel.numbers import format_currency, get_currency_precision


def babel_locale(locale: str) -> str:
    """BCP 47 ("en", "en-US") to a Babel identifier; English defaults to en_US for the demo."""
    normalised = locale.replace("-", "_")
    return "en_US" if normalised == "en" else normalised


def minor_to_decimal(amount_minor: int, currency: str) -> Decimal:
    precision = get_currency_precision(currency)
    return Decimal(amount_minor) / (Decimal(10) ** precision)


def format_money(amount_minor: int, currency: str, locale: str) -> str:
    return format_currency(
        minor_to_decimal(amount_minor, currency), currency, locale=babel_locale(locale)
    )


def format_day(value: datetime, locale: str) -> str:
    return format_date(value.date(), format="medium", locale=babel_locale(locale))


_PERIOD_LABELS = {
    "en": {
        "monthly": "per month",
        "annual": "per year",
        "about_monthly": "about {amount} per month",
        "estimate": "estimate",
    }
}


def period_label(key: str, locale: str) -> str:
    language = locale.split("-")[0]
    return _PERIOD_LABELS.get(language, _PERIOD_LABELS["en"])[key]


def render_premium(
    premium_minor: int,
    monthly_equivalent_minor: int,
    currency: str,
    billing_period: str,
    locale: str,
) -> str:
    """The only way a premium is written in customer text (ADR-0003)."""
    premium = format_money(premium_minor, currency, locale)
    estimate = period_label("estimate", locale)
    if billing_period == "annual":
        monthly = format_money(monthly_equivalent_minor, currency, locale)
        about = period_label("about_monthly", locale).format(amount=monthly)
        return f"{premium} {period_label('annual', locale)} ({about}, {estimate})"
    return f"{premium} {period_label('monthly', locale)} ({estimate})"
