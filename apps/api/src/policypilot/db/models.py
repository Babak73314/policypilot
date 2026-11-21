"""SQLAlchemy models for the `app` schema (docs/design/data-model.md).

Enumerations are text columns with CHECK constraints. Money is bigint minor units plus currency.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, ClassVar

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.schema import Computed

SCHEMA = "app"
EMBEDDING_DIMENSIONS = 384

NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(schema=SCHEMA, naming_convention=NAMING)
    type_annotation_map: ClassVar[dict[Any, Any]] = {
        dict[str, Any]: JSONB,
        list[str]: ARRAY(Text),
    }


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )


def _created() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


def _enum_check(column: str, values: tuple[str, ...], name: str | None = None) -> CheckConstraint:
    quoted = ", ".join(f"'{v}'" for v in values)
    return CheckConstraint(f"{column} IN ({quoted})", name=name or f"{column}_valid")


# ============================================================================ catalog


class Carrier(Base):
    __tablename__ = "carriers"
    id: Mapped[uuid.UUID] = _uuid_pk()
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(Text)
    financial_strength: Mapped[str] = mapped_column(Text)
    website: Mapped[str] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    created_at: Mapped[datetime] = _created()


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (
        _enum_check("line", ("life", "health", "auto")),
        _enum_check("billing_period", ("monthly", "annual")),
    )
    id: Mapped[uuid.UUID] = _uuid_pk()
    carrier_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("carriers.id"))
    code: Mapped[str] = mapped_column(String(64), unique=True)
    line: Mapped[str] = mapped_column(Text)
    currency: Mapped[str] = mapped_column(String(3))
    billing_period: Mapped[str] = mapped_column(Text)
    attributes: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))
    active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    created_at: Mapped[datetime] = _created()


class ProductText(Base):
    __tablename__ = "product_texts"
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), primary_key=True
    )
    locale: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text)
    highlights: Mapped[list[str]] = mapped_column(server_default=text("'{}'"))
    exclusions_summary: Mapped[str] = mapped_column(Text)


class RateTable(Base):
    __tablename__ = "rate_tables"
    __table_args__ = (
        UniqueConstraint("product_id", "version", name="uq_rate_tables_product_version"),
        UniqueConstraint("product_id", "effective_from", name="uq_rate_tables_product_from"),
        CheckConstraint(
            "effective_to IS NULL OR effective_to > effective_from", name="period_valid"
        ),
    )
    id: Mapped[uuid.UUID] = _uuid_pk()
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"))
    version: Mapped[str] = mapped_column(String(32))
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)
    factors: Mapped[dict[str, Any]]
    checksum: Mapped[str] = mapped_column(String(64))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()


class MessageTemplate(Base):
    __tablename__ = "message_templates"
    __table_args__ = (
        _enum_check("channel", ("web", "whatsapp")),
        CheckConstraint(
            "category IS NULL OR category IN ('utility', 'marketing', 'authentication')",
            name="category_valid",
        ),
    )
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    channel: Mapped[str] = mapped_column(String(16), primary_key=True)
    locale: Mapped[str] = mapped_column(String(16), primary_key=True)
    body: Mapped[str] = mapped_column(Text)
    params: Mapped[list[str]] = mapped_column(server_default=text("'{}'"))
    meta_template_name: Mapped[str | None] = mapped_column(Text)
    meta_language_code: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(Text)


class PolicyDocument(Base):
    __tablename__ = "policy_documents"
    __table_args__ = (
        UniqueConstraint("product_id", "locale", "sha256", name="uq_policy_documents_content"),
        Index(
            "uq_policy_documents_current",
            "product_id",
            "locale",
            unique=True,
            postgresql_where=text("is_current"),
        ),
    )
    id: Mapped[uuid.UUID] = _uuid_pk()
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"))
    locale: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(Text)
    version: Mapped[str] = mapped_column(String(32))
    sha256: Mapped[str] = mapped_column(String(64))
    source_path: Mapped[str] = mapped_column(Text)
    page_count: Mapped[int] = mapped_column(Integer)
    is_current: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    ingested_at: Mapped[datetime] = _created()


class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index", name="uq_document_chunks_position"),
        Index(
            "ix_document_chunks_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index("ix_document_chunks_search", "search", postgresql_using="gin"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("policy_documents.id", ondelete="CASCADE"), index=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer)
    section_ref: Mapped[str] = mapped_column(Text)
    page_start: Mapped[int] = mapped_column(Integer)
    page_end: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    search: Mapped[str] = mapped_column(
        TSVECTOR, Computed("to_tsvector('english', content)", persisted=True)
    )
    embedding: Mapped[list[float]] = mapped_column(VECTOR(EMBEDDING_DIMENSIONS))
    embedding_model: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int] = mapped_column(Integer)


# ============================================================================ customers & channels


class Customer(Base):
    __tablename__ = "customers"
    id: Mapped[uuid.UUID] = _uuid_pk()
    display_name: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    phone_e164: Mapped[str | None] = mapped_column(Text)
    locale: Mapped[str] = mapped_column(String(16), server_default=text("'en'"))
    region: Mapped[str | None] = mapped_column(Text)
    marketing_opt_in: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    whatsapp_opted_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()
    erased_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ChannelIdentity(Base):
    __tablename__ = "channel_identities"
    __table_args__ = (
        UniqueConstraint("channel", "external_id", name="uq_channel_identities_external"),
        _enum_check("channel", ("web", "whatsapp")),
    )
    id: Mapped[uuid.UUID] = _uuid_pk()
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id"), index=True)
    channel: Mapped[str] = mapped_column(String(16))
    external_id: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()


CONVERSATION_STATUSES = ("active", "awaiting_review", "human_takeover", "closed")


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        _enum_check("channel", ("web", "whatsapp")),
        _enum_check("status", CONVERSATION_STATUSES),
    )
    id: Mapped[uuid.UUID] = _uuid_pk()
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id"), index=True)
    channel: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(Text, server_default=text("'active'"))
    locale: Mapped[str] = mapped_column(String(16), server_default=text("'en'"))
    last_customer_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_activity_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    ai_disclosed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    token_version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    created_at: Mapped[datetime] = _created()
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    close_reason: Mapped[str | None] = mapped_column(Text)


class Turn(Base):
    __tablename__ = "turns"
    __table_args__ = (
        UniqueConstraint("conversation_id", "client_message_id", name="uq_turns_client_message"),
        _enum_check("status", ("running", "completed", "failed")),
    )
    id: Mapped[uuid.UUID] = _uuid_pk()
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE")
    )
    client_message_id: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(Text, server_default=text("'running'"))
    customer_message_id: Mapped[int | None] = mapped_column(BigInteger)
    assistant_message_id: Mapped[int | None] = mapped_column(BigInteger)
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    error_code: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = _created()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        _enum_check("role", ("customer", "assistant", "agent", "system")),
        Index("ix_messages_conversation_id_id", "conversation_id", "id"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE")
    )
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    redactions: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", server_default=text("'{}'::jsonb")
    )
    turn_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = _created()


class InboundEvent(Base):
    __tablename__ = "inbound_events"
    __table_args__ = (
        UniqueConstraint("channel", "provider_message_id", name="uq_inbound_events_provider_id"),
        _enum_check("kind", ("message", "button", "status", "unsupported")),
        _enum_check("status", ("pending", "processing", "done", "failed")),
        Index("ix_inbound_events_drain", "conversation_id", "status", "received_at", "id"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    channel: Mapped[str] = mapped_column(String(16))
    provider_message_id: Mapped[str] = mapped_column(Text)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(String(16))
    payload: Mapped[dict[str, Any]]
    received_at: Mapped[datetime] = _created()
    status: Mapped[str] = mapped_column(Text, server_default=text("'pending'"))
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    last_error: Mapped[str | None] = mapped_column(Text)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OutboundMessage(Base):
    __tablename__ = "outbound_messages"
    __table_args__ = (
        _enum_check("kind", ("text", "template", "interactive")),
        _enum_check("status", ("pending", "sent", "delivered", "read", "failed")),
        Index("ix_outbound_messages_pending", "status", "next_attempt_at"),
    )
    id: Mapped[uuid.UUID] = _uuid_pk()
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    channel: Mapped[str] = mapped_column(String(16))
    message_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("messages.id", ondelete="SET NULL")
    )
    kind: Mapped[str] = mapped_column(String(16))
    payload: Mapped[dict[str, Any]]
    status: Mapped[str] = mapped_column(Text, server_default=text("'pending'"))
    provider_message_id: Mapped[str | None] = mapped_column(Text, index=True)
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()


class SimulatorOutbox(Base):
    __tablename__ = "simulator_outbox"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE")
    )
    to_external_id: Mapped[str] = mapped_column(Text, index=True)
    payload: Mapped[dict[str, Any]]
    created_at: Mapped[datetime] = _created()


# ============================================================================ sales


class Quote(Base):
    __tablename__ = "quotes"
    __table_args__ = (
        _enum_check("source", ("assistant", "mcp", "agent")),
        _enum_check("billing_period", ("monthly", "annual")),
    )
    id: Mapped[uuid.UUID] = _uuid_pk()
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"), index=True
    )
    customer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("customers.id"))
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"))
    rate_table_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("rate_tables.id", ondelete="RESTRICT")
    )
    inputs: Mapped[dict[str, Any]]
    inputs_hash: Mapped[str] = mapped_column(String(64))
    premium_minor: Mapped[int] = mapped_column(BigInteger)
    monthly_equivalent_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
    billing_period: Mapped[str] = mapped_column(Text)
    breakdown: Mapped[dict[str, Any]]
    variants: Mapped[dict[str, Any]]
    engine_version: Mapped[str] = mapped_column(String(32))
    source: Mapped[str] = mapped_column(String(16))
    idempotency_key: Mapped[str | None] = mapped_column(String(128), unique=True)
    created_at: Mapped[datetime] = _created()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Lead(Base):
    __tablename__ = "leads"
    __table_args__ = (
        _enum_check("kind", ("application", "contact_request")),
        _enum_check("status", ("new", "contacted", "qualified", "won", "lost")),
    )
    id: Mapped[uuid.UUID] = _uuid_pk()
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id"), index=True)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL")
    )
    kind: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(Text, server_default=text("'new'"))
    line_interest: Mapped[list[str]] = mapped_column(server_default=text("'{}'"))
    profile: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))
    source: Mapped[str] = mapped_column(String(16))
    assigned_agent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agent_users.id"))
    next_follow_up_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    contact_consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    contact_consent_source: Mapped[str | None] = mapped_column(Text)
    consent_text_version: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


APPLICATION_STATUSES = (
    "draft",
    "pending_review",
    "changes_requested",
    "approved",
    "rejected",
    "withdrawn",
    "expired",
)


class Application(Base):
    __tablename__ = "applications"
    __table_args__ = (_enum_check("status", APPLICATION_STATUSES),)
    id: Mapped[uuid.UUID] = _uuid_pk()
    lead_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("leads.id"))
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id"), index=True)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL")
    )
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"))
    quote_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("quotes.id"))
    status: Mapped[str] = mapped_column(Text, server_default=text("'draft'"))
    current_snapshot_version: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ApplicationSnapshot(Base):
    __tablename__ = "application_snapshots"
    application_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), primary_key=True
    )
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    data: Mapped[dict[str, Any]]
    needs_profile: Mapped[dict[str, Any]]
    quote_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("quotes.id"))
    rate_table_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("rate_tables.id"))
    policy_document_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("policy_documents.id"))
    created_by: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()


# ============================================================================ review


REVIEW_STATUSES = ("open", "claimed", "resolving", "resolved")


class ReviewTask(Base):
    __tablename__ = "review_tasks"
    __table_args__ = (
        _enum_check("kind", ("application_review", "handoff")),
        _enum_check("status", REVIEW_STATUSES),
        Index(
            "uq_review_tasks_one_unresolved",
            "conversation_id",
            unique=True,
            postgresql_where=text("status IN ('open', 'claimed', 'resolving')"),
        ),
    )
    id: Mapped[uuid.UUID] = _uuid_pk()
    review_key: Mapped[str] = mapped_column(Text, unique=True)
    kind: Mapped[str] = mapped_column(String(32))
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE")
    )
    application_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("applications.id"))
    snapshot_version: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(Text, server_default=text("'open'"))
    priority: Mapped[int] = mapped_column(SmallInteger, server_default=text("0"))
    summary: Mapped[str] = mapped_column(Text, server_default=text("''"))
    payload: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))
    claimed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agent_users.id"))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    resolution: Mapped[str | None] = mapped_column(Text)
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agent_users.id"))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()


# ================================================================ people, access, limits


class AgentUser(Base):
    __tablename__ = "agent_users"
    __table_args__ = (
        _enum_check("role", ("agent", "admin")),
        Index("uq_agent_users_email_lower", text("lower(email)"), unique=True),
    )
    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(Text)
    display_name: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(16), server_default=text("'agent'"))
    password_hash: Mapped[str] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()


class ApiToken(Base):
    __tablename__ = "api_tokens"
    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(Text, unique=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    prefix: Mapped[str] = mapped_column(String(16))
    scopes: Mapped[list[str]]
    created_by: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()


class LlmUsageCounter(Base):
    __tablename__ = "llm_usage_counters"
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    calls: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    limit_value: Mapped[int] = mapped_column(Integer)


class IntegrationFollowUp(Base):
    __tablename__ = "integration_follow_ups"
    occurrence_id: Mapped[str] = mapped_column(Text, primary_key=True)
    lead_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"))
    channel: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()


# ============================================================================ events, audit, traces


class OutboxEvent(Base):
    __tablename__ = "outbox_events"
    __table_args__ = (
        Index(
            "ix_outbox_events_pending",
            "available_at",
            postgresql_where=text("delivered_at IS NULL AND dead_lettered_at IS NULL"),
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    event_type: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]]
    created_at: Mapped[datetime] = _created()
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    dead_lettered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (
        _enum_check("actor_type", ("system", "assistant", "customer", "agent", "integration")),
        Index("ix_audit_log_entity", "entity_type", "entity_id"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    occurred_at: Mapped[datetime] = _created()
    actor_type: Mapped[str] = mapped_column(String(16))
    actor_id: Mapped[str | None] = mapped_column(Text)
    action: Mapped[str] = mapped_column(Text)
    entity_type: Mapped[str] = mapped_column(Text)
    entity_id: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))


class AgentRun(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (_enum_check("status", ("ok", "error", "interrupted")),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    turn_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    channel: Mapped[str] = mapped_column(String(16))
    route: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16))
    models: Mapped[list[str]] = mapped_column(server_default=text("'{}'"))
    fallback_used: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    input_tokens: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    output_tokens: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    cache_read_tokens: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    cost_estimate_micros: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    guardrail_actions: Mapped[list[str]] = mapped_column(server_default=text("'{}'"))
    error: Mapped[str | None] = mapped_column(Text)


class AgentRunStep(Base):
    __tablename__ = "agent_run_steps"
    __table_args__ = (_enum_check("kind", ("node", "llm", "tool", "guardrail")),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True
    )
    seq: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int] = mapped_column(Integer)
    model: Mapped[str | None] = mapped_column(Text)
    provider: Mapped[str | None] = mapped_column(Text)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    summary: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))
