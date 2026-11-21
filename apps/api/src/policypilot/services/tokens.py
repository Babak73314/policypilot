"""Opaque API tokens for MCP clients and n8n (ADR-0014). Only SHA-256 hashes are stored."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from policypilot.db.models import ApiToken
from policypilot.security.auth import hash_api_token, new_api_token

VALID_SCOPES = frozenset({"mcp:read", "mcp:write", "integrations"})


@dataclass(frozen=True)
class TokenPrincipal:
    token_id: str
    name: str
    scopes: frozenset[str]

    def has(self, scope: str) -> bool:
        return scope in self.scopes


async def create_token(
    session: AsyncSession, *, name: str, scopes: list[str], created_by: str
) -> str:
    unknown = set(scopes) - VALID_SCOPES
    if unknown:
        raise ValueError(f"unknown scopes: {sorted(unknown)}")
    secret = new_api_token()
    session.add(
        ApiToken(
            name=name,
            token_hash=hash_api_token(secret),
            prefix=secret[:8],
            scopes=sorted(set(scopes)),
            created_by=created_by,
        )
    )
    await session.flush()
    return secret


async def revoke_tokens(session: AsyncSession, prefix: str) -> int:
    result = await session.execute(
        update(ApiToken)
        .where(ApiToken.prefix == prefix[:8], ApiToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )
    return int(getattr(result, "rowcount", 0) or 0)


async def verify_token(session: AsyncSession, secret: str) -> TokenPrincipal | None:
    row = (
        await session.execute(select(ApiToken).where(ApiToken.token_hash == hash_api_token(secret)))
    ).scalar_one_or_none()
    now = datetime.now(UTC)
    if row is None or row.revoked_at is not None or (row.expires_at and row.expires_at <= now):
        return None
    row.last_used_at = now
    return TokenPrincipal(token_id=str(row.id), name=row.name, scopes=frozenset(row.scopes))
