"""Catalog reads: products with localized texts, current rate tables, discovery search."""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from pydantic import TypeAdapter
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from policypilot.db.models import Carrier, Product, ProductText, RateTable
from policypilot.domain.rating.models import RateTable as RateTableModel

_RATE_TABLE: TypeAdapter[Any] = TypeAdapter(RateTableModel)


@dataclass(frozen=True)
class ProductView:
    id: uuid.UUID
    code: str
    line: str
    carrier_code: str
    carrier_name: str
    name: str
    summary: str
    highlights: list[str]
    exclusions_summary: str
    attributes: dict[str, Any]
    currency: str
    billing_period: str
    active: bool

    def brief(self) -> dict[str, Any]:
        """What tools return to the model: no prices, no rate factors."""
        return {
            "product_id": str(self.id),
            "code": self.code,
            "carrier": self.carrier_name,
            "name": self.name,
            "line": self.line,
            "billing_period": self.billing_period,
            "attributes": self.attributes,
            "summary": self.summary,
        }


_NAME_WORD = re.compile(r"[a-z0-9]+")
# Words that every product of a line shares; they cannot tell products apart.
_SHARED_NAME_WORDS = frozenset(
    {"term", "life", "health", "auto", "insurance", "plan", "policy", "plus", "the"}
)


def mentioned_products(
    text: str, products: Sequence[ProductView], line: str | None = None
) -> list[ProductView]:
    """The products a customer names, so policy questions search the right wording.

    A carrier name is required: words such as "family", "standard" or "gold" alone are too
    common. Further name words ("gold", "20", "comprehensive") narrow the carrier's products down;
    the best-scoring products are returned (several when the message stays ambiguous). The line,
    when known, narrows the choice only if it leaves a candidate.
    """
    words = set(_NAME_WORD.findall(text.lower()))
    by_carrier = [p for p in products if _carrier_word(p) in words]
    in_line = [p for p in by_carrier if line and p.line == line]
    candidates = in_line or by_carrier
    if not candidates:
        return []

    def score(product: ProductView) -> int:
        name_words = set(_NAME_WORD.findall(product.name.lower()))
        return len((name_words - _SHARED_NAME_WORDS - {_carrier_word(product)}) & words)

    best = max(score(p) for p in candidates)
    return [p for p in candidates if score(p) == best]


def _carrier_word(product: ProductView) -> str:
    return product.carrier_name.split()[0].lower()


async def list_products(
    session: AsyncSession,
    *,
    line: str | None = None,
    locale: str = "en",
    active_only: bool = True,
    product_ids: list[uuid.UUID] | None = None,
) -> list[ProductView]:
    stmt = select(Product, Carrier).join(Carrier, Carrier.id == Product.carrier_id)
    if line:
        stmt = stmt.where(Product.line == line)
    if active_only:
        stmt = stmt.where(Product.active.is_(True))
    if product_ids is not None:
        stmt = stmt.where(Product.id.in_(product_ids))
    rows = (await session.execute(stmt.order_by(Product.line, Product.code))).all()
    if not rows:
        return []
    ids = [product.id for product, _ in rows]
    texts = (
        await session.execute(
            select(ProductText).where(
                ProductText.product_id.in_(ids), ProductText.locale.in_([locale, "en"])
            )
        )
    ).scalars()
    by_product: dict[uuid.UUID, dict[str, ProductText]] = {}
    for item in texts:
        by_product.setdefault(item.product_id, {})[item.locale] = item
    views: list[ProductView] = []
    for product, carrier in rows:
        localized = by_product.get(product.id, {})
        text_row = localized.get(locale) or localized["en"]
        views.append(
            ProductView(
                id=product.id,
                code=product.code,
                line=product.line,
                carrier_code=carrier.code,
                carrier_name=carrier.name,
                name=text_row.name,
                summary=text_row.summary,
                highlights=list(text_row.highlights),
                exclusions_summary=text_row.exclusions_summary,
                attributes=dict(product.attributes),
                currency=product.currency,
                billing_period=product.billing_period,
                active=product.active,
            )
        )
    return views


async def get_product(
    session: AsyncSession, product_id: uuid.UUID, locale: str = "en"
) -> ProductView | None:
    views = await list_products(session, locale=locale, active_only=False, product_ids=[product_id])
    return views[0] if views else None


async def current_rate_table(
    session: AsyncSession, product_id: uuid.UUID, at: datetime | None = None
) -> RateTable | None:
    """The published table effective at `at` (data-model.md selection rule)."""
    day: date = (at or datetime.now(UTC)).date()
    stmt = (
        select(RateTable)
        .where(
            RateTable.product_id == product_id,
            RateTable.published_at.is_not(None),
            RateTable.effective_from <= day,
            or_(RateTable.effective_to.is_(None), and_(RateTable.effective_to > day)),
        )
        .order_by(RateTable.effective_from.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


def parse_factors(table: RateTable) -> Any:
    return _RATE_TABLE.validate_python(table.factors)


def matches_must_have(view: ProductView, must_have: list[str]) -> bool:
    for key in must_have:
        value = view.attributes.get(key)
        if value in (None, False, 0, ""):
            return False
    return True


async def search_products(
    session: AsyncSession,
    *,
    line: str,
    locale: str = "en",
    must_have: list[str] | None = None,
    limit: int = 6,
) -> list[ProductView]:
    views = await list_products(session, line=line, locale=locale)
    if must_have:
        views = [v for v in views if matches_must_have(v, must_have)]
    return views[: max(1, min(limit, 10))]
