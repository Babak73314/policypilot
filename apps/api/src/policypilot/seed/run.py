"""`policypilot seed`: idempotent load of the fictional catalog and documents.

Also creates the demo agent and registers local API tokens from the environment.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import structlog
from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from policypilot.config import Settings
from policypilot.db.models import (
    AgentUser,
    ApiToken,
    Carrier,
    DocumentChunk,
    MessageTemplate,
    PolicyDocument,
    Product,
    ProductText,
    RateTable,
)
from policypilot.db.session import Database
from policypilot.rag.chunking import chunk_pages, embedding_text
from policypilot.rag.embeddings import Embedder
from policypilot.rag.pdf import document_title, document_version, extract_pages, render_markdown_pdf
from policypilot.security.auth import hash_api_token, hash_password
from policypilot.seed.loader import CatalogSeed, default_seed_dir, load_catalog

log = structlog.get_logger(__name__)


@dataclass
class SeedReport:
    carriers: int = 0
    products: int = 0
    rate_tables_added: int = 0
    templates: int = 0
    documents_ingested: list[str] = field(default_factory=list)
    documents_unchanged: int = 0
    agent_created: bool = False
    tokens: list[str] = field(default_factory=list)


def _checksum(payload: dict[str, object]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


async def _seed_catalog(session: AsyncSession, catalog: CatalogSeed, report: SeedReport) -> None:
    for carrier in catalog.carriers:
        await session.execute(
            insert(Carrier)
            .values(**carrier.model_dump())
            .on_conflict_do_update(
                index_elements=["code"],
                set_={
                    "name": carrier.name,
                    "financial_strength": carrier.financial_strength,
                    "website": carrier.website,
                },
            )
        )
        report.carriers += 1
    carrier_ids = {c.code: c.id for c in (await session.execute(select(Carrier))).scalars()}

    for product in catalog.products:
        await session.execute(
            insert(Product)
            .values(
                carrier_id=carrier_ids[product.carrier],
                code=product.code,
                line=product.line,
                currency=product.currency,
                billing_period=product.billing_period,
                attributes=product.attributes,
            )
            .on_conflict_do_update(
                index_elements=["code"],
                set_={"attributes": product.attributes, "billing_period": product.billing_period},
            )
        )
        product_id = (
            await session.execute(select(Product.id).where(Product.code == product.code))
        ).scalar_one()
        for locale, texts in product.texts.items():
            await session.execute(
                insert(ProductText)
                .values(product_id=product_id, locale=locale, **texts.model_dump())
                .on_conflict_do_update(
                    index_elements=["product_id", "locale"], set_=texts.model_dump()
                )
            )
        factors = product.rate_table.factors.model_dump(mode="json")
        exists = (
            await session.execute(
                select(RateTable.id).where(
                    RateTable.product_id == product_id,
                    RateTable.version == product.rate_table.version,
                )
            )
        ).scalar_one_or_none()
        if exists is None:
            session.add(
                RateTable(
                    product_id=product_id,
                    version=product.rate_table.version,
                    effective_from=product.rate_table.effective_from,
                    factors=factors,
                    checksum=_checksum(factors),
                    published_at=datetime.now(UTC),
                )
            )
            report.rate_tables_added += 1
        report.products += 1

    for template in catalog.templates:
        values = template.model_dump()
        await session.execute(
            insert(MessageTemplate)
            .values(**values)
            .on_conflict_do_update(
                index_elements=["key", "channel", "locale"],
                set_={k: v for k, v in values.items() if k not in ("key", "channel", "locale")},
            )
        )
        report.templates += 1


async def ingest_documents(
    db: Database,
    embedder: Embedder,
    documents_dir: Path,
    output_dir: Path,
    report: SeedReport,
    locale: str = "en",
    *,
    reindex: bool = False,
) -> None:
    """Render each wording to PDF, parse it back, chunk, embed; store a version if changed.

    `reindex` re-embeds every document even when its text and embedding model are unchanged
    (after a chunking or embedding-text change, for example).
    """
    async with db.transaction() as session:
        products = {p.code: p.id for p in (await session.execute(select(Product))).scalars()}
    sources = await asyncio.to_thread(lambda: sorted(documents_dir.glob("*.md")))
    for source in sources:
        code = source.stem
        if code not in products:
            log.warning("seed.document_without_product", code=code)
            continue
        markdown = await asyncio.to_thread(source.read_text, encoding="utf-8")
        sha = hashlib.sha256(markdown.encode()).hexdigest()
        async with db.session() as session:
            current = (
                await session.execute(
                    select(PolicyDocument).where(
                        PolicyDocument.product_id == products[code],
                        PolicyDocument.locale == locale,
                        PolicyDocument.is_current.is_(True),
                    )
                )
            ).scalar_one_or_none()
        if (
            not reindex
            and current is not None
            and current.sha256 == sha
            and await _document_uses_model(db, current.id, embedder.model_name)
        ):
            report.documents_unchanged += 1
            continue
        pdf_path = output_dir / f"{code}.pdf"
        page_count = render_markdown_pdf(markdown, pdf_path)
        chunks = chunk_pages(extract_pages(pdf_path))
        title = document_title(markdown)
        vectors = await embedder.embed_documents([embedding_text(title, c) for c in chunks])
        async with db.transaction() as session:
            await session.execute(
                update(PolicyDocument)
                .where(
                    PolicyDocument.product_id == products[code],
                    PolicyDocument.locale == locale,
                    PolicyDocument.is_current.is_(True),
                )
                .values(is_current=False)
            )
            existing = (
                await session.execute(
                    select(PolicyDocument).where(
                        PolicyDocument.product_id == products[code],
                        PolicyDocument.locale == locale,
                        PolicyDocument.sha256 == sha,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                existing.is_current = True
                stored = list(
                    (
                        await session.execute(
                            select(DocumentChunk)
                            .where(DocumentChunk.document_id == existing.id)
                            .order_by(DocumentChunk.chunk_index)
                        )
                    ).scalars()
                )
                if [c.content for c in stored] == [c.content for c in chunks]:
                    # Same wording and chunks: update the vectors in place, so chunk ids (the
                    # citation ids in stored messages) stay valid (review 02, finding 21).
                    for row, vector in zip(stored, vectors, strict=True):
                        row.embedding = vector
                        row.embedding_model = embedder.model_name
                    report.documents_ingested.append(code)
                    continue
                # The chunking changed: the old chunk ids cannot be kept.
                await session.execute(
                    delete(DocumentChunk).where(DocumentChunk.document_id == existing.id)
                )
                document = existing
            else:
                document = PolicyDocument(
                    product_id=products[code],
                    locale=locale,
                    title=title,
                    version=document_version(markdown),
                    sha256=sha,
                    source_path=f"data/seed/documents/{source.name}",
                    page_count=page_count,
                    is_current=True,
                )
                session.add(document)
            await session.flush()
            for chunk, vector in zip(chunks, vectors, strict=True):
                session.add(
                    DocumentChunk(
                        document_id=document.id,
                        chunk_index=chunk.index,
                        section_ref=chunk.section_ref,
                        page_start=chunk.page_start,
                        page_end=chunk.page_end,
                        content=chunk.content,
                        embedding=vector,
                        embedding_model=embedder.model_name,
                        token_count=chunk.word_count,
                    )
                )
        report.documents_ingested.append(code)


async def _document_uses_model(db: Database, document_id: object, model_name: str) -> bool:
    async with db.session() as session:
        found = (
            await session.execute(
                select(DocumentChunk.id)
                .where(
                    DocumentChunk.document_id == document_id,
                    DocumentChunk.embedding_model == model_name,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
    return found is not None


async def _seed_access(session: AsyncSession, settings: Settings, report: SeedReport) -> None:
    password = settings.seed_agent_password.get_secret_value()
    if password:
        existing = (
            await session.execute(
                select(AgentUser).where(AgentUser.email == settings.seed_agent_email.lower())
            )
        ).scalar_one_or_none()
        if existing is None:
            session.add(
                AgentUser(
                    email=settings.seed_agent_email.lower(),
                    display_name="Demo Agent",
                    role="admin",
                    password_hash=hash_password(password),
                )
            )
            report.agent_created = True
    for name, secret, scopes in (
        ("local-mcp", settings.mcp_dev_token.get_secret_value(), ["mcp:read", "mcp:write"]),
        ("local-n8n", settings.n8n_integration_token.get_secret_value(), ["integrations"]),
    ):
        if not secret:
            continue
        await session.execute(
            insert(ApiToken)
            .values(
                name=name,
                token_hash=hash_api_token(secret),
                prefix=secret[:8],
                scopes=scopes,
                created_by="seed",
            )
            .on_conflict_do_update(
                index_elements=["name"],
                set_={
                    "token_hash": hash_api_token(secret),
                    "prefix": secret[:8],
                    "scopes": scopes,
                    # A revoked token stays revoked: re-seeding must not restore access.
                },
            )
        )
        report.tokens.append(name)


async def run_seed(
    db: Database,
    settings: Settings,
    embedder: Embedder,
    *,
    seed_dir: Path | None = None,
    reindex: bool = False,
) -> SeedReport:
    root = seed_dir or default_seed_dir()
    catalog = load_catalog(root)
    report = SeedReport()
    async with db.transaction() as session:
        await _seed_catalog(session, catalog, report)
        await _seed_access(session, settings, report)
    output_dir = Path(settings.documents_output_dir)
    await ingest_documents(db, embedder, root / "documents", output_dir, report, reindex=reindex)
    return report
