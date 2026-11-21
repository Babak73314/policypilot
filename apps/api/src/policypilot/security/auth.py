"""Tokens and passwords (ADR-0014)."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_ALGORITHM = "HS256"
_HASHER = PasswordHasher()
TOKEN_PREFIX = "pp_"  # noqa: S105 - a public prefix, not a secret


class InvalidTokenError(Exception):
    pass


@dataclass(frozen=True)
class CustomerClaims:
    conversation_id: uuid.UUID
    version: int


@dataclass(frozen=True)
class AgentClaims:
    agent_id: uuid.UUID
    role: Literal["agent", "admin"]


def _now() -> datetime:
    return datetime.now(UTC)


def issue_customer_token(
    secret: str, conversation_id: uuid.UUID, version: int, ttl_hours: int
) -> str:
    now = _now()
    payload = {
        "sub": str(conversation_id),
        "typ": "customer",
        "ver": version,
        "iat": now,
        "exp": now + timedelta(hours=ttl_hours),
    }
    return jwt.encode(payload, secret, algorithm=_ALGORITHM)


def verify_customer_token(secret: str, token: str) -> CustomerClaims:
    try:
        payload = jwt.decode(token, secret, algorithms=[_ALGORITHM], options={"require": ["exp"]})
    except jwt.PyJWTError as exc:
        raise InvalidTokenError(str(exc)) from exc
    if payload.get("typ") != "customer":
        raise InvalidTokenError("not a customer token")
    return CustomerClaims(conversation_id=uuid.UUID(payload["sub"]), version=int(payload["ver"]))


def issue_agent_token(
    secret: str, agent_id: uuid.UUID, role: str, ttl_minutes: int
) -> tuple[str, datetime]:
    now = _now()
    expires = now + timedelta(minutes=ttl_minutes)
    payload = {"sub": str(agent_id), "typ": "agent", "role": role, "iat": now, "exp": expires}
    return jwt.encode(payload, secret, algorithm=_ALGORITHM), expires


def verify_agent_token(secret: str, token: str) -> AgentClaims:
    try:
        payload = jwt.decode(token, secret, algorithms=[_ALGORITHM], options={"require": ["exp"]})
    except jwt.PyJWTError as exc:
        raise InvalidTokenError(str(exc)) from exc
    if payload.get("typ") != "agent" or payload.get("role") not in ("agent", "admin"):
        raise InvalidTokenError("not an agent token")
    return AgentClaims(agent_id=uuid.UUID(payload["sub"]), role=payload["role"])


def hash_password(password: str) -> str:
    return _HASHER.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _HASHER.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def new_api_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def hash_api_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
